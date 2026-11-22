"""默认关闭的单次 Live 观察；没有服务启动、扫描或 stdio 回退。"""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from .mcp_adapter import CONTROL_ERROR_CODES, adapt_records, parse_control
from .mcp_client import ALLOWED_TOOLS, MCPClient, MCPError
from .public_errors import MESSAGES, error_code, quiet_external_logs

_CONTROL_STOP_REASONS = {f'控制器不允许观察：{owner}': owner for owner in
                         ('ready', 'agent', 'user', 'takeover_pending', 'unavailable', 'none', 'unknown')}


async def observe_live(client: MCPClient):
    records = []
    previous_control = None
    stop_code = None
    diagnostics = {}
    owner = None
    discovered = []
    try:
        async with client:
            listing = await client.list_tools()
            tools = listing.get('tools') if isinstance(listing, dict) else None
            if not isinstance(tools, list) or not all(isinstance(t, dict) and isinstance(t.get('name'), str) for t in tools):
                stop_code = 'mcp_discovery'
            else:
                discovered = [t['name'] for t in tools if t['name'] in ALLOWED_TOOLS]
                if not ALLOWED_TOOLS <= set(discovered):
                    stop_code = 'mcp_discovery'
            if stop_code is None:
                requests = [('ControlStatus', {}), ('Screenshot', {'use_annotation': False}),
                            ('ControlStatus', {}),
                            ('Snapshot', {'use_vision': False, 'use_dom': False, 'use_ui_tree': True}),
                            ('ControlStatus', {})]
                for tool, arguments in requests:
                    started = datetime.now(timezone.utc).isoformat()
                    try:
                        result = await client.call_tool(tool, arguments)
                    except MCPError as exc:
                        stop_code = error_code(exc, 'live')
                        diagnostics = _public_diagnostics(getattr(exc, 'diagnostics', {}))
                        diagnostics['tool'] = tool
                        result = {'isError': True, 'content': []}
                    records.append({'tool': tool, 'arguments': arguments, 'started_at': started,
                        'observed_at': datetime.now(timezone.utc).isoformat(),
                        'time_basis': 'client-received-not-server-capture', 'result': result})
                    if stop_code:
                        break
                    if tool == 'ControlStatus':
                        control = parse_control(result)
                        if not control['success'] or control['owner'] not in ('ready', 'agent'):
                            owner = control['owner']
                            stop_code = 'mcp_controller'
                            break
                        current = control['raw_state']
                        if not _control_continuous(previous_control, current):
                            stop_code = 'mcp_controller'
                            diagnostics['tool'] = 'ControlStatus'
                            break
                        previous_control = current
    except MCPError as exc:
        if not records:  # 连接/认证/发现失败：CLI 明确报错，不伪装成桌面结果。
            raise
        stop_code = error_code(exc, 'live')
        if stop_code == 'mcp_connection':
            stop_code = 'mcp_shutdown'
    attempted = [record['tool'] for record in records]
    if stop_code:
        # Do not pass provisional desktop data to the adapter, report or planner.
        records = [r for r in records if r['tool'] == 'ControlStatus' or r['result'].get('isError')]
    raw = adapt_records(records, source='MCP:streamable-http', kind='mcp')
    complete = stop_code is None and raw.snapshot.get('success') is True
    if not complete and stop_code is None:
        stop_code = 'mcp_snapshot'
    reason = _stop_reason(stop_code, diagnostics, owner)
    if reason:
        raw = replace(raw, snapshot=dict(raw.snapshot, success=False, error=reason))
    timestamp = raw.observed_at or (records[-1]['observed_at'] if records else None)
    return replace(raw, observed_at=timestamp,
        metadata={'observation_complete': complete, 'stop_reason': reason, 'error_code': stop_code,
                  'diagnostics': diagnostics,
                  'attempted_tools': attempted,
                  'discovered_allowed_tools': discovered, 'image_analysis': False,
                  'basis': 'UI-tree-and-tool-text', 'image_data_saved': False})


def _control_continuous(previous, current):
    """0.8.8 increments generation on each transition, including ready -> ai."""
    generation = current.get('generation')
    if type(generation) is not int or current.get('wait_seconds') != 0:
        return False
    if previous is None:
        return True
    if previous['state'] == current['state']:
        return generation == previous['generation']
    return (previous['state'] == 'ready' and current['state'] == 'ai'
            and generation == previous['generation'] + 1)


def collect_live(endpoint: str):
    # 先做同步安全检查，缺 Token 时连事件循环的 socketpair 都不创建。
    from .transports import StreamableHTTPTransport
    transport = StreamableHTTPTransport(endpoint, enabled=True)
    transport.validate_live()
    # 第三方服务通知/HTTP debug 日志可能含正文或请求头，单次 Live 期间禁止转发。
    with quiet_external_logs():
        return asyncio.run(observe_live(MCPClient(transport)))


def _public_diagnostics(data):
    """Keep the protocol and tool fields used to diagnose a failed observation."""
    if not isinstance(data, dict):
        return {}
    allowed = {
        'category': ('control_rejected', 'server_tool_error', 'server_capture_error', 'sdk_output_schema',
                     'sse_event_size_limit', 'result_parse_error', 'sdk_call_exception'),
        'phase': ('call', 'parse'),
        'exception_type': ('ValueError', 'RuntimeError', 'ValidationError', 'MCPError', 'ReadError',
                           'ConnectError', 'RemoteProtocolError', 'TimeoutError', 'Exception'),
        'control_code': CONTROL_ERROR_CODES,
        'reported_control_state': ('ready', 'user', 'ai', 'unavailable', 'takeover_pending'),
        'tool': tuple(ALLOWED_TOOLS),
    }
    safe = {key: value for key, options in allowed.items()
            if isinstance(value := data.get(key), str) and value in options}
    code = data.get('protocol_code')
    if type(code) is int and -(2**31) <= code < 2**31:
        safe['protocol_code'] = code
    content_types = data.get('content_types')
    if isinstance(content_types, list):
        safe['content_types'] = [kind if isinstance(kind, str) and kind in
                                 ('text', 'image', 'audio', 'resource', 'resource_link') else 'unknown'
                                 for kind in content_types]
    return safe


def _stop_reason(code, diagnostics, owner):
    if not code:
        return None
    reason = MESSAGES.get(code, MESSAGES['unknown'])
    tool = diagnostics.get('tool')
    if tool:
        return f'{tool} · {reason}'
    control_reason = f'控制器不允许观察：{owner}'
    if code == 'mcp_controller' and control_reason in _CONTROL_STOP_REASONS:
        return control_reason
    return reason


def public_live_observation(raw):
    """输出使用允许列表脱敏；内部模型仍供 Planner 使用真实 UI 文本。"""
    from .replay import sanitize_record
    safe_records = [sanitize_record(record) for record in raw.tool_results]
    safe = adapt_records(safe_records, source=raw.source, kind=raw.kind)
    diagnostics = _public_diagnostics(raw.metadata.get('diagnostics'))
    code = raw.metadata.get('error_code')
    owner = safe.control_status.get('owner')
    if not isinstance(code, str) or code not in MESSAGES:
        # Older observations carried only a stop reason. Recognize exact known
        # controller messages without letting arbitrary legacy text pass through.
        previous = raw.metadata.get('stop_reason')
        legacy_owner = _CONTROL_STOP_REASONS.get(previous) if isinstance(previous, str) else None
        if legacy_owner is not None:
            code, owner = 'mcp_controller', legacy_owner
        else:
            code = 'mcp_snapshot' if previous else None
    reason = _stop_reason(code, diagnostics, owner)
    if reason:
        safe = replace(safe, snapshot=dict(safe.snapshot, success=False, error=reason))
    metadata = {
        'observation_complete': raw.metadata.get('observation_complete') is True,
        'stop_reason': reason, 'error_code': code, 'diagnostics': diagnostics,
        'image_analysis': False, 'basis': 'UI-tree-and-tool-text', 'image_data_saved': False, 'output_redacted': True,
    }
    for key in ('attempted_tools', 'discovered_allowed_tools'):
        metadata[key] = [tool for tool in raw.metadata.get(key, ())
                         if isinstance(tool, str) and tool in ALLOWED_TOOLS]
    return replace(safe, observed_at=raw.observed_at, metadata=metadata,
                   warnings=safe.warnings + ('输出已脱敏；仅基于 UI 树和工具文本，未实施视觉分析',))
