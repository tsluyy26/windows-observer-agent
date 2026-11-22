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
from .planning_input import TYPES
from .resource_limits import InputFileError, ResourceLimitError, read_json_file

# Windows-MCP 0.8.8 emits UIA LocalizedControlType labels. Extend the existing
# projection vocabulary for Live reports with verified en-US and zh-CN labels.
_LIVE_CONTROL_TYPES = frozenset(TYPES | {
    'check box', 'combo box', 'list item', 'menu item', 'tab item', 'scroll bar',
    'custom', 'data item', 'group', 'header item', 'hyperlink', 'image', 'link',
    'menu bar', 'radio button', 'slider', 'spinner', 'split button', 'status bar',
    'tab', 'tool bar', 'tree item',
    '编辑', '复选框', '组合框', '列表项目', '拆分按钮', '单选按钮', '链接', '滑块',
    '树项目', '项', '标头项目', '沙网', '文档', '窗口', '窗格', '文本', '工具栏',
    '选项卡', '菜单栏', '自定义', '组', '图像', '状态栏',
})
# Recover rows with malformed or missing types for redaction. The raw MCP
# parser and Replay retain their existing grammar.
_LIVE_CONTROL_ROW = re.compile(
    r'^[│\s├└─]*(\([-?\d,\s]+\)|\(\?\))\s++([^"]*\s)?"(.*?)"(?:\s+.*)?$'
)


def _normalize_live_control_type(value: str | None) -> str:
    normalized = ' '.join(value.casefold().split()) if isinstance(value, str) else ''
    return normalized if normalized in _LIVE_CONTROL_TYPES else 'unknown'


def sanitize_record(record: dict, *, for_replay: bool = False) -> dict:
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
    except ResourceLimitError:
        raise
    except ValueError:
        safe['result'] = {'isError': True, 'content': []}
        return safe
    blocks = []
    structured = None
    control_row = CONTROL_ROW if for_replay else _LIVE_CONTROL_ROW
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
                    elif section == 'UI Tree:' and (match := control_row.fullmatch(line)):
                        coords, control_type, _ = match.groups()
                        if for_replay:
                            if control_type.strip().casefold() not in TYPES:
                                control_type = 'unknown'
                        else:
                            control_type = _normalize_live_control_type(control_type)
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
        try:
            data = read_json_file(self.path)
        except (InputFileError, ResourceLimitError):
            raise
        except (ValueError, RecursionError):
            raise ValueError('无法读取有效的 UTF-8 Replay JSON') from None
        if not isinstance(data, dict) or data.get('schema') != 'woa.replay.v1' or data.get('redacted') is not True:
            raise ValueError('要求明确标注 redacted:true 的 woa.replay.v1 回放')
        records = data.get('records')
        if not isinstance(records, list) or not records or not all(isinstance(r, dict) and r.get('redacted') is True for r in records):
            raise ValueError('回放 records 无效或未脱敏')
        order = [r.get('tool') for r in records]
        if order not in (['ControlStatus', 'Snapshot'], ['ControlStatus', 'Snapshot', 'Screenshot']):
            raise ValueError('回放要求 ControlStatus → Snapshot → 可选 Screenshot')

        if any(not isinstance(record.get('result'), dict) for record in records):
            raise ValueError('回放工具记录缺少结果对象')

        # 只有投影后的记录进入 Scene、规则规划和两种报告。
        # 使用固定来源前缀，保留字段定位，避免将本机文件路径放入公开报告。
        try:
            safe_records = [sanitize_record(record, for_replay=True) for record in records]
            observation = adapt_records(safe_records, source='Replay:local', kind='replay')
        except ResourceLimitError:
            raise
        except (ValueError, OverflowError, RecursionError):
            raise ValueError('回放工具记录的参数或格式无效') from None
        return replace(observation, metadata={'schema': 'woa.replay.v1', 'redacted': True})
