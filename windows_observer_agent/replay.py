"""脱敏回放文件：只保存允许的结构，不保存原图/窗口正文/控件值。"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import re

from .mcp_adapter import (CONTROL_ROW, DISPLAY_ROW, HEADINGS, TRUNCATION, WINDOW_NODE,
                          WINDOW_ROW, adapt_records, decode_text, normalize_result, parse_control)
from .mcp_client import validate_tool


def sanitize_record(record: dict) -> dict:
    """保守允许列表；无法识别的行/字段丢弃，而非试图枚举所有敏感词。"""
    tool = record['tool']
    arguments = deepcopy(record.get('arguments', {}))
    validate_tool(tool, arguments)
    timestamp = None
    try:
        time = datetime.fromisoformat(record.get('observed_at'))
        if time.utcoffset() is not None:
            timestamp = time.isoformat()
    except (TypeError, ValueError):
        pass
    safe = {'tool': tool, 'arguments': arguments, 'observed_at': timestamp, 'redacted': True}
    try:
        result = normalize_result(record['result'])
    except ValueError:
        safe['result'] = {'isError': True, 'content': []}
        return safe
    blocks = []
    structured = None
    # 记录级映射：文本块可以分开承载 focused/opened/UI Tree，不能分别重置身份。
    section = None
    titles, handles = {}, {}

    def title_alias(title):
        return titles.setdefault(title, f'redacted-window-{len(titles) + 1}')

    if tool == 'ControlStatus':
        state = parse_control(result).get('raw_state', {})
        structured = {key: state[key] for key in ('state', 'generation', 'wait_seconds') if key in state}
        blocks.append({'type': 'text', 'text': json.dumps(structured)})
    else:
        for block in result.get('content', []):
            if block.get('type') == 'image':
                blocks.append({'type': 'image', 'mimeType': 'image/png', 'omitted': True})
            elif block.get('type') == 'text':
                sanitized = []
                for line in decode_text(block['text']).splitlines():
                    stripped = line.strip()
                    if stripped in HEADINGS:
                        section = stripped
                        sanitized.append(stripped)
                    elif not stripped:
                        sanitized.append('')
                    elif stripped.startswith('Visible Displays:'):
                        displays = [f'{m[1]}:DISPLAY ({m[2]},{m[3]},{m[4]},{m[5]})' for m in DISPLAY_ROW.finditer(line)]
                        sanitized.append('Visible Displays: ' + '; '.join(displays))
                    elif section in ('Focused Window:', 'Opened Windows:') and (match := WINDOW_ROW.fullmatch(line)):
                        title, depth, status, width, height, handle = match.groups()
                        alias = handles.setdefault(handle, str(1000 + len(handles)))
                        sanitized.append(f'{title_alias(title)}  {depth}  {status}  {width}  {height}  {alias}')
                    elif section == 'UI Tree:' and (match := WINDOW_NODE.fullmatch(line)):
                        sanitized.append(line[:line.find('window')] + f'window "{title_alias(match[1])}"')
                    elif section == 'UI Tree:' and (match := CONTROL_ROW.fullmatch(line)):
                        coords, control_type, _ = match.groups()
                        sanitized.append(f'{coords} {control_type} "redacted-control"')
                    elif match := TRUNCATION.search(line):
                        sanitized.append(f'... [truncated: reached the {match[1]}-element capture limit]')
                    elif stripped in ('desktop', 'No elements', 'No elements found.', 'No active window found', 'No windows found'):
                        sanitized.append(stripped)
                    elif section in ('Active Desktop:', 'All Desktops:'):
                        if stripped == 'Name' or re.fullmatch(r'[- ]+', stripped):
                            sanitized.append(stripped)
                        else:
                            sanitized.append('redacted-desktop')
                    elif section in ('Focused Window:', 'Opened Windows:') and (
                            stripped.split() == ['Name', 'Depth', 'Status', 'Width', 'Height', 'Handle'] or re.fullmatch(r'[- ]+', stripped)):
                        sanitized.append(stripped)
                    # 所有其他元数据/结构节点/属性/未知行均不落盘。
                text = '\n'.join(sanitized)
                # 保留实测 JSON list[str] 包装，回放验证同一传输层形状。
                if block['text'].lstrip().startswith('['):
                    text = json.dumps([text], ensure_ascii=False)
                blocks.append({'type': 'text', 'text': text})
    safe['result'] = {'content': blocks, 'isError': False}
    if structured is not None:
        safe['result']['structuredContent'] = structured
    return safe


class ReplayClient:
    def __init__(self, path: Path):
        self.path = path

    def collect(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('schema') != 'woa.replay.v1' or data.get('redacted') is not True:
            raise ValueError('要求明确标注 redacted:true 的 woa.replay.v1 回放')
        records = data.get('records')
        if not isinstance(records, list) or not records or not all(isinstance(r, dict) and r.get('redacted') is True for r in records):
            raise ValueError('回放 records 无效或未脱敏')
        order = [r.get('tool') for r in records]
        if order not in (['ControlStatus', 'Snapshot'], ['ControlStatus', 'Snapshot', 'Screenshot']):
            raise ValueError('回放要求 ControlStatus → Snapshot → 可选 Screenshot')

        # Windows asyncio 事件循环自身会创建 socketpair；纯本地 CLI 无需事件循环。
        for record in records:
            validate_tool(record['tool'], record.get('arguments', {}))
        # 错误记录也交给适配器转为失败场景，避免重复解析以及 CLI 无法回放失败。
        observation = adapt_records(records, source='Replay:' + str(self.path.resolve()), kind='replay')
        return replace(observation, metadata={key: deepcopy(value) for key, value in data.items() if key != 'records'})
