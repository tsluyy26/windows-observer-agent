# Windows Observer Agent

[中文](README.md) | [English](README.en.md)

Windows Observer Agent 是一个独立 Python 程序：把桌面观察资料整理成结构化状态，再根据用户目标生成带前置条件、验证方法和失败停止条件的计划。**当前只观察和规划，没有桌面行动执行器。**

你可以先用内置 Mock 数据离线体验，无需 Windows-MCP、模型账户或 API Key。需要真实桌面观察时，程序可显式连接已经准备好的本地 Windows-MCP HTTP 服务；需要模型规划时，可明确选择 Responses API、兼容 Chat Completions 的服务，或在受信 Python 代码中注册自己的 Adapter。

从零开始请读 **[Windows 安装与使用指南](docs/installation-windows.md)**。公开仓库：[tsluyy26/windows-observer-agent](https://github.com/tsluyy26/windows-observer-agent)，默认分支 main。

## 项目特点

- **模块化**：采集、MCP 适配、Scene Model、Planner、Reporter 各有边界。
- **三种观察来源**：内置 Mock、本地脱敏 Replay、默认关闭的真实 Live。
- **多 Provider**：同一规划流程使用统一请求/响应，厂商 SDK 由 Adapter 隔离；配置不会任意导入 Python 模块。
- **离线可用**：默认规则与 Mock LLM 只使用 Python 标准库。
- **计划校验与回退**：模型输出经过本地结构、证据和安全检查；失败时尝试规则规划，不自动切换服务商。
- **规划与执行分离**：所有计划均为 executable=false、execution_permission=not_granted；规划成功不等于操作授权或任务完成。

英文主页不改变程序语言：CLI 与报告当前主要为中文，模型目标缩减使用以中文为主的有限任务词表；不代表已支持通用英文目标。

规则 Planner 目前只支持“新建一份空白记事本文档”等有限等价表述。Mock LLM 是确定性替身，不具备真实模型的通用语言理解。真实模型输入也经过保守任务词缩减，可能丢失细节；不明确时应请求确认。

## 当前状态：v0.4.1

| 项目 | 已确认范围 |
| --- | --- |
| 发布副本最近全量测试 | 151 项通过，0 失败、0 错误、0 跳过；维护者确认 2026-11-02 首次 Push 触发的 Windows CI 已通过同样的 151 项完整离线测试；本轮文档整理未重跑 |
| 真实桌面联调 | 已完成一次 Windows 11 / Windows-MCP 0.8.8 的只读观察，顺序为 ControlStatus → Screenshot → Snapshot |
| 多模型接口 | 已有 Responses / Chat / Custom 架构和 SDK 内存 HTTP 测试；尚未完成真实第三方模型 API 的完整兼容性验证 |
| 图片能力 | 可以接收截图块并在内存处理；没有视觉理解，默认不保存或上传原图 |
| 安装与分发 | 当前从源码目录运行；干净 Windows 安装、wheel、pip 安装路径、exe 均未完成分发验收 |
| 桌面执行 | 未实现正式执行器，不点击、不输入、不执行模型建议的命令 |

## 环境要求

- **Windows 11**：真实桌面观察的已验证系统。没有宣称 Windows 10 或其他系统的支持矩阵。
- **Python 3.14.x**：pyproject.toml 要求 >=3.14,<3.15；.python-version 固定 3.14.10，当前已验证解释器为 3.14.10。
- **UV**：推荐管理解释器、虚拟环境与锁定依赖；不是程序运行时 SDK。本地安装未在项目元数据中限制 UV 版本；CI 固定使用 UV 0.13.6。
- **PowerShell 7**：推荐用于本指南；完整脚本测试需要 pwsh，真实联调脚本要求 PowerShell 7。
- **Windows-MCP 0.8.8（可选）**：只有真实 Live 观察才需要服务；它不是本项目的默认依赖，也不是 Mock/Replay 的前提。live extra 安装的是客户端 SDK，不会替用户安装 Windows-MCP 服务端。
- **网络与账户（可选）**：下载工具/解释器/依赖可能需要网络；外部模型另需密钥、费用确认和数据共享授权。下载完成后的离线模式无需模型服务。

## 安装：从源码运行

以下是根据当前配置和 UV 官方文档整理的候选步骤，**尚未在干净 Windows 环境完整执行**。安装命令会创建环境或下载文件，本次文档任务没有执行它们。

1. 从公开仓库的 Code → Download ZIP 获取源码，或由用户自行使用 Git 克隆；具体命令见安装指南。
2. 安装 UV（需要网络；已安装则跳过），重开 PowerShell 后确认可用：

~~~powershell
winget install --id=astral-sh.uv -e
uv --version
~~~

3. 在 PowerShell 中选择解压后的项目根目录：

~~~powershell
$project = Read-Host '输入含 pyproject.toml 的项目目录'
Set-Location -LiteralPath $project
Test-Path -LiteralPath .\pyproject.toml
~~~

4. 准备解释器和项目环境，再同步基础依赖：

~~~powershell
# 安装 Python：需要网络；已有可发现的 3.14.10 可跳过
uv python install 3.14.10

# 创建本项目 .venv；不允许此命令自动下载解释器
uv venv --python 3.14.10 --no-python-downloads

# 按锁文件同步基础环境；当前基础 dependencies 为空
uv sync --locked --no-python-downloads
~~~

如要运行全部 SDK 测试或使用可选接口，改用：

~~~powershell
uv sync --extra live --extra llm --locked --no-python-downloads
~~~

当前基础 dependencies 为空，以下为锁定的可选依赖版本：mcp==2.3.0、httpx2==2.16.0（live）和 openai==3.42.0（llm）。

sync 是环境同步，可能下载依赖并移除未选中的可选包；反复同步时应保留所需 extra。run 是运行命令，但默认也会同步环境，不能当作只读验收命令。pip install 不等于上述源码运行流程；当前没有经过验证的 pip 安装教程。参见 [UV 同步说明](https://docs.astral.sh/uv/concepts/projects/sync/)。

## 基本使用

先进入项目根目录。以下命令都不激活虚拟环境，直接指定解释器：

~~~powershell
# 默认：Mock 观察 + 规则计划；不会读取真实桌面
.\.venv\Scripts\python.exe -B -m windows_observer_agent

# Mock LLM：无密钥、无模型请求
.\.venv\Scripts\python.exe -B -m windows_observer_agent --planner mock-llm --goal '新建一份空白记事本文档。'

# 只验证 Provider 配置，不读取密钥、不连接服务、不观察桌面
.\.venv\Scripts\python.exe -B -m windows_observer_agent --provider-config .\examples\providers.toml --validate-provider-config

# 显式选择命名的离线 Provider
.\.venv\Scripts\python.exe -B -m windows_observer_agent --planner provider --provider-config .\examples\providers.toml --provider offline

# 查看全部 CLI 参数
.\.venv\Scripts\python.exe -B -m windows_observer_agent --help
~~~

上述 CLI 参数已在现有解释器中通过离线入口检查，但发布副本目前没有自己的 .venv；这不等于以上安装步骤已完成。

可给报告命令加 --json 输出结构化结果。--mock-file、--replay-file、--live 互斥；--endpoint 必须与 --live 同时使用。内置真实脱敏回放记录的是 user 状态及工具失败，以下命令**预期退出码为 1**：

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent --replay-file .\tests\fixtures\windows_mcp_0_8_8_redacted.json
~~~

非 Live 退出码：0 表示生成 conditional 计划，1 表示待确认/不支持/规划失败，2 表示配置或读取错误。Live 的 0 仅表示观察完成，不能据此认定模型规划成功或用户任务完成。

## 模型服务商配置

没有默认付费模型，默认规划器始终是 rule。完整示例见 [examples/providers.toml](examples/providers.toml)，默认选择 offline。

| Adapter | 用途 | 需要什么 |
| --- | --- | --- |
| mock | 确定性离线替身 | 不需要 SDK 或密钥 |
| openai_responses | Responses API | llm extra；明确模型、端点、能力与密钥环境变量 |
| openai_chat_completions | OpenAI 兼容 Chat Completions | llm extra；服务实际支持的字段和能力，不保证任意兼容服务可用 |
| Custom | 受信 Python 代码注册的适配器 | 实现 complete(request) → LLMResponse；配置不能动态导入模块 |

Provider 是一个命名的“服务+模型+能力”配置，Adapter 是把统一请求与服务格式相互转换的实现。可同时配置多个名字；切换模型应选择另一命名条目，不用 --model 覆盖命名配置。

一个虚构的兼容接口配置如下。base_url 和 model 都必须替换为经用户确认的真实服务资料，**不能把真实密钥写进 TOML**：

~~~toml
[llm]
provider = "my_service"
adapter = "openai_chat_completions"

[llm.providers.my_service]
base_url = "https://api.example.com/v1"
model = "example-model"
api_key_env = "EXAMPLE_MODEL_KEY"
timeout = 30
max_output_tokens = 4000

[llm.providers.my_service.capabilities]
text_input = true
chat_completions_api = true
json_mode = true
~~~

建议保存到自己创建的 providers.local.toml；*.local.toml 已列入忽略规则，但这不是保存明文密钥的理由。Responses 配置使用 openai_responses，必须声明 responses_api=true，并按实际模型和接口能力设置 structured_output、json_mode（两者默认 false）。structured_output=true 时优先请求严格 JSON Schema；否则 json_mode=true 时请求 JSON object；两者均为 false 时请求文本 JSON，仍经过本地严格解析与计划校验。两者同时为 true 时以 structured_output 为先。这是客户端已实现的请求分支，不保证所选服务或模型兼容；能力不能仅凭厂商名称猜测。当前请求路径仍只发送文本，不启用图像、工具调用或流式输出。

真实命名 Provider 必须同时使用 --enable-external-model 与 --confirm-data-sharing；自定义端点还需 --confirm-provider-endpoint 精确匹配完整 base_url。只有默认官方 https://api.openai.com/v1 免自定义端点确认；HTTP 只允许显式授权的 localhost/127.0.0.1，并额外要求 --allow-local-http。服务使用 HTTPS 验证，禁止重定向/环境代理；不支持通过配置关闭 TLS。

密钥只从 api_key_env 指定的环境变量读取。安全输入、运行和清理示例见 [安装指南的 Provider 章节](docs/installation-windows.md#8-配置模型-provider)。这些真实请求可能计费，**尚未在本项目中完成实际第三方兼容验证，不属于离线快速开始**。

Chat Adapter 使用 max_tokens 传递预算；不支持该字段、需要特殊认证或不同请求格式的服务应使用专用 Adapter。extra_params 不能覆盖认证、模型、消息、工具、格式、端点等保留字段；额外参数值不会自动经过桌面脱敏，不应放入个人信息。所有 Adapter 最终统一为 LLMResponse，再经本地计划校验；不会静默换厂商。

旧 --planner openai 兼容入口仍存在：使用官方 Responses 地址，要求 --model、--confirm-data-sharing 和 WOA_LLM_API_KEY；它不要求命名入口专用的 --enable-external-model，也不接受自定义 base_url。新配置优先使用命名 Provider。

## 自动化测试

完整验收需要安装 live 与 llm 两个 extra，并具备 PowerShell 7。已准备环境后运行：

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
~~~

当前套件含 151 项；依赖缺失或没有 pwsh 时部分测试会跳过，看到 OK 还必须检查跳过数量。SDK 使用内存 HTTP，PowerShell 测试只调用安全替身/合成输出程序；不会运行真实 Live 入口。没有新环境的只读验收应使用已存在的解释器，参见安装指南，避免 uv run 意外创建环境。

## 项目结构

~~~text
windows-observer-agent/
├── README.md
├── README.en.md
├── pyproject.toml / uv.lock / .python-version / .gitignore
├── windows_observer_agent/
│   ├── __main__.py / models.py
│   ├── mcp_client.py / transports.py / mcp_adapter.py
│   ├── observer.py / live.py / replay.py / control_gate.py
│   ├── scene_model.py / planner.py / reporter.py
│   ├── planning_input.py / llm_planner.py / llm_provider.py
│   ├── providers.py / provider_adapters.py
│   └── mock_data/
├── tests/（Python、PowerShell 替身及脱敏 fixture）
├── examples/providers.toml
├── scripts/Test-Live.ps1 / live-server.toml（仅手动真实联调）
└── docs/（Windows 安装指南与历史设计）
~~~

Scene 保留时间、来源、未知项和截断证据。未观察到窗口不等于不存在；识别到控件不等于验证可点击。设计背景见 [v0.4.1](docs/v0.4.1-design.md)、[v0.4](docs/v0.4-design.md)、[v0.3](docs/v0.3-design.md)、[0.8.8 协议记录](docs/windows-mcp-0.8.8-protocol.md)；历史文档的测试数和限制应按对应版本理解。内部开发记忆不随发布副本提供。

## 安全说明

- Agent 的 Live 客户端只连接明确指定的 127.0.0.1 端点，使用环境变量 WOA_MCP_TOKEN；不自动启动服务、不扫描或回退到 stdio。
- Windows-MCP 工具白名单仅为 ControlStatus、Screenshot、Snapshot。工具按 ControlStatus → Screenshot → Snapshot 的顺序调用；控制器拒绝、用户接管或发生错误时均停止观察，不申请或绕过控制保护。
- scripts/Test-Live.ps1 是会启动并清理真实测试服务的**手动工具**，不是普通启动命令。需要退出现有 Codex 服务、检查旧实例后另行执行，见 [手动 Live 边界](docs/installation-windows.md#11-可选真实桌面观察的边界)。live-server.toml 不能脱离脚本认证与三工具参数单独照抄。
- 默认不上传截图。外部模型仅在明确配置和共享确认后接收允许列表脱敏结构；自由窗口标题、路径、正文和原始图像不进入模型输入。目标缩减可能丢失语义，必须保留不确定性。
- 原始工具文本仍可能存在程序内存中；Live 报告脱敏与模型输入投影不是同一处理层。时间、应用类别、坐标、尺寸及关系等结构性元数据仍可能识别用户活动。终端捕获、自定义输入及第三方留存需要另行审查，不能宣称所有原始文本都已删除或所有数据都不会落盘。
- 第三方模型服务可能按自己的政策记录、保留或处理请求。项目无法控制其内部留存；store=false 只是请求选项，不保证零留存或不用于训练。启用外部 Provider 后，获准发送的数据会离开本机。
- 自定义 Python Adapter 是受信任扩展代码，不具备沙箱隔离；注册不代表安全。不要运行来源不明的 Adapter。
- 桌面文字视为不可信数据；本地校验拒绝伪造证据及危险输出，但不是完整语义安全证明。
- 模型密钥、MCP Token、原始桌面资料和敏感日志不应提交。忽略规则不能撤销已经泄漏的凭据，也不能清除既有历史。
- 不应把当前项目描述为能完全自主操作桌面的 Agent。

## Roadmap（均未完成）

- v0.4.2：在明确服务、费用与数据共享授权后，开展真实模型联调。
- v0.5：视觉理解及单独的图像隐私设计。
- v0.6：受控执行器、逐步授权、重新观察和结果验证。
- 后续：GUI、错误恢复、干净 Windows 安装与发行包验证。离线 Windows CI 已完成首次远程验收。

许可证文件采用 MIT。项目源码仓库现已公开，但尚未创建正式 GitHub Release，也未发布 wheel、exe 等发行包。

## 文档与项目规范

- [贡献指南](CONTRIBUTING.md)
- [更新记录](CHANGELOG.md)
- [安全政策](SECURITY.md)
- [行为准则](CODE_OF_CONDUCT.md)
- [免责声明](DISCLAIMER.md)
- [AI 编程助手规则](AGENTS.md)
- [隐私与数据处理](docs/PRIVACY_AND_DATA_HANDLING.md)

## 许可证

本项目采用 MIT 许可证。完整条款见 [LICENSE](LICENSE) 文件。

第三方依赖和组件仍受各自的许可证及条款约束。

## AI 辅助开发

Windows Observer Agent 由 **tsluyy26** 独立发起、设计和维护。

本项目在开发过程中广泛使用 OpenAI Codex 作为 AI 辅助开发工具，涵盖代码生成、功能实现、重构、测试和文档编写。

项目的整体方向、功能需求、架构决策和开发优先级由维护者决定。

使用 AI 辅助开发不代表 OpenAI 对项目提供官方背书，也不意味着项目所有权转移给 OpenAI。
