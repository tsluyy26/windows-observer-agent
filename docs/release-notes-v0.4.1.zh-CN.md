# Windows Observer Agent v0.4.1

## 项目概览

Windows Observer Agent 把桌面观察整理成结构化场景，根据用户目标生成供审阅的计划。从 Windows 源码目录即可运行，默认使用合成数据离线规划，无需模型账户。

## 主要功能

- 使用 Mock 数据或回放脱敏的历史观察。规则计划包含前置条件、观察证据、验证步骤与停止条件。所有计划均不可执行。
- 显式开启 Live 后，连接已有的本机 Windows-MCP 0.8.8 服务，仅允许 ControlStatus、Screenshot、Snapshot 三个工具。
- 可选择离线 Mock 模型、OpenAI Responses、兼容 Chat Completions 的服务或受信任的 Python Adapter。外部模型需要显式选择及数据共享授权，模型输出在本地校验。

## 安全改进

手动 Live 启动器核对监听进程归属，要求服务端拒绝无 Token 和错误 Token 的请求，再验证正确凭据下的初始化及工具发现。服务端和解释器探测进程使用受限环境，Windows Job Object 管理本轮启动的相关进程。Provider 配置拒绝将 MCP 凭据变量用于模型，内置模型路径检查请求及已解码响应中的凭据暴露。

## 隐私与数据处理

Replay 过滤未知字段和本地输入路径。Live 报告仅保留已识别的控件类型。原始工具数据可能短暂驻留内存，截图不进入报告或模型请求，脱敏后的结构信息仍可能识别桌面。手动启动器关闭已审计的 Windows-MCP 遥测路径，独立部署的服务与外部模型各自决定遥测和留存方式。

## 可靠性改进

HTTP 正文、SSE 帧及会话、工具发现、本地文件和 JSON 结构均有资源上限。受处理的错误使用限定的诊断类别。Screenshot 和 Snapshot 返回后分别复核控制状态及代次，复核失败即丢弃本轮临时采集数据并停止观察。超时或取消后的迟到工具结果会被拒绝。

## 测试记录

本地最近一次全量离线复测 **240/240** 通过，0 失败、0 错误、0 跳过，耗时 35.439 秒。此前一次全量有一项 S8 Job 计数间歇失败，保留原断言后 PowerShell 单跑 23/23 通过。早期 Windows CI 为 151/151。这些是不同阶段的历史记录，本轮冻结没有重新运行测试。

## 已知限制

- 此前完成过一次真实桌面只读联调。本版本没有重新验收真实服务认证、受限环境启动、桌面接管时序及正常或异常关停。
- 服务端已开始的采集可能在接管或客户端取消后继续完成。观察不会授予桌面操作权限，当前没有视觉理解或桌面执行器。
- 干净 Windows 11 安装、真实 Provider 兼容矩阵、wheel/EXE 分发均未验收。独立服务的真实遥测送达与留存未经测量。
- GitHub 私密漏洞报告已确认启用且有公开入口。登录后的研究者表单、跨账户隔离及端到端送达未经实测。

## 安装

解压源码归档，在包含 `pyproject.toml` 的目录打开 PowerShell，使用 UV 和 Python 3.14.10。首次准备环境可能需要联网。默认 Mock 用法如下。

```powershell
uv python install 3.14.10
uv sync --locked --python 3.14.10 --no-python-downloads
.\.venv\Scripts\python.exe -B -m windows_observer_agent
```

如需可选客户端和模型 SDK，可用 `uv sync --locked --extra live --extra llm`。Live extra 不会安装 Windows-MCP 服务端。GitHub 自动生成的归档仅含源码。

## 许可证

MIT License。Copyright (c) 2026 tsluyy26。第三方组件保留各自的许可证。
