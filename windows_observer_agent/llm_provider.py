"""v0.4 兼容入口；新的统一 Provider 契约位于 providers.py。"""
import json

from .providers import (LLMProvider, LLMRequest, LLMResponse, ModelFailure, ModelPolicyError,
                        ModelCapabilities, ProviderConfig, ProviderAccess, validate_access)


class MockLLMProvider:
    name = 'mock-llm'

    def __init__(self, response=None):
        self.response = response

    def complete(self, request):
        request.validate()
        return LLMResponse(self.generate(request.as_prompt()), self.name, 'mock-model')

    def generate(self, prompt):
        if self.response is not None:
            return self.response
        data = prompt['data']
        ambiguous = data['goal_requires_confirmation'] or not data['observation_success']
        steps = []
        if not ambiguous and data['elements']:
            match = next(((k, v) for k, v in data['elements'].items()
                          if v['kind'] == 'control' and v['label'] != 'redacted'
                          and any(word in v['label'] for word in data['goal'].split())), None)
            alias, value = match or next(iter(data['elements'].items()))
            steps.append({'operation': 'review_control' if value['kind'] == 'control' else 'review_window',
                          'action': '审查目标：' + data['goal'] + '；确认候选界面元素 ' + alias,
                          'preconditions': ['用户确认脱敏目标含义，重新观察并核实候选身份'],
                          'elements': [alias], 'evidence': [value['evidence']],
                          'verification': '用户检查候选身份及目标是否一致；识别不代表可操作',
                          'stop_conditions': ['目标不一致、观察不足或验证失败时停止']})
        steps.append({'operation': 'ask_user', 'action': '请用户审查目标含义和条件计划，补充缺失信息',
                      'preconditions': ['尚未执行任何操作'], 'elements': [], 'evidence': [],
                      'verification': '用户明确确认目标及下一步所需信息',
                      'stop_conditions': ['含义不明时停止，任务结果保持未知']})
        return json.dumps({'goal_understanding': '供审查的目标：' + data['goal'],
                           'planning_status': 'needs_confirmation' if ambiguous or not data['elements'] else 'success',
                           'executable': False, 'steps': steps,
                           'uncertainties': data['uncertainties'],
                           'confirmations': ['请确认缩减后的任务词表达了原始目标']}, ensure_ascii=False)


class OpenAIProvider:
    """保留 v0.4 构造与文本返回 API；内部使用 Responses Adapter 和统一响应。"""
    name = 'openai'
    SDK_VERSION = '3.42.0'
    BASE_URL = 'https://api.openai.com/v1'

    def __init__(self, model: str, *, enabled=False, share_confirmed=False, timeout=30.0):
        self.model, self.timeout = model, timeout
        self.enabled, self.share_confirmed = enabled is True, share_confirmed is True
        self.config = ProviderConfig(self.name, 'openai_responses', model, self.BASE_URL,
            timeout=timeout, capabilities=ModelCapabilities(responses_api=True, structured_output=True))
        # 旧 API 在构造时即校验超时，保留原有行为；模型/授权在 validate 检查。
        import math
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ModelPolicyError('模型超时须为 0 到 120 秒之间的有限正数')

    def validate(self):
        validate_access(self.config, self._access())

    def _access(self):
        return ProviderAccess(enabled=self.enabled, share_confirmed=self.share_confirmed)

    def _adapter(self):
        from .provider_adapters import OpenAIResponsesAdapter
        return OpenAIResponsesAdapter(self.config, self._access())

    def complete(self, request):
        return self._adapter().complete(request)

    def generate(self, prompt):
        from .llm_planner import PLAN_SCHEMA
        self.validate()
        return self.complete(LLMRequest(**prompt, schema=PLAN_SCHEMA)).text

    async def generate_async(self, prompt):
        from .llm_planner import PLAN_SCHEMA
        self.validate()
        result = await self._adapter().complete_async(LLMRequest(**prompt, schema=PLAN_SCHEMA))
        return result.text
