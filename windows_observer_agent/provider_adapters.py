"""将各 SDK 响应转换为 LLMResponse；没有工具调度、端点探测或服务启动。"""
import asyncio
from copy import deepcopy
from importlib.metadata import version
import json
import os

from .providers import (LLMResponse, ModelFailure, ProviderAccess,
                        validate_base_url, validate_external_request, suppress_model_logs)


class MockAdapter:
    def __init__(self, config, access=None):
        self.config = config

    def complete(self, request):
        from .llm_provider import MockLLMProvider
        request.validate()
        text = MockLLMProvider().generate(request.as_prompt())
        return LLMResponse(text, self.config.name, self.config.model)


class _SDKAdapter:
    route = ''

    def __init__(self, config, access=None):
        self.config = deepcopy(config)
        self.access = access if access is not None else ProviderAccess()

    async def _guard_request(self, request):
        actual = validate_base_url(str(request.url))
        expected = validate_base_url(self.config.base_url.rstrip('/') + self.route)
        def identity(parsed):
            return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), parsed.path
        if identity(actual) != identity(expected) or request.method != 'POST':
            raise ModelFailure('model_endpoint_rejected')

    async def _guard_response(self, response):
        if response.status_code in (401, 403):
            raise ModelFailure('model_authentication')
        if 300 <= response.status_code < 400:
            raise ModelFailure('model_redirect_rejected')

    def complete(self, request):
        validate_external_request(self.config, self.access, request)  # Windows socketpair 前检查。
        return asyncio.run(self.complete_async(request))

    async def complete_async(self, request):
        validate_external_request(self.config, self.access, request)
        with suppress_model_logs():
            return await self._request(request)

    async def _request(self, request):
        try:
            import openai
            import httpx2
            if version('openai') != '3.42.0' or version('httpx2') != '2.16.0':
                raise ModelFailure('model_sdk_version')
            async with asyncio.timeout(self.config.timeout):
                async with httpx2.AsyncClient(trust_env=False, follow_redirects=False, verify=True,
                    timeout=httpx2.Timeout(self.config.timeout, connect=min(5, self.config.timeout)),
                    event_hooks={'request': [self._guard_request], 'response': [self._guard_response]}) as http:
                    async with openai.AsyncOpenAI(api_key=os.environ[self.config.api_key_env],
                        base_url=self.config.base_url, organization='', project='', max_retries=0,
                        timeout=self.config.timeout, http_client=http) as client:
                        return await self._call(client, request)
        except ModelFailure:
            raise
        except ImportError:
            raise ModelFailure('model_sdk_missing') from None
        except TimeoutError:
            raise ModelFailure('model_timeout') from None
        except Exception as exc:
            code = 'model_timeout' if type(exc).__name__ == 'APITimeoutError' else 'model_failure'
            raise ModelFailure(code) from None

    def _instructions(self, request):
        # 无服务端 schema 能力也保留严格本地 Parser；不自动切换到别的接口。
        return request.instructions + '\n仅输出 JSON，必须符合此 Schema：\n' + json.dumps(request.schema, ensure_ascii=False)

    def _result(self, text, usage=None):
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 65536:
            raise ModelFailure('model_invalid_response')
        if os.environ[self.config.api_key_env] in text:
            raise ModelFailure('model_secret_rejected')
        counters = {}
        for key, aliases in {'input_tokens': ('input_tokens', 'prompt_tokens'),
                             'output_tokens': ('output_tokens', 'completion_tokens')}.items():
            for alias in aliases:
                value = getattr(usage, alias, None)
                if type(value) is int and value >= 0:
                    counters[key] = value
                    break
        return LLMResponse(text, self.config.name, self.config.model, usage=counters)


class OpenAIResponsesAdapter(_SDKAdapter):
    route = '/responses'

    async def _call(self, client, request):
        caps = self.config.capabilities
        if not caps.responses_api:
            raise ModelFailure('model_capability')
        fmt = {'type': 'text'}
        if caps.structured_output:
            fmt = {'type': 'json_schema', 'name': 'review_plan', 'strict': True, 'schema': request.schema}
        elif caps.json_mode:
            fmt = {'type': 'json_object'}
        response = await client.responses.create(model=self.config.model,
            instructions=self._instructions(request), input=json.dumps(request.data, ensure_ascii=False),
            text={'format': fmt}, tools=[], tool_choice='none', store=False, stream=False,
            max_output_tokens=self.config.max_output_tokens, extra_body=deepcopy(self.config.extra_params))
        if response.status != 'completed' or response.error or response.incomplete_details:
            raise ModelFailure('model_incomplete')
        messages = [item for item in response.output if item.type == 'message']
        if (not messages or any(item.type not in ('message', 'reasoning') for item in response.output)
            or any(not item.content or any(c.type != 'output_text' for c in item.content) for item in messages)):
            raise ModelFailure('model_nontext_output')
        return self._result(response.output_text, response.usage)


class ChatCompletionsAdapter(_SDKAdapter):
    route = '/chat/completions'

    async def _call(self, client, request):
        caps = self.config.capabilities
        if not caps.chat_completions_api:
            raise ModelFailure('model_capability')
        options = {}
        if caps.structured_output:
            options['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': 'review_plan', 'strict': True, 'schema': request.schema}}
        elif caps.json_mode:
            options['response_format'] = {'type': 'json_object'}
        # 不发送 tools/tool_choice 等可选扩展，提高兼容性；仍拒绝服务端任何工具输出。
        response = await client.chat.completions.create(model=self.config.model,
            messages=[{'role': 'system', 'content': self._instructions(request)},
                      {'role': 'user', 'content': json.dumps(request.data, ensure_ascii=False)}],
            max_tokens=self.config.max_output_tokens, stream=False, store=False,
            extra_body=deepcopy(self.config.extra_params), **options)
        if len(response.choices) != 1:
            raise ModelFailure('model_invalid_response')
        choice = response.choices[0]
        message = choice.message
        if choice.finish_reason != 'stop':
            raise ModelFailure('model_incomplete')
        if message.tool_calls or message.function_call or message.refusal:
            raise ModelFailure('model_nontext_output')
        return self._result(message.content, response.usage)
