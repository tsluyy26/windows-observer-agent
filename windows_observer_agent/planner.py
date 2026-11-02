"""有限目标的规则规划器。返回文字计划，不执行任何操作。"""
from dataclasses import replace
from .models import Plan, PlanStep, Scene

SUPPORTED_GOALS = frozenset({'新建一份空白记事本文档', '新建空白记事本文档',
                              '新建一个空白记事本文档'})


def make_plan(goal: str, scene: Scene, *, review_only: bool = False) -> Plan:
    """review_only 允许无行动权限时制定供审查的计划；默认保留 v0.3 API 门控。"""
    plan = _make_rule_plan(goal, scene, review_only=review_only)
    uncertainties = ['窗口标题和控件识别不证明内容为空或操作可用；执行前须重新观察']
    if scene.ui_tree_truncated:
        uncertainties.append('UI 树可能截断，未观察到的界面元素仍可能存在')
    if not scene.control.ready:
        uncertainties.append('当前没有可据此使用的行动权限，但可以审查条件计划')
    if not scene.observation_success:
        uncertainties.append('观察失败，无法确认当前界面')
    return replace(plan, uncertainties=tuple(uncertainties),
                   confirmations=('请用户确认目标含义、界面身份及条件计划；本程序不执行',))


def _make_rule_plan(goal: str, scene: Scene, *, review_only: bool) -> Plan:
    preconditions = ('仅生成计划；未来执行前需另行授权真实桌面控制',
                     '执行每步前重新观察同一桌面，并确认控制权仍为 agent',
                     '确认目标窗口与控件身份、可用性，保留已有文档')
    stops = ('控制权变为 user、none 或 unknown，立即停止',
             '观察失败、目标身份不明、验证不通过或出现意外对话框，立即停止',
             '涉及覆盖、关闭未保存文档、权限提升或敏感信息，停止并请求用户处理')
    assumptions = [('本次输入为 Mock；计划尚未经过真实桌面验证' if scene.raw.mock else
                    '本次输入为历史回放；不能代表实时状态' if scene.raw.kind == 'replay' else
                    '本次输入来自 MCP；执行前仍需重新观察并验证'),
                   '窗口标题不证明文档为空；控件识别不证明可以点击']
    if scene.ui_tree_truncated:
        assumptions.append('UI 树可能截断；未来定位前需重新获取目标范围的完整观察')
    normalized = goal.strip().rstrip('。.!！').strip()
    if normalized not in SUPPORTED_GOALS:
        return Plan(goal, 'unsupported', preconditions, tuple(assumptions), (),
                    ('目标不在当前版本支持范围，停止规划',))
    if not scene.observation_success or (not review_only and not scene.control.ready):
        reason = '观察失败' if not scene.observation_success else '控制状态不允许行动：' + scene.control.owner
        return Plan(goal, 'blocked', preconditions, tuple(assumptions), (), (reason + '，停止',) + stops)

    # 精确应用标识，不用标题包含“记事本”猜测应用身份。
    candidates = [w for w in scene.windows if w.app and w.app.casefold() == 'notepad.exe']
    target = scene.active_window if scene.active_window in candidates else (
        candidates[0] if len(candidates) == 1 else None)
    steps = []
    if target is None:
        assumptions.append('未观察到可唯一确定的记事本窗口，不能据此判断安装状态')
        steps.append(PlanStep(
            '未来先查找并确认记事本应用；若确认可用且授权允许，再打开并重新观察',
            preconditions + ('当前未观察到唯一目标；不关闭任何现有窗口',),
            '重新观察并确认应用身份为记事本、窗口已激活；若查找无结果，报告未知',
            stops + ('找不到或无法确认记事本应用时停止，不自行安装',), scene.evidence))
    elif target != scene.active_window:
        steps.append(PlanStep('未来激活已识别的记事本窗口',
                              preconditions + ('重新确认目标窗口标识：' + str(target.id),),
                              '重新观察，确认同一记事本窗口已成为活动窗口', stops, (target.source,)))
    steps.append(PlanStep(
        '未来在已确认的记事本中选择新建文档或新建标签页，保留现有内容',
        preconditions + ('记事本已激活；重新识别新建入口并验证可用，不能把识别当作点击验证',),
        '重新观察：确认新增文档/标签页与原有内容均保留；读取编辑区域确认内容为空',
        stops + ('无法验证空白内容或无法区分新旧文档时停止，报告未完成',),
        (target.source,) if target else scene.evidence,
    ))
    return Plan(goal, 'conditional', preconditions, tuple(assumptions), tuple(steps), stops)
