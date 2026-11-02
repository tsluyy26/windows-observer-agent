"""只读 Mock 接口与 MCP 通信策略；没有进程启动代码。"""
from copy import deepcopy
import asyncio
import math
import json
from pathlib import Path
from typing import Any, Protocol

from .models import Observation


class ObservationClient(Protocol):
    """未来适配器负责将真实工具结果转换成内部 Observation。"""

    def collect(self) -> Observation:
        """采集观察；不提供任何桌面操作接口。"""
        ...


class MockClient:
    def __init__(self, path: Path | None = None, *, data: dict[str, Any] | None = None):
        if path is not None and data is not None:
            raise ValueError('Mock path 与 data 不能同时指定')
        self.path = path if path is not None else Path(__file__).parent / 'mock_data' / 'normal.json'
        self.data = data

    def read_mock(self) -> dict[str, Any]:
        payload = deepcopy(self.data) if self.data is not None else json.loads(
            self.path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('mock') is not True:
            raise ValueError('仅接受带有 mock: true 的 Mock 数据')
        if payload.get('schema') != 'woa.mock.v1':
            raise ValueError('不支持的 Mock schema，要求 woa.mock.v1')
        return payload

    def collect(self) -> Observation:
        data = self.read_mock()
        source = 'Mock:memory' if self.data is not None else 'Mock:' + str(self.path.resolve())
        return Observation(
            mock=True, observed_at=data.get('observed_at'),
            snapshot=deepcopy(data.get('snapshot')) if isinstance(data.get('snapshot'), dict) else {},
            control_status=deepcopy(data.get('control_status')) if isinstance(data.get('control_status'), dict) else {},
            source=source,
        )


ALLOWED_TOOLS = frozenset({'ControlStatus', 'Snapshot', 'Screenshot'})
TOOL_ARGUMENTS = {
    'ControlStatus': frozenset(),
    'Snapshot': frozenset({'use_vision', 'use_dom', 'use_annotation', 'use_ui_tree',
                          'width_reference_line', 'height_reference_line', 'display', 'region'}),
    'Screenshot': frozenset({'use_annotation', 'width_reference_line',
                            'height_reference_line', 'display', 'region'}),
}


class MCPError(ValueError):
    """对调用方稳定、无原始敏感内容的通信错误。"""


class MCPPolicyError(MCPError):
    pass


class MCPConnectionError(MCPError):
    pass


class MCPAuthenticationError(MCPConnectionError):
    pass


class MCPTimeoutError(MCPError):
    pass


class MCPCallError(MCPError):
    def __init__(self, message: str, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class ToolTransport(Protocol):
    async def connect(self) -> None: ...
    async def list_tools(self) -> dict: ...
    async def call_tool(self, name: str, arguments: dict) -> dict: ...
    async def close(self) -> None: ...


def safe_mcp_error(exc: BaseException, *, connecting=False) -> MCPError:
    """SDK 的 task group 可能包装异常；只提取项目自己的安全错误类型。"""
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            error = safe_mcp_error(item, connecting=connecting)
            if isinstance(error, (MCPAuthenticationError, MCPPolicyError, MCPTimeoutError)):
                return error
    if isinstance(exc, TimeoutError):
        return MCPTimeoutError('MCP 连接/初始化超时' if connecting else 'MCP 调用超时')
    return (MCPConnectionError('MCP HTTP 连接/初始化失败') if connecting else
            MCPCallError('MCP HTTP 通信或工具返回失败'))


def validate_tool(name: str, arguments: dict) -> None:
    if name not in ALLOWED_TOOLS or not isinstance(arguments, dict):
        raise MCPPolicyError('工具不在只读白名单或参数不是对象')
    if not arguments.keys() <= TOOL_ARGUMENTS[name]:
        raise MCPPolicyError('工具含有未允许的参数')
    for key, value in arguments.items():
        if key.startswith('use_'):
            valid = type(value) is bool or (isinstance(value, str) and value.lower() in ('true', 'false'))
        elif key in ('width_reference_line', 'height_reference_line'):
            valid = value is None or (type(value) is int and value > 0)
        elif key == 'display':
            valid = value is None or (isinstance(value, list) and all(type(v) is int and v >= 0 for v in value))
        else:  # region 的字符串形式只接受 JSON 数字坐标，不接受任意文本。
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    raise MCPPolicyError('region 必须为 JSON 坐标') from None
            valid = value is None or (isinstance(value, list) and len(value) == 4
                and all(type(v) is int for v in value) and value[0] < value[2] and value[1] < value[3])
        if not valid:
            raise MCPPolicyError('工具参数类型或取值无效：' + key)


class MCPClient:
    """异步、顺序使用的工具会话；不创建进程，无重试或自动夺权。"""

    def __init__(self, transport: ToolTransport, *, connect_timeout=5.0,
                 call_timeout=30.0, close_timeout=5.0):
        for value in (connect_timeout, call_timeout, close_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('超时必须为有限正数')
        self.transport = transport
        self.connect_timeout, self.call_timeout, self.close_timeout = connect_timeout, call_timeout, close_timeout
        self.connected = False
        self._entered = False

    async def __aenter__(self):
        if self._entered:
            raise MCPConnectionError('会话不可重复进入，请创建新客户端')
        self._entered = True
        try:
            async with asyncio.timeout(self.connect_timeout):
                await self.transport.connect()
        except BaseException as exc:
            cleanup_error = None
            try:
                await self.close()
            except MCPError as closing:
                cleanup_error = closing
            if isinstance(exc, asyncio.CancelledError):
                # AnyIO 后台网络错误会先取消等待者，真实异常在退出 task group 时出现。
                if cleanup_error is not None:
                    raise cleanup_error from None
                raise
            if isinstance(exc, MCPError):
                raise
            if isinstance(exc, TimeoutError):
                raise MCPTimeoutError('MCP 连接/初始化超时') from None
            if not isinstance(exc, Exception):
                raise
            raise safe_mcp_error(exc, connecting=True) from None
        self.connected = True
        return self

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        arguments = {} if arguments is None else arguments
        validate_tool(name, arguments)
        if not self.connected:
            raise MCPConnectionError('MCP 会话尚未连接或已经关闭')
        from .mcp_adapter import diagnose_tool_failure, normalize_result

        payload = None
        phase = 'call'
        try:
            async with asyncio.timeout(self.call_timeout):
                payload = await self.transport.call_tool(name, deepcopy(arguments))
                phase = 'parse'
                return normalize_result(payload)
        except BaseException as exc:
            cleanup_error = None
            try:
                await self.close()
            except MCPError as closing:
                cleanup_error = closing
            if isinstance(exc, asyncio.CancelledError):
                if cleanup_error is not None:
                    raise cleanup_error from None
                raise
            if isinstance(exc, TimeoutError):
                raise MCPTimeoutError('MCP 工具调用超时：' + name) from None
            if isinstance(exc, MCPError):
                raise
            if not isinstance(exc, Exception):
                raise
            details = diagnose_tool_failure(payload, exc, phase)
            labels = {'control_rejected': '服务端控制保护拒绝', 'server_tool_error': '服务端工具返回错误',
                      'server_capture_error': '服务端截图捕获错误', 'sdk_output_schema': 'SDK 输出合约不匹配',
                      'sse_event_size_limit': 'SSE 消息超过大小上限', 'result_parse_error': '工具结果结构无法解析',
                      'sdk_call_exception': 'SDK 工具调用异常'}
            description = labels[details['category']]
            if details.get('control_code'):
                description += '（' + details['control_code'] + '）'
            raise MCPCallError(description + '：' + name, diagnostics=details) from None

    async def list_tools(self) -> dict:
        if not self.connected:
            raise MCPConnectionError('MCP 会话尚未连接或已经关闭')
        try:
            async with asyncio.timeout(self.call_timeout):
                return await self.transport.list_tools()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise safe_mcp_error(exc) from None

    async def close(self):
        self.connected = False
        try:
            async with asyncio.timeout(self.close_timeout):
                await self.transport.close()
        except TimeoutError:
            raise MCPTimeoutError('MCP 关闭超时') from None
        except MCPError:
            raise
        except Exception as exc:
            raise safe_mcp_error(exc, connecting=True) from None

    async def __aexit__(self, exc_type, exc, tb):
        try:
            await self.close()
        except MCPError:
            if exc is None or isinstance(exc, asyncio.CancelledError):
                raise
