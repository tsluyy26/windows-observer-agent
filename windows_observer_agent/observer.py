"""采集一次观察；不启动服务，不访问鼠标、键盘或网络。"""
from .mcp_client import ObservationClient
from .models import Observation


def observe(client: ObservationClient) -> Observation:
    return client.collect()


async def observe_mcp(client, *, include_screenshot=False) -> Observation:
    """未来 HTTP 接入入口。调用方提供显式启用的客户端；本轮只用内存传输测试。"""
    from datetime import datetime, timezone
    from .mcp_adapter import adapt_records

    requests = [('ControlStatus', {}), ('Snapshot', {'use_vision': False, 'use_dom': False})]
    if include_screenshot:
        requests.append(('Screenshot', {'use_annotation': False}))
    records = []
    async with client:
        for tool, arguments in requests:
            started = datetime.now(timezone.utc).isoformat()
            result = await client.call_tool(tool, arguments)
            records.append({'tool': tool, 'arguments': arguments, 'started_at': started,
                            'observed_at': datetime.now(timezone.utc).isoformat(),
                            'time_basis': 'client-received-not-server-capture', 'result': result})
    return adapt_records(records, source='MCP:streamable-http', kind='mcp')
