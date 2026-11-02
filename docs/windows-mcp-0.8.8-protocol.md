# 本机 Windows-MCP 0.8.8 协议核查

核查日期：2026-11-01。证据来自本机安装文件和现有 Codex 工具连接，未使用 GitHub main。
只读源码，没有导入/运行 Windows-MCP 包、修改文件、修改注册表或启动/终止服务。

## 版本与证据路径

本机安装环境根目录：
`C:\path\to\windows-mcp-0.8.8\Lib\site-packages`。

- `windows_mcp-0.8.8.dist-info/METADATA`：Name=windows-mcp，Version=0.8.8。
- 同环境：FastMCP 4.1.4、MCP SDK / mcp-types 2.3.0。
- `windows_mcp/tools/snapshot.py`：Snapshot/Screenshot 注册、参数及捕获错误处理。
- `windows_mcp/tools/_snapshot_helpers.py`：文本分区、显示器元数据、图像缩放与 PNG。
- `windows_mcp/tools/control_status.py`、`desktop/control.py`：控制工具及真实状态。
- `windows_mcp/desktop/views.py`：窗口/桌面表格 renderer。
- `windows_mcp/tree/views.py`、`tree/budget.py`：UI 树与可配置截断语义。
- `windows_mcp/__main__.py::_run_server`：stdio/HTTP 服务入口。
- `fastmcp/tools/base.py`：工具返回值到文本/结构化内容的转换。
- `mcp_types/_types.py::CallToolResult`：SDK 结果类型。
- `mcp/client/stdio.py`、`streamable_http.py`、`session.py`：客户端连接与生命周期。

用于复核版本漂移的 SHA256：

| 安装文件 | SHA256 |
| --- | --- |
| tools/snapshot.py | 73E11967C646A5E9D6C8D6155B985B3B3E914000B544979B059F66B2DA4DC58C |
| tools/control_status.py | 7F90B95963EA5288DC7C6D0DBFA8E534FB6EF1CC2FCDB27F4EC0311E9BF254B5 |
| tools/_snapshot_helpers.py | 0B21A2E12489FAD5AEC8E7743F5F495EB71B3BCA6CF30A3243712539CD6820EF |

现有工具的参数与安装源码一致，但本轮没有重新 handshake 读取现有连接的 serverInfo，
所以“安装包版本已确认”和“现有工具连接实测结果”作为两种证据分别记录。

## 真实参数

以下是 Python 工具函数公开参数；`ctx` 是 FastMCP 注入的上下文，不属于调用参数。

| 工具 | 参数 | 默认值 |
| --- | --- | --- |
| ControlStatus | 无参数 | `{}` |
| Snapshot | use_vision: bool 或 str | false |
| Snapshot | use_dom: bool 或 str | false |
| Snapshot | use_annotation: bool 或 str | true |
| Snapshot | use_ui_tree: bool 或 str | true |
| Snapshot | width_reference_line: int 或 null | null |
| Snapshot | height_reference_line: int 或 null | null |
| Snapshot | display: list[int] 或 null | null |
| Snapshot | region: list[int]、JSON 字符串或 null | null |
| Screenshot | use_annotation: bool 或 str | false |
| Screenshot | width_reference_line: int 或 null | null |
| Screenshot | height_reference_line: int 或 null | null |
| Screenshot | display: list[int] 或 null | null |
| Screenshot | region: list[int]、JSON 字符串或 null | null |

display 为零起始显示器索引，region 为虚拟桌面 `[left, top, right, bottom]`，region 优先。
源码 `_as_bool` 只将 true 或忽略大小写的字符串 "true" 当作真。
`use_dom=True` 且 `use_ui_tree=False` 被服务拒绝。Screenshot 固定启用 vision、跳过 UI 树。
项目策略比服务更严格：校验布尔字符串、正数网格、显示器索引及有面积的整数矩形。

## 返回 envelope 与内容

MCP wire 字段为 `content`、可选 `structuredContent`、`isError`；本机 SDK 的模型字段
也可能以 `structured_content`、`is_error` 输出。适配器接受两者，冲突时失败。
也支持 JSON-RPC `result` envelope，协议级 `error` 则失败。

ControlStatus 实测同时包含一个 JSON TextContent 和 structuredContent，内容一致：

```json
{"state":"ready","generation":13,"wait_seconds":0}
```

源码状态包括 `unavailable`、`ready`、`user`、`takeover_pending`、`ai`。
只有有效 `ai` 才映射内部 agent，ready 只表示可用状态，不代表已拥有控制权。
`generation` 为控制代次，`wait_seconds` 是用户空闲等待时间；查询成功不等于允许行动。
适配器不申请控制、不调用控制切换工具。历史回放的控制状态一律不能作为当前许可。

Snapshot 文本包括 Cursor/显示器元数据、Active Desktop、All Desktops、Focused Window、
Opened Windows、UI Tree。窗口为 Name/Depth/Status/Width/Height/Handle 的 simple 表格，
**没有 exe 路径或进程名字段**，也没有可靠桌面编号；不从标题推断这些信息。
UI Tree 是 unicode 语义树，可含窗口、容器、坐标、控件类型、名称、action 和元数据。
`action: click` 只是渲染器提示，不是已验证点击。

关键实测差异：Snapshot(use_vision=false) 的一个文本块内容是 JSON 序列化的
`list[str]`，必须解码一层；Screenshot 因含 Image 对象，实测为普通文本块加 ImageContent。
因此源码中的 Python 返回 `list` 不等于 MCP 的 `content` 数组。

图像为 image/png；可能缩放，文本可能包含 Screenshot Original Size、Coordinate Scale。
本轮不解析图像坐标、不恢复图片、不保存 base64；仅保留 MIME 与 omitted=true。
正常未知 envelope 字段保留在内存模型；落盘脱敏时使用允许列表并丢弃未知字段。

## 错误与截断

错误分三层处理：

1. 连接/调用异常与超时，转换为不包含原始敏感异常文本的 MCPError 子类。
2. JSON-RPC error、isError=true 或畸形错误标记，视为失败。
3. 工具捕获异常后可能返回普通字符串列表：
   `Error capturing desktop state: ...` 或 `Error capturing screenshot: ...`。
   即使 isError=false，也视为失败；同时处理 JSON 数组包裹的错误文本。

源码默认 `DEFAULT_MAX_TREE_ELEMENTS=500`，可以由 `WINDOWS_MCP_MAX_TREE_ELEMENTS` 改变。
实际截断文本含 `... [truncated: reached the N-element capture limit ...]`。
本项目提取 N，不对真实结果机械套用 Mock 的 500 计数。

没有该标记仍不能证明完整：语义树为空、窗口失败、跳过树等情况都可能不携带完整性证据。
所以 `truncation_reported` 记录显式标记，`completeness` 为 truncated/unknown；
Scene 的 `ui_tree_truncated` 继续表示“可能截断”，未知时保守为 true。

## stdio、HTTP 和避免第二实例

本机 SDK `stdio_client` 明确调用 `_create_platform_compatible_process` 启动子进程。
不能将其当作附着到 Codex 已有 stdio 流的办法；本项目没有 stdio transport。

安装版 `_run_server` 对 stdio 调用 `mcp.run(transport='stdio')`；对 streamable-http
调用同一服务的 HTTP 运行入口，支持鉴权中间件及可选 stateless_http。
这些是**服务端启动代码**，本轮完全不调用它们。

独立客户端若使用 HTTP，只能连接已由用户确认存在的服务端点。
本机 SDK 的 `streamable_http_client(url)` 管理 HTTP/SSE 流，ClientSession 执行 initialize；
离开上下文发送会话终止请求（有 session id 时），不终止 Server 进程。
本项目的 transport 通过 AsyncExitStack 管理流和会话，连接/初始化、调用、关闭分别限时。
不扫描端口、不探测端点、不重试启动、不修改 Codex 配置。

本轮 HTTP 仅以假 SDK/内存对象验证 API 调用和清理顺序，**没有建立真实 HTTP 连接**。
可选 SDK 不安装到本项目，离线模块不会导入它；运行时需要另行准备兼容 MCP SDK 2.3。
当前未实现认证参数配置及其他 SDK 版本适配，不能据此宣称 HTTP 已完成真实联调。

## 实测与脱敏回放来源

使用现有连接顺序调用 ControlStatus → Snapshot(vision=false, dom=false, ui_tree=true)
→ Screenshot(annotation=false, region=[0,0,1,1])。没有执行鼠标键盘或控制切换。

保存到 `tests/fixtures/windows_mcp_0_8_8_redacted.json` 的只是必要脱敏结构：
标题/桌面名称/控件名称替换，句柄换成局部编号，属性值/正文/未知行丢弃，图像 data 丢弃。
首次实测未单独计时，最终 fixture 已通过同一连接重新采样，逐次记录 UTC started_at
与 observed_at，并注明 client-received-not-server-capture；recorded_at 是脱敏存档时间。
最终重新采样时 ControlStatus 为 user，Snapshot/Screenshot 返回 isError=true，
因此该 fixture 验证真实错误结果的保守回放，不声称是成功桌面快照。没有申请控制权。
首次成功调用核实的 JSON 数组文本、普通文本和图像块结构继续由内存合约测试覆盖。
首轮采集端 fixture 的句柄别名索引错误已更正，错误 fixture 不作为成功解析证据。

这份 fixture 是真实返回结构的有损脱敏回放，不是原始桌面、不是实时观察、也不是完整快照。
其他测试在内存中构造的样本是合约测试样本，不冒充真实捕获。
