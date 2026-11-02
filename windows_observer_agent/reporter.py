"""只生成字符串报告，不写入文件。"""
from .models import Plan, Scene
from . import __version__


def render_report(scene: Scene, plan: Plan) -> str:
    mode = '离线 Mock 报告（非真实桌面）' if scene.raw.mock else ('脱敏历史回放报告（非实时状态）' if scene.raw.kind == 'replay' else 'MCP 观察报告')
    lines = [f'Windows Observer Agent v{__version__} — {mode}',
             f'观察时间：{scene.observed_at or "未知"}',
             f'桌面：{scene.desktop_id or "未知"}',
             f'观察成功：{scene.observation_success}；UI 树可能截断：{scene.ui_tree_truncated}',
             f'控制器：{scene.control.owner}；查询成功：{scene.control.success}',
             f'原始控制状态：{scene.control.state}；历史数据：{scene.control.historical}',
             f'活动窗口：{scene.active_window.title if scene.active_window else "未知"}',
             '显示器：']
    if scene.raw.kind == 'mcp':
        privacy = '输出已脱敏' if scene.raw.metadata.get('output_redacted') else '内存观察模型'
        lines.insert(1, '识别依据：UI 树与工具文本；未实施视觉分析；截图不落盘；' + privacy)
        lines.insert(2, f'Live 观察完成：{scene.raw.metadata.get("observation_complete", False)}')
        if scene.raw.metadata.get('stop_reason'):
            lines.insert(3, '停止原因：' + scene.raw.metadata['stop_reason'])
        if scene.raw.metadata.get('diagnostics'):
            lines.insert(4, '脱敏诊断：' + str(scene.raw.metadata['diagnostics']))
        if scene.raw.metadata.get('attempted_tools'):
            lines.insert(3, '已调用工具：' + ' → '.join(scene.raw.metadata['attempted_tools']))
    lines.extend(f'  {m.id or "未知"}: {m.width or "?"} × {m.height or "?"} [{m.source}]'
                 for m in scene.monitors)
    lines.append('已识别窗口（不代表完整窗口清单）：')
    for window in scene.windows:
        lines.append(f'  {window.id or "未知"}: {window.title or "标题未知"} / {window.app or "应用未知"} [{window.source}]')
        for control in window.controls:
            lines.append(f'    {control.name or "名称未知"} ({control.type or "类型未知"}), '
                         f'属性={control.properties}, 来源={control.provider or "未知"}, '
                         f'点击已验证={control.click_verified} [{control.source}]')
    lines.append(f'未归属控件：{len(scene.unassigned_controls)}（完整属性见 JSON）')
    lines.append(f'截图引用：{scene.screenshot_ref or "未知/已省略"}（未读取图片）')
    lines.append('未知与限制：')
    lines.extend('  - ' + value for value in scene.unknowns)
    lines.extend('错误：' + value for value in scene.errors)
    lines.extend((f'目标：{plan.goal}', f'计划状态：{plan.status}；可执行：{plan.executable}', '前置条件：'))
    lines.insert(len(lines) - 1, f'规划状态：{plan.planning_status}；规划器：{plan.planner}；执行权限：{plan.execution_permission}')
    lines.insert(len(lines) - 1, '目标理解：' + plan.goal_understanding)
    if plan.fallback_reason:
        lines.insert(len(lines) - 1, '规则回退原因：' + plan.fallback_reason)
    lines.extend('  - ' + value for value in plan.preconditions)
    lines.append('规划依据与假设：')
    lines.extend('  - ' + value for value in plan.assumptions)
    for i, step in enumerate(plan.steps, 1):
        lines.extend((f'{i}. {step.action}', '   前提：' + '；'.join(step.preconditions),
                      '   验证：' + step.verification, '   失败停止：' + '；'.join(step.stop_conditions),
                      '   证据：' + '；'.join(step.evidence)))
    lines.append('全局停止条件：')
    lines.extend('  - ' + value for value in plan.stop_conditions)
    lines.append('规划不确定信息：')
    lines.extend('  - ' + value for value in plan.uncertainties)
    lines.append('需要用户确认：')
    lines.extend('  - ' + value for value in plan.confirmations)
    return '\n'.join(lines)
