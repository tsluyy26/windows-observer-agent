"""厂商无关的模型契约、纯本地配置与显式运行时授权。此模块不导入 SDK。"""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
import json
import logging
import math
import os
import re
import tomllib
from threading import Lock
from typing import Callable, Protocol
from urllib.parse import urlsplit

_log_lock = Lock()
_log_users = 0
_log_previous = 0


@contextmanager
def suppress_model_logs():
    """嵌套/重叠调用共用计数，最后一个退出时才恢复全局日志级别。"""
    global _log_users, _log_previous
    with _log_lock:
        if _log_users == 0:
            _log_previous = logging.root.manager.disable
            logging.disable(max(_log_previous, logging.CRITICAL))
        _log_users += 1
    try:
        yield
    finally:
        with _log_lock:
            _log_users -= 1
            if _log_users == 0:
                logging.disable(_log_previous)


class ModelPolicyError(ValueError):
    pass


class ModelFailure(ValueError):
    def __init__(self, code='model_failure'):
        allowed = ('model_failure', 'model_timeout', 'model_authentication', 'model_redirect_rejected',
                   'model_sdk_version', 'model_sdk_missing', 'model_incomplete', 'model_nontext_output',
                   'model_invalid_response', 'model_capability', 'model_endpoint_rejected', 'model_secret_rejected')
        self.code = code if code in allowed else 'model_failure'
        super().__init__(self.code)


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value)


def validate_base_url(url):
    """仅验证地址，不解析 DNS、不建立连接；HTTP 授权另由运行时检查。"""
    try:
        parsed = urlsplit(url)
        valid = (isinstance(url, str) and url.isascii() and parsed.scheme in ('https', 'http')
                 and parsed.hostname and parsed.hostname != '0.0.0.0'
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and parsed.path.startswith('/')
                 and (parsed.port is None or 1 <= parsed.port <= 65535)
                 and not any(c.isspace() or ord(c) < 32 for c in url)
                 and not any(c in url for c in ('\\', '%', '?', '#'))
                 and (parsed.scheme == 'https' or parsed.hostname in ('localhost', '127.0.0.1'))
                 and re.fullmatch(r'[A-Za-z0-9.-]+', parsed.hostname)
                 and not any(part in ('.', '..') for part in parsed.path.split('/')))
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ModelPolicyError('模型地址无效：默认 HTTPS；HTTP 仅 localhost/127.0.0.1，禁止凭据/query/fragment')
    return parsed


@dataclass(frozen=True)
class ModelCapabilities:
    text_input: bool = True
    image_input: bool = False
    structured_output: bool = False
    json_mode: bool = False
    tool_calling: bool = False
    streaming: bool = False
    responses_api: bool = False
    chat_completions_api: bool = False

    def validate(self):
        if any(type(getattr(self, f.name)) is not bool for f in fields(self)):
            raise ModelPolicyError('能力声明必须为布尔值')


# 额外参数可包含兼容服务的扩展 JSON，但不能覆盖调用身份、认证或安全边界。
RESERVED = frozenset({'api_key', 'authorization', 'headers', 'extra_headers', 'base_url', 'model',
    'messages', 'input', 'instructions', 'tools', 'tool_choice', 'functions', 'function_call',
    'stream', 'stream_options', 'store', 'max_tokens', 'max_completion_tokens', 'max_output_tokens',
    'response_format', 'text', 'timeout', 'verify', 'proxy', 'extra_body', 'extra_query', 'modalities', 'audio',
    'web_search_options', 'background', 'conversation', 'previous_response_id', 'n',
    'access_programs', 'prompt', 'context_management', 'parallel_tool_calls', 'tool_resources'})


def _validate_extra(value, depth=0):
    if depth > 6:
        raise ModelPolicyError('额外参数嵌套过深')
    if type(value) is dict:
        for key, item in value.items():
            if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', key)
                or key.casefold() in RESERVED or re.search(r'(?i)secret|password|credential|auth|(?:^|_)token(?:$|_)|(?:^|_)key(?:$|_)', key)):
                raise ModelPolicyError('额外参数不得包含认证信息或覆盖安全/调用字段')
            _validate_extra(item, depth + 1)
    elif type(value) is list:
        if len(value) > 100:
            raise ModelPolicyError('额外参数数组过长')
        for item in value:
            _validate_extra(item, depth + 1)
    elif value is None or type(value) in (bool, int):
        pass
    elif type(value) is float and math.isfinite(value):
        pass
    elif type(value) is str and len(value) <= 1000:
        pass
    else:
        raise ModelPolicyError('额外参数必须是有界的有限 JSON 值')


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    adapter: str
    model: str
    base_url: str = field(default='', repr=False)
    api_key_env: str = 'WOA_LLM_API_KEY'
    timeout: float = 30.0
    max_output_tokens: int = 4000
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    extra_params: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        object.__setattr__(self, 'extra_params', deepcopy(self.extra_params))

    def validate(self):
        if not _identifier(self.name) or not _identifier(self.adapter):
            raise ModelPolicyError('Provider 和 Adapter 名称必须为简单标识，不允许模块导入路径')
        if not isinstance(self.model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', self.model):
            raise ModelPolicyError('须明确指定有效模型标识')
        if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or not 0 < self.timeout <= 120:
            raise ModelPolicyError('模型超时须为 0 到 120 秒之间的有限正数')
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 32000:
            raise ModelPolicyError('max_output_tokens 须在 1 到 32000 之间')
        if not isinstance(self.capabilities, ModelCapabilities):
            raise ModelPolicyError('缺少有效模型能力声明')
        self.capabilities.validate()
        if not self.capabilities.text_input:
            raise ModelPolicyError('本阶段规划必须支持文本输入')
        if self.adapter == 'openai_responses' and not self.capabilities.responses_api:
            raise ModelPolicyError('所选配置未声明 Responses API 能力')
        if self.adapter == 'openai_chat_completions' and not self.capabilities.chat_completions_api:
            raise ModelPolicyError('所选配置未声明 Chat Completions API 能力')
        if self.base_url:
            validate_base_url(self.base_url)
        elif self.adapter in ('openai_responses', 'openai_chat_completions'):
            raise ModelPolicyError('SDK Adapter 必须明确配置 base_url')
        if not isinstance(self.api_key_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', self.api_key_env):
            raise ModelPolicyError('密钥配置只能是环境变量名称')
        if type(self.extra_params) is not dict:
            raise ModelPolicyError('extra_params 必须是对象')
        _validate_extra(self.extra_params)
        if len(json.dumps(self.extra_params)) > 16000:
            raise ModelPolicyError('额外参数过大')


@dataclass(frozen=True)
class ProviderAccess:
    enabled: bool = False
    share_confirmed: bool = False
    confirmed_endpoint: str | None = field(default=None, repr=False)
    allow_local_http: bool = False


def validate_access(config, access):
    config.validate()
    if access.enabled is not True or access.share_confirmed is not True:
        raise ModelPolicyError('外部模型默认关闭；须显式启用并确认数据共享')
    parsed = validate_base_url(config.base_url)
    if config.base_url.rstrip('/') != 'https://api.openai.com/v1':
        if access.confirmed_endpoint != config.base_url:
            raise ModelPolicyError('自定义服务地址须用 --confirm-provider-endpoint 明确确认完整地址')
    if parsed.scheme == 'http' and access.allow_local_http is not True:
        raise ModelPolicyError('本地 HTTP 须显式 --allow-local-http 授权')
    key = os.environ.get(config.api_key_env, '')
    if not key or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
        raise ModelPolicyError('缺少有效模型密钥环境变量')
    if key in json.dumps([config.name, config.model, config.base_url, config.extra_params], ensure_ascii=False):
        raise ModelPolicyError('密钥不得出现在额外请求参数中')


@dataclass(frozen=True)
class LLMRequest:
    instructions: str = field(repr=False)
    data: dict = field(repr=False)
    schema: dict = field(repr=False)
    images: tuple = field(default=(), repr=False)
    tool_calling: bool = False
    streaming: bool = False

    def __post_init__(self):
        # Adapter 可以构造请求，但不能回写 Planner 留作校验依据的原观察。
        object.__setattr__(self, 'data', deepcopy(self.data))
        object.__setattr__(self, 'schema', deepcopy(self.schema))

    def as_prompt(self):
        return {'instructions': self.instructions, 'data': deepcopy(self.data)}

    def validate(self):
        if self.images or self.tool_calling is not False or self.streaming is not False:
            raise ModelPolicyError('本阶段只允许文本规划，不启用图片、工具或流式请求')
        if not isinstance(self.instructions, str) or type(self.data) is not dict or type(self.schema) is not dict:
            raise ModelPolicyError('模型请求结构无效')


@dataclass(frozen=True)
class LLMResponse:
    text: str = field(repr=False)
    provider: str
    model: str = ''
    finish_reason: str = 'completed'
    usage: dict = field(default_factory=dict, repr=False)


def validate_external_request(config, access, request):
    validate_access(config, access)
    request.validate()
    try:
        content = json.dumps([request.as_prompt(), request.schema], ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ModelPolicyError('模型请求必须是有效 JSON 数据') from None
    if os.environ[config.api_key_env] in content:
        raise ModelPolicyError('密钥不得进入模型输入')


class LLMProvider(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


class ProviderRegistry:
    """只注册受信 Python factory；配置不能导入模块。自定义 Python 代码不是沙箱。"""
    def __init__(self):
        self._entries: dict[str, tuple[Callable, bool]] = {}

    def register(self, adapter, factory, *, external=True):
        if not _identifier(adapter) or adapter in self._entries or not callable(factory) or type(external) is not bool:
            raise ModelPolicyError('Adapter 注册无效或重复')
        self._entries[adapter] = (factory, external)

    def entry(self, adapter):
        if adapter not in self._entries:
            raise ModelPolicyError('Adapter 未在受信代码中注册')
        return self._entries[adapter]


def default_registry():
    from .provider_adapters import MockAdapter, OpenAIResponsesAdapter, ChatCompletionsAdapter
    registry = ProviderRegistry()
    registry.register('mock', MockAdapter, external=False)
    registry.register('openai_responses', OpenAIResponsesAdapter)
    registry.register('openai_chat_completions', ChatCompletionsAdapter)
    return registry


class ProviderManager:
    def __init__(self, configs, selected, *, registry=None, access=None):
        self._configs = deepcopy(configs)
        self.registry = registry if registry is not None else default_registry()
        self.access = access if access is not None else ProviderAccess()
        self.select(selected)

    @property
    def name(self):
        return self.config.name

    def select(self, provider, *, model=None, capabilities=None):
        if provider not in self._configs:
            raise ModelPolicyError('所选 Provider 不存在')
        config = self._configs[provider]
        if not isinstance(config, ProviderConfig) or config.name != provider:
            raise ModelPolicyError('Provider 配置名称不一致')
        if model is not None and model != config.model:
            if capabilities is None:
                raise ModelPolicyError('更换模型须同时明确其能力声明，或选择另一命名配置')
            config = replace(config, model=model, capabilities=capabilities)
        config.validate()
        self.registry.entry(config.adapter)
        self.config = config  # 验证全部通过才变更选择；失败不自动换模型。

    def validate(self):
        self.config.validate()
        _, external = self.registry.entry(self.config.adapter)
        if external:
            validate_access(self.config, self.access)

    def complete(self, request):
        self.validate()  # factory 调用前先验证授权与密钥。
        request.validate()
        factory, external = self.registry.entry(self.config.adapter)
        if external:
            validate_external_request(self.config, self.access, request)
        try:
            with suppress_model_logs():
                result = factory(deepcopy(self.config), self.access).complete(deepcopy(request))
        except ModelFailure:
            raise
        except ModelPolicyError:
            raise ModelPolicyError('Adapter 请求被本地策略拒绝') from None
        except TimeoutError:
            raise ModelFailure('model_timeout') from None
        except Exception:
            raise ModelFailure('model_failure') from None
        if (not isinstance(result, LLMResponse) or result.provider != self.config.name
            or result.model != self.config.model or result.finish_reason != 'completed'
            or not isinstance(result.text, str) or len(result.text.encode('utf-8')) > 65536):
            raise ModelFailure('model_invalid_response')
        if external and os.environ[self.config.api_key_env] in result.text:
            raise ModelFailure('model_secret_rejected')
        return result


def load_provider_config(text):
    """解析 TOML 字符串，不读取密钥、不激活 Provider、不执行动态 import。"""
    try:
        root = tomllib.loads(text)
        if set(root) != {'llm'} or type(root['llm']) is not dict:
            raise ValueError()
        llm = root['llm']
        if set(llm) - {'provider', 'adapter', 'providers'} or type(llm.get('providers')) is not dict:
            raise ValueError()
        selected = llm['provider']
        configs = {}
        allowed = {f.name for f in fields(ProviderConfig)} - {'name'}
        for name, raw in llm['providers'].items():
            if type(raw) is not dict or set(raw) - allowed:
                raise ValueError()
            values = deepcopy(raw)
            if name == selected and 'adapter' not in values and 'adapter' in llm:
                values['adapter'] = llm['adapter']
            cap = values.pop('capabilities', {})
            if type(cap) is not dict:
                raise ValueError()
            config = ProviderConfig(name=name, capabilities=ModelCapabilities(**cap), **values)
            config.validate()
            configs[name] = config
        if selected not in configs or ('adapter' in llm and configs[selected].adapter != llm['adapter']):
            raise ValueError()
        return configs, selected
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        # TOML 解析错误可能附带原始配置行；不向终端暴露。
        raise ModelPolicyError('Provider 配置无效：检查字段、能力和地址，禁止明文密钥/动态模块/配置授权') from None


class LegacyProviderAdapter:
    """仅兼容 v0.4 的 generate(dict)->str；原始 SDK 对象不被接受。"""
    def __init__(self, provider):
        self.provider = provider
        self.name = getattr(provider, 'name', 'llm')

    def complete(self, request):
        request.validate()
        text = self.provider.generate(request.as_prompt())
        if not isinstance(text, str):
            raise ModelFailure('model_invalid_response')
        return LLMResponse(text, self.name)
