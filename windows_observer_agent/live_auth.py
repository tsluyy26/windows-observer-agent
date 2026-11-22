"""Authentication-only probes for the manual Windows-MCP 0.8.8 launcher."""

import asyncio
import json

from .mcp_client import ALLOWED_TOOLS, MCPClient, MCPPolicyError
from .public_errors import InvalidArguments, SafeArgumentParser, emit_error, error_code, quiet_external_logs
from .transports import StreamableHTTPTransport


class AuthProbeError(MCPPolicyError):
    """Failed protocol check, optionally carrying a verified HTTP status."""

    def __init__(self, message, *, status=None, category='mcp_policy'):
        self.status = status
        self.category = category
        super().__init__(message)


class _DiscoveryTransport(StreamableHTTPTransport):
    unexpected_status: int | None = None

    async def _guard_response(self, response):
        method = response.request.method
        if method == 'POST':
            request_method = json.loads(response.request.content).get('method', '')
            allowed = (202,) if request_method.startswith('notifications/') else (200,)
        elif method == 'GET':
            allowed = (200, 405)  # The optional stream can be disabled by the server.
        else:
            allowed = (200, 202, 204, 405)  # Session cleanup.
        if response.status_code not in allowed:
            if self.unexpected_status is None:
                self.unexpected_status = response.status_code
            raise AuthProbeError(f'认证连接收到 HTTP {response.status_code}', status=response.status_code)


async def probe(port: int, mode: str) -> None:
    if not 1024 <= port <= 65535 or mode not in ('missing', 'wrong', 'correct'):
        raise AuthProbeError('认证探测参数无效')
    endpoint = f'http://127.0.0.1:{port}/mcp'
    if mode == 'correct':
        transport = _DiscoveryTransport(endpoint, enabled=True)
        async with MCPClient(transport, call_timeout=5) as client:
            try:
                tools = (await client.list_tools())['tools']
            except AuthProbeError:
                raise
            except MCPPolicyError as exc:
                raise AuthProbeError('认证后工具发现未通过', category=error_code(exc, 'live')) from None
            names = [tool['name'] for tool in tools]
            if len(names) != len(ALLOWED_TOOLS) or set(names) != ALLOWED_TOOLS:
                raise AuthProbeError('认证后工具列表与三项白名单不一致')
        # The SDK can log and suppress an HTTP failure during session cleanup.
        if transport.unexpected_status is not None:
            raise AuthProbeError(f'认证连接收到 HTTP {transport.unexpected_status}', status=transport.unexpected_status)
        return

    import httpx2
    from mcp_types.version import LATEST_HANDSHAKE_VERSION

    headers = {'Accept': 'application/json, text/event-stream'}
    if mode == 'wrong':
        # The manual launcher generates hexadecimal keys only.
        headers['Authorization'] = 'Bearer ' + 'x' * 64
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': LATEST_HANDSHAKE_VERSION, 'capabilities': {},
        'clientInfo': {'name': 'windows-observer-agent-auth-probe', 'version': '0.4.1'},
    }}
    async with asyncio.timeout(5), httpx2.AsyncClient(trust_env=False, follow_redirects=False) as http:
        async with http.stream('POST', endpoint, headers=headers, json=request) as response:
            if response.status_code != 401:
                raise AuthProbeError(f'应拒绝认证的初始化收到 HTTP {response.status_code}，预期 HTTP 401',
                                     status=response.status_code)
            if (response.headers.get('WWW-Authenticate', '').strip().casefold() != 'bearer'
                    or 'Mcp-Session-Id' in response.headers):
                raise AuthProbeError('HTTP 401 缺少预期 Bearer challenge 或附带了会话标识')


def main(argv=None) -> int:
    parser = SafeArgumentParser()
    parser.add_argument('port', type=int)
    parser.add_argument('mode', choices=('missing', 'wrong', 'correct'))
    try:
        args = parser.parse_args(argv)
    except InvalidArguments:
        emit_error('arguments', 'arguments')
        return 2
    try:
        with quiet_external_logs():
            asyncio.run(probe(args.port, args.mode))
    except KeyboardInterrupt:
        emit_error('live', 'cancelled')
        return 130
    except AuthProbeError as exc:
        status = f' HTTP {exc.status}。' if type(exc.status) is int and 100 <= exc.status <= 599 else ''
        print(f'LIVE_AUTH_FAILED [{args.mode}] MCP 认证协议或工具发现未通过。' + status)
        if exc.category == 'resource_limit':
            emit_error('live', 'resource_limit')
        return 2
    except Exception as exc:
        print(f'LIVE_AUTH_FAILED [{args.mode}]')
        code = error_code(exc, 'live')
        emit_error('live', 'mcp_connection' if code in ('unknown', 'internal') else code)
        return 2
    print(f'LIVE_AUTH_PROBE_PASS [{args.mode}]')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
