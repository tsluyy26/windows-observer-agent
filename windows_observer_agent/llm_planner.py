"""严格结构校验 + 观察证据校验；模型输出只能成为不可执行的文字计划。"""
from dataclasses import replace
import json
import re

from .models import Plan, PlanStep
from .planner import make_plan
from .planning_input import build_prompt, public_planning_scene
from .providers import (LLMRequest, LLMResponse, LegacyProviderAdapter, ModelFailure,
                        ModelPolicyError)

OPERATIONS = ['review_window', 'review_control', 'propose_activation', 'propose_ui_action', 'verify', 'ask_user']
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 800}


def array(items=TEXT, minimum=0, maximum=24):
    return {'type': 'array', 'items': items, 'minItems': minimum, 'maxItems': maximum}


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


PLAN_SCHEMA = obj({'goal_understanding': TEXT,
    'planning_status': {'type': 'string', 'enum': ['success', 'needs_confirmation']},
    'executable': {'type': 'boolean', 'enum': [False]},
    'steps': array(obj({'operation': {'type': 'string', 'enum': OPERATIONS}, 'action': TEXT,
        'preconditions': array(minimum=1), 'elements': array(maximum=12), 'evidence': array(maximum=12),
        'verification': TEXT, 'stop_conditions': array(minimum=1)}), minimum=1, maximum=12),
    'uncertainties': array(), 'confirmations': array(minimum=1)})


def _validate(value, schema):
    kind = schema['type']
    if kind == 'object':
        if type(value) is not dict or set(value) != set(schema['properties']):
            raise ValueError('字段不完整或含未知字段')
        for key, child in schema['properties'].items():
            _validate(value[key], child)
    elif kind == 'array':
        if type(value) is not list or not schema['minItems'] <= len(value) <= schema['maxItems']:
            raise ValueError('数组类型或长度无效')
        for item in value:
            _validate(item, schema['items'])
    elif kind == 'string':
        if type(value) is not str or not value.strip() or not schema.get('minLength', 1) <= len(value) <= schema.get('maxLength', 800):
            raise ValueError('文本字段无效')
    elif kind == 'boolean' and type(value) is not bool:
        raise ValueError('布尔字段无效')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('枚举无效')


def parse_plan(text, prompt, *, planner):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 65536:
        raise ValueError('输出类型或大小无效')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('重复 JSON 字段')
            result[key] = value
        return result
    payload = json.loads(text, object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非法 JSON 数字')))
    _validate(payload, PLAN_SCHEMA)
    validate_plan(payload, prompt)
    data = prompt['data']
    guard = ('仅供审查，执行前另行授权；重新观察并确认目标身份及可用性',)
    stops = ('观察改变、控制异常、目标不明或验证失败时停止，交由用户确认',)
    steps = tuple(PlanStep(s['action'], guard + tuple(s['preconditions']), s['verification'],
                          stops + tuple(s['stop_conditions']), tuple(s['evidence']), s['operation'], tuple(s['elements']))
                  for s in payload['steps'])
    status = payload['planning_status']
    # 本地事实优先于模型对完整性或权限的叙述。
    if data['goal_requires_confirmation'] or not data['observation_success']:
        status = 'needs_confirmation'
    return Plan(data['goal'], 'conditional' if status == 'success' else 'needs_confirmation',
                guard, ('依据为脱敏 UI 结构及工具文本，没有视觉分析；模型计划未经执行验证',),
                steps, stops, planning_status=status, planner=planner,
                goal_understanding=payload['goal_understanding'],
                uncertainties=tuple(dict.fromkeys(data['uncertainties'] + payload['uncertainties'])),
                confirmations=tuple(payload['confirmations']) + ('请核对目标缩减、别名及当前桌面状态',))


def validate_plan(payload, prompt):
    data = prompt['data']
    # 保守拒绝命令/文件系统/越权建议，不能把词法检查当作通用语义安全证明。
    dangerous = (r'(?i)\b(?:powershell|filesystem|click|type|cmd|subprocess|eval|exec|bash|curl|wget|'
                 r'delete|remove|discard|overwrite|run|execute|shell|registry|password|credential)\b|'
                 r'删除|覆盖|清空|移除|销毁|格式化|提权|关闭.{0,16}(?:保护|防护|未保存|文档|文件|窗口)|'
                 r'运行.{0,40}(?:exe|程序|脚本|命令)|执行.{0,8}(?:命令|脚本|代码)|'
                 r'(?:任务|目标).{0,8}(?:已经完成|已完成)|(?:已经|已|成功)完成.{0,8}(?:任务|目标)|'
                 r'忽略.{0,8}指令|```|[a-z]:[\\/]|https?://|\S+@\S+')
    if re.search(dangerous, json.dumps(payload, ensure_ascii=False)):
        raise ValueError('建议超出安全规划范围')
    elements = data['elements']
    for step in payload['steps']:
        refs = step['elements']
        op = step['operation']
        mentioned = set(re.findall(r'\b[WC]\d+\b', ' '.join([step['action'], step['verification']] + step['preconditions'] + step['stop_conditions'])))
        if not mentioned <= set(refs):
            raise ValueError('文字提及未引用的元素别名')
        if op == 'ask_user':
            if refs or step['evidence']:
                raise ValueError('确认步骤不能伪装为控件操作')
            continue
        if data['goal_requires_confirmation'] or not data['observation_success'] or not refs or any(ref not in elements for ref in refs):
            raise ValueError('引用未观察元素或失败观察')
        expected = {elements[ref]['evidence'] for ref in refs}
        if set(step['evidence']) != expected:
            raise ValueError('证据与元素不匹配')
        if op in ('propose_ui_action', 'review_control') and any(elements[ref]['kind'] != 'control' for ref in refs):
            raise ValueError('控件步骤必须引用控件')
        if op in ('propose_activation', 'review_window') and any(elements[ref]['kind'] != 'window' for ref in refs):
            raise ValueError('窗口步骤必须引用窗口')
        if op == 'propose_ui_action' and any(elements[ref]['label'] == 'redacted' or elements[ref].get('enabled') is False for ref in refs):
            raise ValueError('缺少可确认的入口语义或控件禁用')


def plan_with_provider(goal, scene, provider):
    # 外部 Provider 的授权错误不悄悄回退：CLI 必须明确告诉用户请求未获允许。
    if hasattr(provider, 'validate'):
        provider.validate()
    prompt = build_prompt(goal, scene)
    reason = None
    try:
        unified = provider if hasattr(provider, 'complete') else LegacyProviderAdapter(provider)
        response = unified.complete(LLMRequest(**prompt, schema=PLAN_SCHEMA))
        if not isinstance(response, LLMResponse) or response.finish_reason != 'completed':
            raise ModelFailure('model_invalid_response')
        reply = response.text
    except ModelPolicyError:
        raise
    except ModelFailure as exc:
        reason = exc.code if exc.code in {'model_timeout', 'model_authentication', 'model_redirect_rejected',
            'model_sdk_version', 'model_sdk_missing', 'model_incomplete', 'model_nontext_output', 'model_failure',
            'model_invalid_response', 'model_capability', 'model_endpoint_rejected', 'model_secret_rejected'} else 'model_failure'
    except TimeoutError:
        reason = 'model_timeout'
    except Exception:
        reason = 'model_failure'
    if reason is None:
        try:
            return parse_plan(reply, prompt, planner=provider.name if isinstance(provider.name, str) else 'llm')
        except (ValueError, TypeError, RecursionError):
            reason = 'invalid_plan'
    # 回退也仅使用脱敏 Scene 和目标，避免 rule 把原始证据/标题重新输出。
    safe_scene = public_planning_scene(scene, prompt)
    fallback = make_plan(prompt['data']['goal'].replace(' ', ''), safe_scene, review_only=True)
    return replace(fallback, planner='rule-fallback', fallback_reason=reason,
                   uncertainties=tuple(prompt['data']['uncertainties']),
                   confirmations=('模型规划失败；请审查规则计划或补充明确目标',))
