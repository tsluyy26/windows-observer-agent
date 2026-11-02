"""锁定 SDK 的 Responses/Chat 统一转换；仅内存 HTTP。"""
import asyncio
import contextlib
from dataclasses import asdict, replace
import importlib.util
import json
import logging
import os
import unittest
from unittest.mock import patch

from test_llm import scene
from windows_observer_agent.llm_provider import MockLLMProvider
from windows_observer_agent.llm_planner import PLAN_SCHEMA, parse_plan
from windows_observer_agent.planning_input import build_prompt
from windows_observer_agent.providers import (ProviderConfig, ModelCapabilities, LLMRequest,
    LLMResponse, ProviderAccess, ModelFailure)
from windows_observer_agent.provider_adapters import OpenAIResponsesAdapter, ChatCompletionsAdapter


@unittest.skipUnless(importlib.util.find_spec('openai'), '需安装 llm extra')
class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx2
        self.httpx = httpx2
        self.original = httpx2.AsyncClient
        self.requests, self.clients = [], []
        self.prompt = build_prompt('新建一份空白记事本文档', scene())
        self.request = LLMRequest(**self.prompt, schema=PLAN_SCHEMA)
        self.reply = MockLLMProvider().generate(self.prompt)
        self.delay, self.status, self.finish = 0, 200, 'stop'
        self.tool = self.refusal = self.network_error = False
        self.entered = asyncio.Event()

    def config(self, adapter='openai_chat_completions', **overrides):
        values = dict(name='example', adapter=adapter, model='example-model',
            base_url='https://api.example.com/v1', api_key_env='EXAMPLE_MODEL_KEY',
            timeout=2, max_output_tokens=2048, extra_params={'temperature': 0.1},
            capabilities=ModelCapabilities(chat_completions_api=True, responses_api=True, structured_output=True))
        values.update(overrides)
        return ProviderConfig(**values)

    def adapter(self, config):
        access = ProviderAccess(enabled=True, share_confirmed=True, confirmed_endpoint=config.base_url,
                                allow_local_http=True)
        cls = ChatCompletionsAdapter if config.adapter == 'openai_chat_completions' else OpenAIResponsesAdapter
        return cls(config, access)

    async def handler(self, request):
        self.entered.set()
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.network_error:
            raise self.httpx.ConnectError('SECRET', request=request)
        if self.status != 200:
            return self.httpx.Response(self.status, json={'error': {'message': 'SECRET'}},
                headers={'Location': 'https://evil.example/steal'})
        if request.url.path.endswith('/responses'):
            return self.httpx.Response(200, json={'id': 'resp_fake', 'object': 'response', 'created_at': 1,
                'model': 'example-model', 'status': 'completed', 'error': None, 'incomplete_details': None,
                'output': [{'id': 'msg_fake', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': self.reply, 'annotations': []}]}],
                'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}})
        message = {'role': 'assistant', 'content': self.reply}
        if self.tool:
            message['tool_calls'] = [{'id': 'call_fake', 'type': 'function', 'function': {'name': 'Click', 'arguments': '{}'}}]
        if self.refusal:
            message['refusal'] = 'SECRET'
        return self.httpx.Response(200, json={'id': 'chat_fake', 'object': 'chat.completion', 'created': 1,
            'model': 'example-model', 'choices': [{'index': 0, 'finish_reason': self.finish, 'message': message}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}})

    @contextlib.contextmanager
    def memory(self):
        owner = self
        class MemoryClient(self.original):
            def __init__(self, **kwargs):
                owner.assertTrue(kwargs['verify'])
                owner.assertFalse(kwargs['trust_env'])
                owner.assertFalse(kwargs['follow_redirects'])
                super().__init__(transport=owner.httpx.MockTransport(owner.handler), **kwargs)
                owner.clients.append(self)
        with patch.dict(os.environ, {'EXAMPLE_MODEL_KEY': 'synthetic-only-key', 'OPENAI_BASE_URL': 'https://wrong.example'}), \
             patch('httpx2.AsyncClient', MemoryClient), patch('socket.socket.connect', side_effect=AssertionError('network')), \
             patch('subprocess.Popen', side_effect=AssertionError('process')):
            yield

    async def test_both_adapters_produce_identical_plan_structure(self):
        plans = []
        for kind in ('openai_responses', 'openai_chat_completions'):
            with self.memory():
                result = await self.adapter(self.config(kind)).complete_async(self.request)
            self.assertIsInstance(result, LLMResponse)
            self.assertEqual(result.usage, {'input_tokens': 10, 'output_tokens': 20})
            plans.append(asdict(parse_plan(result.text, self.prompt, planner=result.provider)))
        self.assertEqual(plans[0], plans[1])
        self.assertFalse(plans[0]['executable'])
        self.assertEqual(plans[0]['execution_permission'], 'not_granted')
        self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_chat_request_structure_and_extra_parameters(self):
        with self.memory():
            await self.adapter(self.config()).complete_async(self.request)
        body = json.loads(self.requests[0].content)
        self.assertEqual(str(self.requests[0].url), 'https://api.example.com/v1/chat/completions')
        self.assertEqual(self.requests[0].headers['Authorization'], 'Bearer synthetic-only-key')
        self.assertEqual(body['max_tokens'], 2048)
        self.assertEqual(body['temperature'], 0.1)
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user'])
        self.assertEqual(body['response_format']['type'], 'json_schema')
        self.assertNotIn('tools', body)
        self.assertNotIn('tool_choice', body)
        self.assertFalse(body['stream'])
        self.assertFalse(body['store'])
        self.assertNotIn('synthetic-only-key', self.requests[0].content.decode())
        self.assertNotIn('项目说明', self.requests[0].content.decode())

    async def test_chat_json_mode_and_plain_json_are_explicit_capabilities(self):
        for json_mode in (True, False):
            config = self.config(capabilities=ModelCapabilities(chat_completions_api=True, json_mode=json_mode))
            with self.memory():
                await self.adapter(config).complete_async(self.request)
            body = json.loads(self.requests[-1].content)
            if json_mode:
                self.assertEqual(body['response_format'], {'type': 'json_object'})
            else:
                self.assertNotIn('response_format', body)
            self.assertIn('JSON', body['messages'][0]['content'])

    async def test_responses_uses_configured_budget_and_format(self):
        config = self.config('openai_responses', capabilities=ModelCapabilities(responses_api=True, json_mode=True))
        with self.memory():
            await self.adapter(config).complete_async(self.request)
        body = json.loads(self.requests[0].content)
        self.assertEqual(body['max_output_tokens'], 2048)
        self.assertEqual(body['text']['format'], {'type': 'json_object'})
        self.assertEqual(body['tools'], [])
        self.assertEqual(body['tool_choice'], 'none')

    async def test_local_http_is_used_only_with_runtime_authorization(self):
        config = self.config(base_url='http://localhost:8080/v1')
        adapter = self.adapter(config)
        with self.memory():
            await adapter.complete_async(self.request)
        self.assertEqual(str(self.requests[0].url), 'http://localhost:8080/v1/chat/completions')

    async def test_redirect_auth_and_server_failures_do_not_retry_or_log_secrets(self):
        for status in (301, 307, 401, 403, 429, 500):
            self.status = status
            self.requests.clear()
            with self.subTest(status=status), self.assertNoLogs(level='DEBUG'), self.memory(), self.assertRaises(ModelFailure) as raised:
                await self.adapter(self.config()).complete_async(self.request)
            self.assertNotIn('SECRET', str(raised.exception))
            self.assertNotIn('synthetic-only-key', repr(raised.exception))
            self.assertEqual(len(self.requests), 1)
            self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_timeout_connection_failure_and_cancel_close_resources(self):
        previous = logging.root.manager.disable
        self.delay = 10
        with self.memory(), self.assertRaises(ModelFailure) as raised:
            await self.adapter(self.config(timeout=0.02)).complete_async(self.request)
        self.assertEqual(raised.exception.code, 'model_timeout')
        self.delay, self.network_error = 0, True
        with self.memory(), self.assertRaises(ModelFailure):
            await self.adapter(self.config()).complete_async(self.request)
        self.delay, self.network_error = 10, False
        self.entered.clear()
        with self.memory():
            task = asyncio.create_task(self.adapter(self.config()).complete_async(self.request))
            await asyncio.wait_for(self.entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(all(c.is_closed for c in self.clients))
        self.assertEqual(logging.root.manager.disable, previous)

    async def test_chat_rejects_tools_refusal_incomplete_and_malformed_content(self):
        for field, value in [('tool', True), ('refusal', True), ('finish', 'length'), ('finish', 'tool_calls'), ('reply', None)]:
            original = getattr(self, field)
            setattr(self, field, value)
            with self.subTest(field=field, value=value), self.memory(), self.assertRaises(ModelFailure):
                await self.adapter(self.config()).complete_async(self.request)
            setattr(self, field, original)
        self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_key_cannot_enter_model_input_or_response_text(self):
        from windows_observer_agent.providers import ModelPolicyError
        with self.memory(), self.assertRaises(ModelPolicyError):
            await self.adapter(self.config()).complete_async(replace(self.request, instructions='synthetic-only-key'))
        self.assertEqual(self.requests, [])
        self.reply = 'synthetic-only-key'
        with self.memory(), self.assertRaises(ModelFailure) as raised:
            await self.adapter(self.config()).complete_async(self.request)
        self.assertNotIn('synthetic-only-key', str(raised.exception))

    async def test_overlapping_requests_keep_logs_suppressed_until_last_exit(self):
        first_ready, second_ready = asyncio.Event(), asyncio.Event()
        finish_first, finish_second = asyncio.Event(), asyncio.Event()
        first, second = self.adapter(self.config()), self.adapter(self.config())
        async def request_one(request):
            first_ready.set()
            await finish_first.wait()
            return LLMResponse(self.reply, 'example', 'example-model')
        async def request_two(request):
            second_ready.set()
            await finish_second.wait()
            return LLMResponse(self.reply, 'example', 'example-model')
        first._request, second._request = request_one, request_two
        previous = logging.root.manager.disable
        try:
            with self.memory(), self.assertNoLogs(level='DEBUG'):
                one = asyncio.create_task(first.complete_async(self.request))
                await asyncio.wait_for(first_ready.wait(), 3)
                two = asyncio.create_task(second.complete_async(self.request))
                await asyncio.wait_for(second_ready.wait(), 3)
                finish_first.set()
                await one
                try:
                    self.assertGreaterEqual(logging.root.manager.disable, logging.CRITICAL)
                    logging.warning('synthetic-only-key')
                finally:
                    finish_second.set()
                    await two
            self.assertEqual(logging.root.manager.disable, previous)
        finally:
            logging.disable(previous)


if __name__ == '__main__':
    unittest.main()
