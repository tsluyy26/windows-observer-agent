"""默认关闭的单次 Live 观察；没有服务启动、扫描或 stdio 回退。"""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import logging

from .mcp_adapter import adapt_records, parse_control
from .mcp_client import ALLOWED_TOOLS, MCPClient, MCPError


async def observe_live(client: MCPClient):
    records = []
    reason = None
    discovered = []
    diagnostics = {}
    try:
        async with client:
            listing = await client.list_tools()
            tools = listing.get('tools') if isinstance(listing, dict) else None
            if not isinstance(tools, list) or not all(isinstance(t, dict) and isinstance(t.get('name'), str) for t in tools):
                reason = '工具发现结果无效'
            else:
                discovered = [t['name'] for t in tools if t['name'] in ALLOWED_TOOLS]
                if not ALLOWED_TOOLS <= set(discovered):
                    reason = '服务端缺少三个必需的只读工具'
            if reason is None:
                requests = [('ControlStatus', {}), ('Screenshot', {'use_annotation': False}),
                            ('Snapshot', {'use_vision': False, 'use_dom': False, 'use_ui_tree': True})]
                for tool, arguments in requests:
                    started = datetime.now(timezone.utc).isoformat()
                    try:
                        result = await client.call_tool(tool, arguments)
                    except MCPError as exc:
                        reason = str(exc)
                        diagnostics = dict(getattr(exc, 'diagnostics', {}), tool=tool)
                        result = {'isError': True, 'content': []}
                    records.append({'tool': tool, 'arguments': arguments, 'started_at': started,
                        'observed_at': datetime.now(timezone.utc).isoformat(),
                        'time_basis': 'client-received-not-server-capture', 'result': result})
                    if reason:
                        break
                    if tool == 'ControlStatus':
                        control = parse_control(result)
                        if not control['success'] or control['owner'] not in ('ready', 'agent'):
                            reason = '控制器不允许观察：' + control['owner']
                            break
    except MCPError as exc:
        if not records:  # 连接/认证/发现失败：CLI 明确报错，不伪装成桌面结果。
            raise
        reason = str(exc)
    raw = adapt_records(records, source='MCP:streamable-http', kind='mcp')
    complete = reason is None and raw.snapshot.get('success') is True
    if not complete and reason is None:
        reason = 'Snapshot 观察结果不完整或无法解析'
    if reason:
        raw = replace(raw, snapshot=dict(raw.snapshot, success=False, error=reason))
    timestamp = raw.observed_at or (records[-1]['observed_at'] if records else None)
    return replace(raw, observed_at=timestamp,
        metadata={'observation_complete': complete, 'stop_reason': reason,
                  'diagnostics': diagnostics,
                  'attempted_tools': [record['tool'] for record in records],
                  'discovered_allowed_tools': discovered, 'image_analysis': False,
                  'basis': 'UI-tree-and-tool-text', 'image_data_saved': False})


def collect_live(endpoint: str):
    # 先做同步安全检查，缺 Token 时连事件循环的 socketpair 都不创建。
    from .transports import StreamableHTTPTransport
    transport = StreamableHTTPTransport(endpoint, enabled=True)
    transport.validate_live()
    # 第三方服务通知/HTTP debug 日志可能含正文或请求头，单次 Live 期间禁止转发。
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(observe_live(MCPClient(transport)))
    finally:
        logging.disable(previous)


def public_live_observation(raw):
    """输出使用允许列表脱敏；内部模型仍供 Planner 使用真实 UI 文本。"""
    from .replay import sanitize_record
    safe_records = [sanitize_record(record) for record in raw.tool_results]
    safe = adapt_records(safe_records, source=raw.source, kind=raw.kind)
    if raw.metadata.get('stop_reason'):
        safe = replace(safe, snapshot=dict(safe.snapshot, success=False, error=raw.metadata['stop_reason']))
    return replace(safe, observed_at=raw.observed_at,
                   metadata=dict(raw.metadata, output_redacted=True),
                   warnings=safe.warnings + ('输出已脱敏；仅基于 UI 树和工具文本，未实施视觉分析',))
