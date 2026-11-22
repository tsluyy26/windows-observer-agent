"""只读传输：本地回放与默认关闭的可选 HTTP；没有 stdio 实现。"""
import asyncio
from contextlib import AsyncExitStack
from copy import deepcopy
from importlib.metadata import version
import os
from urllib.parse import urlsplit

from .mcp_client import (MCPAuthenticationError, MCPCallError, MCPConnectionError,
                         MCPCredentialError, MCPPolicyError, MCPResourceError, validate_tool)
from .resource_limits import (
    MCP_EVENT_BYTES, MCP_HTTP_BYTES, MCP_HTTP_REQUESTS, MCP_SESSION_BYTES, MCP_SSE_EVENTS,
    MCP_TOOL_PAGE, MCP_TOOL_PAGES, MCP_TOOLS_TOTAL, ResourceLimitError,
)

SDK_VERSION = '2.3.0'
HTTP_VERSION = '2.16.0'
TOKEN_ENV = 'WOA_MCP_TOKEN'


def validate_endpoint(url: str):
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname == '127.0.0.1'
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and parsed.path.startswith('/')
                 and (parsed.port is None or 1 <= parsed.port <= 65535)
                 and not any(char.isspace() for char in url))
    except ValueError:
        valid = False
    if not valid:
        raise MCPPolicyError('端点必须是无凭据/query/fragment 的 127.0.0.1 HTTP 地址')
    return parsed


class ReplayTransport:
    def __init__(self, records: list[dict]):
        self.records = deepcopy(records)
        self.position = 0
        self.connected = False

    async def connect(self):
        self.connected = True

    async def list_tools(self):
        if not self.connected:
            raise MCPConnectionError('回放尚未打开')
        names = sorted({record['tool'] for record in self.records})
        return {'tools': [{'name': name} for name in names]}

    async def call_tool(self, name: str, arguments: dict):
        validate_tool(name, arguments)
        if not self.connected:
            raise MCPConnectionError('回放尚未打开')
        if self.position >= len(self.records):
            raise MCPCallError('回放记录已耗尽')
        record = self.records[self.position]
        if record.get('tool') != name or record.get('arguments', {}) != arguments:
            raise MCPCallError('回放工具顺序或参数不匹配')
        self.position += 1
        return deepcopy(record['result'])

    async def close(self):
        self.connected = False


class StreamableHTTPTransport:
    """只连接用户明确确认的现有 loopback 端点。无扫描、自动启动或安装。"""

    def __init__(self, url: str, *, enabled=False):
        validate_endpoint(url)
        self.url, self.enabled = url, enabled is True
        self._stack = None
        self._session = None
        self._budget = None
        self.server_info = {}

    def validate_live(self):
        if not self.enabled:
            raise MCPPolicyError('Live 默认关闭，需显式启用 --live')
        token = os.environ.get(TOKEN_ENV, '')
        if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
            raise MCPCredentialError('缺少有效 WOA_MCP_TOKEN 环境变量（至少 32 个 ASCII 非空白字符）')

    async def _guard_request(self, request):
        actual, expected = validate_endpoint(str(request.url)), validate_endpoint(self.url)
        def origin_and_path(parsed):
            return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), parsed.path
        if origin_and_path(actual) != origin_and_path(expected):
            raise MCPPolicyError('拒绝向指定端点以外发送认证请求')

    async def _guard_response(self, response):
        if response.status_code in (401, 403):
            raise MCPAuthenticationError('MCP Bearer 认证失败（401/403）')
        if 300 <= response.status_code < 400:
            raise MCPPolicyError('MCP Live 不允许 HTTP 重定向')

    async def connect(self):
        self.validate_live()
        if self._stack is not None:
            raise MCPConnectionError('HTTP transport 已打开')
        try:
            # 可选集成基于本机只读核实的 MCP SDK 2.3 API；离线模式不会导入 SDK。
            from .http_limits import HttpResponseBudget
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            import httpx2
        except ImportError:
            raise MCPConnectionError('HTTP 模式需要另行准备可选 MCP SDK；本程序不会自动安装') from None
        if version('mcp') != SDK_VERSION or version('httpx2') != HTTP_VERSION:
            raise MCPConnectionError('Live 要求锁定的 mcp==2.3.0 与 httpx2==2.16.0；请 uv sync --extra live --locked')
        self._budget = HttpResponseBudget(
            MCP_HTTP_BYTES, sse_bytes=MCP_EVENT_BYTES, total_bytes=MCP_SESSION_BYTES,
            sse_events=MCP_SSE_EVENTS, requests=MCP_HTTP_REQUESTS,
        )
        self._stack = AsyncExitStack()
        http = await self._stack.enter_async_context(httpx2.AsyncClient(
            headers={'Authorization': 'Bearer ' + os.environ[TOKEN_ENV], 'Accept-Encoding': 'identity'},
            trust_env=False, follow_redirects=False,
            timeout=httpx2.Timeout(30.0, connect=5.0),
            event_hooks={'request': [self._guard_request, self._budget.guard_request],
                         'response': [self._guard_response, self._budget.guard_response]}))
        # 同一任务进入/退出 SDK 上下文，避免 task-group cancel-scope 跨任务。
        streams = await self._stack.enter_async_context(
            streamable_http_client(self.url, http_client=http, terminate_on_close=True,
                                   max_sse_event_size=MCP_EVENT_BYTES))
        self._session = await self._stack.enter_async_context(ClientSession(streams[0], streams[1]))
        result = await self._checked(self._session.initialize())
        if result is not None:
            self.server_info = result.model_dump(by_alias=True, exclude_none=True)

    def _check_budget(self):
        if self._budget is not None:
            try:
                self._budget.check()
            except ResourceLimitError as exc:
                raise MCPResourceError(str(exc)) from None

    async def _checked(self, operation):
        try:
            result = await operation
        except asyncio.CancelledError:
            raise
        except Exception:
            # SDK tasks can wrap a limit error in a protocol/transport failure.
            self._check_budget()
            raise
        self._check_budget()
        return result

    async def list_tools(self):
        if self._session is None:
            raise MCPConnectionError('HTTP 尚未连接')
        from mcp.types import PaginatedRequestParams
        tools, names, cursors = [], set(), set()
        cursor = None
        for _ in range(MCP_TOOL_PAGES):
            page = await self._checked(self._session.list_tools(params=PaginatedRequestParams(cursor=cursor)))
            if len(page.tools) > MCP_TOOL_PAGE:
                raise MCPResourceError('资源限制：mcp_tool_page_limit')
            if len(tools) + len(page.tools) > MCP_TOOLS_TOTAL:
                raise MCPResourceError('资源限制：mcp_tools_total_limit')
            for tool in page.tools:
                if not tool.name or tool.name in names:
                    raise MCPPolicyError('工具发现返回重复或无效工具名称')
                names.add(tool.name)
                tools.append(tool.model_dump(by_alias=True, exclude_none=True))
            cursor = page.next_cursor
            if cursor is None:
                return {'tools': tools}
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise MCPPolicyError('工具发现返回重复或无效游标')
            cursors.add(cursor)
        raise MCPResourceError('资源限制：mcp_tool_pages_limit')

    async def call_tool(self, name: str, arguments: dict):
        validate_tool(name, arguments)
        if self._session is None:
            raise MCPConnectionError('HTTP 尚未连接')
        result = await self._checked(self._session.call_tool(name, arguments))
        return result.model_dump(by_alias=True, exclude_none=True)

    async def close(self):
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            try:
                await stack.aclose()  # 终止本客户端会话，不终止 Server 进程。
            finally:
                self._check_budget()
