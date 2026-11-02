"""Mock 观察到场景模型的容错转换；未知值不补猜测。"""
from copy import deepcopy
from datetime import datetime

from .control_gate import check_control
from .models import Monitor, Observation, Scene, UIControl, Window

MOCK_UI_LIMIT = 500


def build_scene(observation: Observation) -> Scene:
    snapshot = observation.snapshot
    root = observation.source
    unknowns: list[str] = list(observation.warnings)
    errors: list[str] = []

    def text(value, source):
        if isinstance(value, str) and value.strip():
            return value
        unknowns.append(source + ' 未知或缺失')
        return None

    def items(value, source):
        if isinstance(value, list):
            return value
        unknowns.append(source + ' 不是列表或缺失')
        return []

    def dimension(value, source):
        if type(value) is int and value > 0:
            return value
        unknowns.append(source + ' 分辨率未知或无效')
        return None

    observed_at = None
    try:
        observed_at = datetime.fromisoformat(observation.observed_at)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError('时间无时区')
    except (TypeError, ValueError):
        observed_at = None
        unknowns.append(root + '#/observed_at 时间未知或无效（需包含时区）')

    monitors = []
    for i, raw in enumerate(items(snapshot.get('monitors'), root + '#/snapshot/monitors')):
        source = raw.get('_source', f'{root}#/snapshot/monitors/{i}') if isinstance(raw, dict) else f'{root}#/snapshot/monitors/{i}'
        if not isinstance(raw, dict):
            unknowns.append(source + ' 非对象，已跳过')
            continue
        monitors.append(Monitor(text(raw.get('id'), source + '/id'),
                                dimension(raw.get('width'), source + '/width'),
                                dimension(raw.get('height'), source + '/height'), source))

    control_count = 0

    def parse_controls(raw_controls, source):
        nonlocal control_count
        controls = []
        for j, raw_control in enumerate(items(raw_controls, source)):
            control_count += 1
            control_source = f'{source}/{j}'
            if not isinstance(raw_control, dict):
                unknowns.append(control_source + ' 非对象，已跳过')
                continue
            control_source = raw_control.get('_source', control_source)
            properties = raw_control.get('properties')
            if not isinstance(properties, dict):
                unknowns.append(control_source + '/properties 未知或无效')
                properties = {}
            controls.append(UIControl(
                text(raw_control.get('id'), control_source + '/id'),
                text(raw_control.get('name'), control_source + '/name'),
                text(raw_control.get('type'), control_source + '/type'),
                deepcopy(properties), text(raw_control.get('provider'), control_source + '/provider'),
                control_source,
            ))
        return tuple(controls)

    windows = []
    for i, raw in enumerate(items(snapshot.get('windows'), root + '#/snapshot/windows')):
        source = f'{root}#/snapshot/windows/{i}'
        if not isinstance(raw, dict):
            unknowns.append(source + ' 非对象，已跳过')
            continue
        source = raw.get('_source', source)
        controls = parse_controls(raw.get('controls'), source + '/controls')
        windows.append(Window(text(raw.get('id'), source + '/id'),
                              text(raw.get('title'), source + '/title'),
                              text(raw.get('app'), source + '/app'), controls, source))

    unassigned = parse_controls(snapshot.get('unassigned_controls', []), root + '#/snapshot/unassigned_controls')

    active_id = snapshot.get('active_window_id')
    matches = [w for w in windows if w.id is not None and w.id == active_id]
    active = matches[0] if len(matches) == 1 else None
    if active is None:
        unknowns.append(root + '#/snapshot/active_window_id 活动窗口未确认（缺失、未匹配或重复）')
    truncated = snapshot.get('ui_tree_truncated') is not False or (observation.mock and control_count >= MOCK_UI_LIMIT)
    if truncated:
        unknowns.append('UI 树可能截断；未观察到的窗口或控件仍可能存在')
    success = snapshot.get('success') is True
    if not success:
        error = snapshot.get('error')
        errors.append(error if isinstance(error, str) else 'Snapshot 失败或缺失')
    control = check_control(observation.control_status, observation.field_sources.get('control_status', root + '#/control_status'))
    if control.error:
        errors.append(control.error)
    if not control.ready:
        unknowns.append('控制器当前不可用于行动：' + control.owner)
    unknowns.extend(('窗口清单仅表示本次识别结果，不证明应用是否安装',
                     '识别控件未经过点击验证；文档内容及是否为空尚未确认'))
    return Scene(
        observed_at, text(snapshot.get('desktop_id'), root + '#/snapshot/desktop_id'),
        tuple(monitors), active, tuple(windows), success, truncated, control,
        text(snapshot.get('screenshot_ref'), root + '#/snapshot/screenshot_ref'),
        tuple(unknowns), tuple(errors),
        tuple(observation.field_sources.values()) if observation.field_sources else
        (root + '#/observed_at', root + '#/snapshot', root + '#/control_status'), observation, unassigned,
    )
