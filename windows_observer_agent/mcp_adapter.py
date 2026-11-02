"""本机 Windows-MCP 0.8.8 文本 renderer 的保守适配，不猜测 exe/控制权。"""
from copy import deepcopy
import json
import math
import re

from .models import Observation

WINDOW_ROW = re.compile(r'^\s*(.*?)\s+(-?\d+)\s+(Normal|Maximized|Minimized|Hidden)\s+(\d+)\s+(\d+)\s+(\d+)\s*$')
CONTROL_ROW = re.compile(r'^[│\s├└─]*(\([-?\d,\s]+\)|\(\?\))\s+([\w ]+)\s+"(.*?)"(?:\s+.*)?$')
WINDOW_NODE = re.compile(r'^[│\s├└─]*window\s+"(.*)"\s*$')
DISPLAY_ROW = re.compile(r'(\d+):[^;]*?\((-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\)')
TRUNCATION = re.compile(r'\[truncated: reached the (\d+)-element capture limit', re.I)
HEADINGS = frozenset({'Active Desktop:', 'All Desktops:', 'Focused Window:', 'Opened Windows:', 'UI Tree:'})
CONTROL_ERROR_CODES = ('USER_CONTROL', 'TAKEOVER_PENDING', 'CONTROL_UNAVAILABLE',
                       'CONTROL_PREEMPTED', 'PHYSICAL_INPUT_HELD')


def diagnose_tool_failure(payload, exc: Exception, phase: str) -> dict:
    """只输出有限分类/数字/结构。原文只在本函数内检查，永不返回。"""
    category = 'result_parse_error' if phase == 'parse' else 'sdk_call_exception'
    details = {'category': category, 'phase': phase}
    exception_name = type(exc).__name__
    details['exception_type'] = exception_name if exception_name in (
        'ValueError', 'RuntimeError', 'ValidationError', 'MCPError', 'ReadError',
        'ConnectError', 'RemoteProtocolError', 'TimeoutError') else 'Exception'
    message = str(exc)
    error = getattr(exc, 'error', None)
    code = getattr(error, 'code', None)
    if type(code) is int and -(2**31) <= code < 2**31:
        details['protocol_code'] = code
    texts = [message]
    if isinstance(payload, dict):
        body = payload.get('result', payload)
        if isinstance(body, dict):
            content = body.get('content')
            if isinstance(content, list):
                details['content_types'] = [b.get('type') if b.get('type') in (
                    'text', 'image', 'audio', 'resource', 'resource_link') else 'unknown'
                    for b in content if isinstance(b, dict)]
                texts.extend(decode_text(b['text']) for b in content
                             if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str))
            if body.get('isError', body.get('is_error')) is True:
                details['category'] = 'server_tool_error'
    combined = '\n'.join(texts)
    for control_code in CONTROL_ERROR_CODES:
        if re.search(r'"code"\s*:\s*"' + control_code + '"', combined):
            details['category'] = 'control_rejected'
            details['control_code'] = control_code
            state = re.search(r'"state"\s*:\s*"(ready|user|ai|unavailable|takeover_pending)"', combined)
            if state:
                details['reported_control_state'] = state[1]
            break
    else:
        if 'Error capturing screenshot:' in combined or 'Error capturing desktop state:' in combined:
            details['category'] = 'server_capture_error'
        elif 'output schema' in message.lower() or 'structured content' in message.lower():
            details['category'] = 'sdk_output_schema'
        elif 'sse' in combined.lower() and any(word in combined.lower() for word in ('size', 'limit', 'large')):
            details['category'] = 'sse_event_size_limit'
    return details


def decode_text(text: str) -> str:
    """FastMCP 可将 list[str] 序列化为一个文本块中的 JSON 数组。"""
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return '\n'.join(value)
    return text


def normalize_result(payload: dict) -> dict:
    """接收 MCP wire/SDK JSON，保留未知字段；图片内容一律去除。"""
    if not isinstance(payload, dict) or 'error' in payload:
        raise ValueError('MCP JSON-RPC 错误或无效结果')
    payload = deepcopy(payload)
    if 'result' in payload and isinstance(payload['result'], dict) and 'content' not in payload:
        payload = payload['result']
    for old, new in (('is_error', 'isError'), ('structured_content', 'structuredContent')):
        if old in payload:
            if new in payload and payload[new] != payload[old]:
                raise ValueError('MCP 别名字段冲突')
            payload[new] = payload.pop(old)
    if type(payload.get('isError', False)) is not bool or payload.get('isError') is True:
        raise ValueError('MCP 工具错误或错误标记无效')
    content = payload.get('content', [])
    if not isinstance(content, list) or not (content or isinstance(payload.get('structuredContent'), dict)):
        raise ValueError('MCP 结果缺少有效内容')
    cleaned = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError('MCP 内容块不是对象')
        if block.get('type') == 'text':
            if not isinstance(block.get('text'), str):
                raise ValueError('MCP 文本无效')
            if decode_text(block['text']).lstrip().startswith(('Error capturing desktop state:', 'Error capturing screenshot:')):
                raise ValueError('Windows-MCP 返回捕获错误文本')
        if block.get('type') == 'image':
            block = {'type': 'image', 'mimeType': block.get('mimeType', block.get('mime_type')),
                     'omitted': True}
        cleaned.append(block)
    payload['content'] = cleaned
    payload['isError'] = False
    return payload


def result_text(result: dict) -> str:
    return '\n'.join(decode_text(block['text']) for block in result.get('content', []) if block.get('type') == 'text')


def parse_control(payload: dict) -> dict:
    try:
        result = normalize_result(payload)
        structured = result.get('structuredContent')
        text = result_text(result)
        from_text = json.loads(text) if text.strip() else None
        if structured is not None and from_text is not None and structured != from_text:
            raise ValueError('ControlStatus 文本与结构化内容冲突')
        state = structured if structured is not None else from_text
        if not isinstance(state, dict) or not isinstance(state.get('state'), str):
            raise ValueError('ControlStatus 缺少 state')
        owner = state['state']
        if owner not in ('ai', 'user', 'ready', 'takeover_pending', 'unavailable'):
            raise ValueError('ControlStatus 未知状态')
        if 'generation' in state and (type(state['generation']) is not int or state['generation'] < 0):
            raise ValueError('ControlStatus 控制代次无效')
        wait = state.get('wait_seconds')
        if wait is not None and (type(wait) not in (int, float) or not math.isfinite(wait) or wait < 0):
            raise ValueError('ControlStatus 等待时间无效')
        if owner == 'ai' and (type(state.get('generation')) is not int or state['generation'] < 0 or wait != 0):
            raise ValueError('AI 控制凭据缺失或矛盾')
        return {'success': True, 'owner': 'agent' if owner == 'ai' else owner, 'raw_state': state}
    except (ValueError, TypeError):
        return {'success': False, 'owner': 'unknown', 'error': 'ControlStatus 无效、失败或相互冲突'}


def parse_snapshot(result: dict, source: str) -> tuple[dict, list[str]]:
    """只解析已核实的表格与语义树；原始文本仍在 Observation.tool_results。"""
    warnings = []
    snapshot = {'success': False, 'desktop_id': None, 'monitors': [], 'windows': [],
                'active_window_id': None, 'ui_tree_truncated': True, 'unassigned_controls': []}
    text = result_text(result)
    lines = [(line, f'{source}/content/{i}/text#decoded-line={number}')
             for i, block in enumerate(result.get('content', [])) if block.get('type') == 'text'
             for number, line in enumerate(decode_text(block['text']).splitlines(), 1)]
    section = None
    seen = set()
    focused = []
    windows = {}
    current_window = None
    tree_rows = []
    for row_number, (line, line_source) in enumerate(lines, 1):
        stripped = line.strip()
        if stripped in HEADINGS:
            section = stripped
            seen.add(section)
            continue
        if line.lstrip().startswith('Visible Displays:'):
            for match in DISPLAY_ROW.finditer(line):
                index, left, top, right, bottom = map(int, match.groups())
                snapshot['monitors'].append({'id': str(index), 'width': right-left,
                                             'height': bottom-top, '_source': line_source})
        if section in ('Focused Window:', 'Opened Windows:'):
            match = WINDOW_ROW.fullmatch(line)
            if match:
                title, depth, status, width, height, handle = match.groups()
                window = {'id': handle, 'title': title, 'app': None, 'controls': [],
                          'properties': {'depth': int(depth), 'status': status,
                                         'width': int(width), 'height': int(height)}, '_source': line_source}
                if section == 'Focused Window:':
                    focused.append(window)
                else:
                    if handle in windows:
                        warnings.append('Opened Windows 存在重复 Handle，身份需重新验证')
                    windows[handle] = window
        if section == 'UI Tree:':
            window_match = WINDOW_NODE.fullmatch(line)
            if window_match:
                current_window = window_match.group(1)
            control = CONTROL_ROW.fullmatch(line)
            if control:
                coords, control_type, name = control.groups()
                numbers = re.findall(r'-?\d+', coords)
                properties = {'coordinates': list(map(int, numbers)) if len(numbers) == 2 else None,
                              'coordinate_space': 'virtual-desktop',
                              'metadata_text': line[line.find('"', line.find('"') + 1) + 1:].strip()}
                tree_rows.append((current_window, {'id': f'observed-line-{row_number}', 'name': name,
                    'type': control_type.strip(), 'provider': 'Windows-MCP.semantic-tree',
                    'properties': properties, '_source': line_source}))
    if len(focused) == 1:
        window = focused[0]
        existing = windows.get(window['id'])
        if existing is None or existing['title'] == window['title']:
            windows.setdefault(window['id'], window)
            snapshot['active_window_id'] = window['id']
        else:
            warnings.append('Focused/Open Windows 身份冲突，活动窗口未知')
    else:
        warnings.append('活动窗口缺失或无法唯一确定')
    for window_name, control in tree_rows:
        matches = [w for w in windows.values() if w['title'] == window_name]
        if len(matches) == 1:
            matches[0]['controls'].append(control)
        else:
            snapshot['unassigned_controls'].append(control)
    snapshot['windows'] = list(windows.values())
    marker = TRUNCATION.search(text)
    if marker:
        snapshot['capture_limit'] = int(marker.group(1))
        warnings.append('服务显式报告 UI 树截断')
    else:
        warnings.append('未见截断标记不证明 UI 完整；可能跳过树、部分窗口失败或格式变化')
    warnings.append('工具只提供桌面名称，不能据此推断桌面编号；窗口标题不能证明 exe 身份')
    snapshot['truncation_reported'] = marker is not None
    snapshot['completeness'] = 'truncated' if marker else 'unknown'
    if snapshot['unassigned_controls']:
        warnings.append('部分控件无法唯一关联窗口，保留为未归属控件')
    snapshot['success'] = {'Focused Window:', 'Opened Windows:', 'UI Tree:'} <= seen and (
        bool(windows) or 'No windows found' in text)
    if not snapshot['success']:
        snapshot['error'] = 'Snapshot 缺少已知结构或窗口数据无法解析'
    return snapshot, warnings


def adapt_records(records: list[dict], *, source: str, kind: str) -> Observation:
    snapshot = {}
    control = {'success': False, 'owner': 'unknown', 'error': 'ControlStatus 缺失'}
    warnings = []
    retained = []
    field_sources = {}
    observed_at = None
    for i, record in enumerate(records):
        tool = record.get('tool')
        record_source = f'{source}#/records/{i}/result'
        entry = deepcopy(record)
        try:
            result = normalize_result(record.get('result'))
            entry['result'] = result
            if tool == 'ControlStatus':
                control = parse_control(result)
                field_sources['control_status'] = record_source
            elif tool == 'Snapshot':
                field_sources['snapshot'] = record_source
                field_sources['observed_at'] = f'{source}#/records/{i}/observed_at'
                snapshot, notes = parse_snapshot(result, record_source)
                warnings.extend(notes)
                observed_at = record.get('observed_at')
            elif tool == 'Screenshot':
                warnings.append('Screenshot 图像数据已丢弃，仅保留 MIME 与省略标记；不与 Snapshot 强行合并')
        except ValueError:
            # 错误原文可能含路径/凭据，不保留到公开模型；正常未知字段仍保留。
            entry['result'] = {'isError': True, 'content': []}
            warnings.append(str(tool) + ' 结果错误或无效')
            if tool == 'Snapshot':
                snapshot = {'success': False, 'error': 'Snapshot 结果错误或无效'}
            elif tool == 'ControlStatus':
                control = {'success': False, 'owner': 'unknown', 'error': 'ControlStatus 结果错误或无效'}
        retained.append(entry)
    if kind == 'replay':
        warnings.append('回放为历史观察，不代表当前桌面或当前控制权')
        # 即使历史记录为 ai，也不能作为现在的控制许可。
        control['historical'] = True
    return Observation(False, observed_at, snapshot, control, source, kind,
                       tuple(retained), tuple(warnings), field_sources)
