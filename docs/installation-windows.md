# Windows 安装与使用指南

适用版本：Windows Observer Agent v0.4.1。面向 Windows 11 / PowerShell 用户，推荐先完成离线体验，再决定是否配置模型或真实桌面观察。

> 验证范围（2026-11-02）：本指南依据真实代码、pyproject.toml、uv.lock、CLI 与本机 UV 0.13.6 帮助整理。已有环境中实际检查了六种离线 CLI 用法；**没有在干净 Windows 上安装 UV/Python、创建 .venv 或下载依赖**。下面安装步骤是待验收的候选流程，不能当成干净机安装成功记录。最近完整测试 151/151 来自前一轮，本轮没有重复运行全套。

## 目录

1. [Windows 环境准备](#1-windows-环境准备)
2. [安装 Python 和 UV](#2-安装-python-和-uv)
3. [获取项目源码](#3-获取项目源码)
4. [创建运行环境](#4-创建运行环境)
5. [安装项目依赖](#5-安装项目依赖)
6. [首次启动与离线体验](#6-首次启动与离线体验)
7. [运行自动化测试](#7-运行自动化测试)
8. [配置模型 Provider](#8-配置模型-provider)
9. [常见错误排查](#9-常见错误排查)
10. [安全配置 API Key](#10-安全配置-api-key)
11. [可选：真实桌面观察的边界](#11-可选真实桌面观察的边界)
12. [命令依据与验证状态](#12-命令依据与验证状态)

## 1. Windows 环境准备

- 已有真实联调记录的系统是 Windows 11，其他 Windows 版本未建立支持矩阵。
- 建议使用 PowerShell 7。脚本入口为 pwsh，Windows PowerShell 5.1 不是同一个程序；完整测试中的脚本检查需要 PowerShell 7。
- Windows Terminal 只是终端窗口，不会替你安装 Python、UV 或 PowerShell。
- 离线 Mock、Replay、规则规划、Mock LLM 和配置验证不要求 Windows-MCP；模型 SDK 和真实 MCP 服务分别按需准备。

查看当前 PowerShell 及命令是否可找到：

~~~powershell
$PSVersionTable.PSVersion
Get-Command pwsh, uv -ErrorAction SilentlyContinue
~~~

PowerShell 安装方法可参考 [Microsoft 官方安装说明](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows)。下载与安装属于用户后续联网操作，本轮没有执行。不要为运行本项目关闭系统安全策略或以管理员身份运行未知脚本。

## 2. 安装 Python 和 UV

Python 是运行代码的解释器；UV 管理解释器、项目环境和依赖。推荐使用 UV 保持与锁文件一致，但它不是运行默认业务逻辑必需的 Python 库。

**需要网络，且会安装软件；已有 UV 则跳过安装行。** 以下 WinGet 命令来自 [UV 官方安装文档](https://docs.astral.sh/uv/getting-started/installation/)：

~~~powershell
winget install --id=astral-sh.uv -e
~~~

完成后重新打开 PowerShell：

~~~powershell
uv --version
~~~

如果找不到 uv，先核对安装是否成功、重新打开终端和 PATH；不要默认某个个人缓存路径一定存在。

当前 pyproject 要求 Python >=3.14,<3.15，.python-version 为 3.14.10。首次下载解释器需要网络：

~~~powershell
uv python install 3.14.10
~~~

已有可发现的 Python 3.14.10 可以跳过下载。这里不要求修改全局 Python 默认版本。具体发行包可下载性及全新机器安装仍待验证；失败时保留错误类型，不擅自改成别的主次版本。

## 3. 获取项目源码

公开仓库为 [tsluyy26/windows-observer-agent](https://github.com/tsluyy26/windows-observer-agent)。可在 Code → Download ZIP 下载并解压，或在已有 Git 的 PowerShell 中由用户自行执行（需要网络）：

~~~powershell
git clone https://github.com/tsluyy26/windows-observer-agent.git
Set-Location windows-observer-agent
~~~

这里提供克隆示例，不代表本轮助手执行过命令。维护者确认首次 Windows CI 已通过 151 项离线测试；这不等于普通用户干净 Windows 安装或 wheel/exe 分发已验收。

解压后应看到 README.md、pyproject.toml、uv.lock、windows_observer_agent、tests、examples 和 docs。不要复制维护者的 .venv、私密配置、日志或原始截图。

在 PowerShell 中进入你实际解压的目录；通过输入选择位置，避免把个人电脑路径写进教程：

~~~powershell
$project = Read-Host '输入项目根目录的完整路径'
Set-Location -LiteralPath $project
Test-Path -LiteralPath .\pyproject.toml
Test-Path -LiteralPath .\windows_observer_agent\__main__.py
~~~

两次检查应为 True。Set-Location 只改变当前目录，不安装项目。后续相对路径命令均从这里执行。

## 4. 创建运行环境

已有 3.14.10 后运行以下候选命令。它会创建 .venv，**不是只读检查**：

~~~powershell
uv venv --python 3.14.10 --no-python-downloads
~~~

不要把本项目环境指向其他项目或系统 Python。若 .venv 已存在，先检查其用途，不盲目重建或覆盖。

确认新环境的解释器：

~~~powershell
.\.venv\Scripts\python.exe --version
~~~

预期为 Python 3.14.10。无需激活环境，也无需更改 PowerShell 执行策略；直接运行 python.exe 可以减少“激活了错误环境”的问题。路径由变量保存时，PowerShell 用 & 调用：

~~~powershell
$python = (Resolve-Path -LiteralPath .\.venv\Scripts\python.exe).Path
& $python --version
~~~

UV 的 --no-python-downloads 只禁止自动下载解释器，不等于禁止下载依赖；若要限制包下载，还需另行指定 --offline。

## 5. 安装项目依赖

pyproject.toml 的基础 dependencies 为空。纯规则、Mock/Replay 和 Mock LLM 用标准库即可；为统一环境记录，推荐用锁文件同步。

| 使用场景 | 选择 |
| --- | --- |
| 只体验默认规则/Mock LLM/配置验证 | 基础环境 |
| 连接已准备的 MCP 服务 | 增加 live extra |
| 调用外部模型 | 增加 llm extra |
| 完整 151 项 SDK/脚本验收 | live + llm，并安装 PowerShell 7 |

以下同步可能访问包索引、下载文件或改变 .venv；不是本轮已经执行的步骤：

~~~powershell
# 基础环境
uv sync --locked --no-python-downloads

# 仅外部模型所需依赖（按需选择）
uv sync --extra llm --locked --no-python-downloads

# 保留 Live 与模型 SDK，用于完整测试
uv sync --extra live --extra llm --locked --no-python-downloads
~~~

三条是不同选择，不要求依次执行。sync 默认按选择精确同步，后来只运行基础命令可能移除之前的可选 SDK；需要哪些 extra 就继续显式保留它们。

锁定版本为 mcp==2.3.0、httpx2==2.16.0、openai==3.42.0；客户端也校验对应 SDK 版本。**live extra 安装的是 MCP 客户端 SDK，不会安装或启动 Windows-MCP 0.8.8 服务。**

--locked 要求现有锁文件与元数据一致，否则失败；不自动修锁。已有完整缓存且明确不联网时，可以给所选 sync 命令再加 --offline，但它仍会写环境，缺缓存会失败。

区别：

- uv sync：同步环境，可能创建环境、下载和移除未选依赖。
- uv run：运行程序，默认会先检查/同步环境，不是纯只读运行。
- python -m windows_observer_agent：从当前源码目录加载程序；使用哪个解释器，就使用哪个环境。
- pip install：安装 Python 分发包，不是读取 uv.lock 的替代方法。目前没有经验证的本项目 pip 安装路径。

本项目尚无 [build-system] 或安装后的命令入口，uv.lock 将其标为 virtual 项目；UV 同步依赖不会把当前项目安装成可在任何目录导入的包。因此始终从源码根目录运行。wheel、pip 和 exe 分发仍待单独设计、构建和验证。依据见 [UV 同步](https://docs.astral.sh/uv/concepts/projects/sync/)与[构建系统说明](https://docs.astral.sh/uv/concepts/projects/config/#build-systems)。

## 6. 首次启动与离线体验

先从最简单的命令开始：

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent
~~~

预期输出中文观察报告，来源明确为 Mock，并展示条件计划；它没有读取当前真实桌面，没有真的打开记事本。原始 Mock schema 与真实 Windows-MCP 返回格式不同。

体验 Mock LLM：

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent --planner mock-llm --goal '新建一份空白记事本文档。'
~~~

使用示例配置中的命名 Mock：

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent --planner provider --provider-config .\examples\providers.toml --provider offline
~~~

只验证配置：

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent --provider-config .\examples\providers.toml --validate-provider-config
~~~

应显示“Provider 配置有效”，同时注明未检查密钥或连接。示例中的模型名和远程端点是占位符，验证通过不证明服务可用。

查看参数或 JSON 报告：

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent --help
.\.venv\Scripts\python.exe -B -m windows_observer_agent --json
~~~

脱敏历史回放：

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent --replay-file .\tests\fixtures\windows_mcp_0_8_8_redacted.json
$LASTEXITCODE
~~~

此 fixture 记录 user 状态和工具失败，预期退出 1；这不是安装失败。非 Live 的 0 为 conditional 计划，1 为待确认/不支持/失败，2 为配置或读取错误；Live 退出 0 只表示观察完成。所有计划始终不可执行。

## 7. 运行自动化测试

先按第 5 节准备 live + llm 依赖和 PowerShell 7。测试使用内存 HTTP/Mock/脱敏 Replay，不需要真实模型密钥或 Windows-MCP 服务：

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
~~~

最近一次发布副本验收为 151 项全部通过，没有错误或跳过；本轮没有重跑全套。无 pwsh 或可选 SDK 时会有 skip，不能把带跳过的 OK 当作完整验收。

测试可启动安全的 Python/PowerShell 测试宿主；不启动真实 Windows-MCP。tests/Test-AgentOutput.ps1 只产生合成输出，test_powershell.py 显式传入测试进程的 sys.executable；它不是 scripts/Test-Live.ps1 的真实启动入口。

-B 禁止当前 Python 写字节码；PYTHONDONTWRITEBYTECODE=1 由子进程继承。对于没有项目 .venv 的**只读检查**，可以明确指定已经准备好的解释器，仍从待验收副本根目录运行：

~~~powershell
$python = Read-Host '输入已安装全部测试依赖的 python.exe 完整路径'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw '解释器文件不存在' }
$env:PYTHONDONTWRITEBYTECODE = '1'
& $python -B -m unittest discover -s tests -v
~~~

这种方式不会创建新的环境，但应另检查应用模块来自当前副本；解释器和源码路径是两件事。不要为了“只读验收”使用会同步环境的 uv run。本轮只实际核验少量离线 CLI，没有重复执行上面的全量命令。

## 8. 配置模型 Provider

### 8.1 从公开示例建立个人配置

仅未来需要模型时操作。以下复制命令会创建本地文件，目标已存在就中止，不覆盖：

~~~powershell
if (Test-Path -LiteralPath .\providers.local.toml) { throw '配置已存在，请保留并检查现有内容' }
Copy-Item -LiteralPath .\examples\providers.toml -Destination .\providers.local.toml
~~~

用可信文本编辑器修改副本，保留公开示例不变。默认仍是 offline，可通过 --provider 明确选择其他条目。不要在 TOML 写 api_key、Authorization 或运行时授权；配置只保存 api_key_env 这个环境变量名称。

示例内三个名字：

| 配置名 | Adapter | 含义 |
| --- | --- | --- |
| offline | mock | 可立即离线运行 |
| responses_example | openai_responses | 官方 Responses 地址，模型是待替换占位符 |
| compatible_example | openai_chat_completions | example.com 虚构端点和模型，不能直接用于真实请求 |

每个服务/模型建立独立命名配置。model、timeout、max_output_tokens 写在 TOML；命名模式拒绝 --model 和 --model-timeout 覆盖。

### 8.2 声明模型实际能力

Responses 要求 responses_api=true；Chat 要求 chat_completions_api=true。按实际模型选择 structured_output、json_mode；前者启用 schema 格式，后者启用 JSON 模式，两者均不支持时请求文本 JSON并由本地严格解析。

除 text_input 默认 true 外，其他能力默认 false。image_input/tool_calling/streaming 目前只是扩展元数据，实际 LLMRequest 仍禁止图像、工具、流式功能。

支持 timeout 大于 0 且不超过 120 秒，max_output_tokens 为 1–32000。extra_params 可用于有限 JSON 扩展，如温度；不能包含认证或覆盖保留字段。Chat Adapter 发送标准 max_tokens，不兼容该字段的模型需专用 Adapter。不能根据厂商名推断接口能力。

### 8.3 先做本地验证

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent --provider-config .\providers.local.toml --validate-provider-config
~~~

不读取密钥、不解析 DNS、不联网、不采集桌面；也不证明账户余额、模型名、认证或服务端兼容性。

### 8.4 可选真实模型请求：尚未完成联调

下面代码只供用户以后明确确认接收方、费用与脱敏资料共享后使用，**本轮未执行，可能产生费用**。默认观察源仍为 Mock，不会因为开启模型而启动 Windows-MCP。

先修改个人配置中的 compatible_example，使 base_url/model/capabilities 对应实际服务。密钥的安全输入和清理如下，Read-Host 的输入不是命令行字面量：

~~~powershell
$key = Read-Host '输入已确认服务的 API Key（隐藏输入）' -AsSecureString
$env:EXAMPLE_MODEL_KEY = [System.Net.NetworkCredential]::new('', $key).Password
$endpoint = Read-Host '输入个人配置中 compatible_example 的完整 base_url'
try {
    .\.venv\Scripts\python.exe -B -m windows_observer_agent --planner provider --provider-config .\providers.local.toml --provider compatible_example --enable-external-model --confirm-data-sharing --confirm-provider-endpoint $endpoint
} finally {
    Remove-Item Env:EXAMPLE_MODEL_KEY -ErrorAction SilentlyContinue
    $key = $null
}
~~~

api_key_env 必须与 EXAMPLE_MODEL_KEY 一致。环境变量的值会在当前进程和子进程内存中以明文可读，不是安全保险箱；finally 只移除本次进程的环境变量，不删除磁盘文件，也不保证清除所有内存副本。

如果选择 responses_example，用配置声明的 WOA_LLM_API_KEY 输入同样方式的密钥，选择 --provider responses_example。默认官方 https://api.openai.com/v1 不要求自定义端点确认；所有命名外部配置仍要求 --enable-external-model 与 --confirm-data-sharing。

自定义端点必须精确通过 --confirm-provider-endpoint 确认。远程只允许 HTTPS；本地 HTTP 仅 localhost 或 127.0.0.1，并额外加 --allow-local-http。URL 不允许凭据、query 或 fragment，不能关闭 TLS 验证或依赖环境代理。模型服务与 MCP 的地址限制不同：MCP Live 只接受 127.0.0.1。

请求失败、SDK 版本不符或计划校验失败时可能返回 rule-fallback。必须查看 planner、fallback_reason、planning_status，不能用报告出现或退出 0 证明真实模型已成功规划。程序不会自动切换服务商，也不会执行计划。

### 8.5 Custom Adapter 与旧入口

开发者在受信 Python 代码中通过 ProviderRegistry.register 注册实现，complete(request) 返回统一 LLMResponse；配置文件不能指定任意模块导入。自定义代码不是沙箱，Adapter 应自行保证连接安全和资源关闭。详见 [Provider 设计](v0.4.1-design.md)。

旧 --planner openai 仍支持官方 Responses、--model、--model-timeout、--confirm-data-sharing，密钥为 WOA_LLM_API_KEY；此旧入口不使用命名模式的 --enable-external-model，也不支持自定义 base_url。推荐新用户采用命名配置，避免混淆两套参数。

## 9. 常见错误排查

| 现象 | 检查与处理 |
| --- | --- |
| uv / pwsh 找不到 | 确认对应软件已安装并重开终端；用 Get-Command 检查；不要猜个人安装路径 |
| 找不到 Python 3.14.10 | 确认解释器已安装且 UV 可发现；--no-python-downloads 会阻止自动下载 |
| .venv 的 python.exe 不存在 | 安装流程尚未完成；不要复制别人环境，也不要把路径直接改成维护者的电脑路径 |
| No module named windows_observer_agent | 从含 pyproject.toml 的源码根目录运行；当前项目未安装成全局包 |
| --locked 报不一致 | 停止并核对源码与锁文件是否来自同一版本；不要自动重生成锁文件来掩盖问题 |
| 可选 SDK 缺失/版本错误 | 按选择同步锁定 extras；本项目校验固定 SDK，不能随意升级；只读任务中不要安装 |
| 同步后 SDK 消失 | 之前的 sync 未保留所需 extra；下次明确写出所有要保留的 extra |
| 测试出现 skipped | 核对 live/llm extras 和 PowerShell 7；带跳过不等于完整 151 项验收 |
| Provider 配置验证成功，实际请求失败 | 本地验证不检查 Key、额度、模型和网络；真实兼容性尚待联调 |
| Provider 配置/授权无效 | 核对选择名、密钥环境变量、共享/外部启用/完整端点确认；不要输出密钥诊断 |
| 模型返回 rule-fallback | 查看固定原因码；规则支持范围有限，不能当作外部模型成功 |
| 脱敏 Replay 退出 1 | 当前 fixture 有意记录失败，不是安装错误 |
| JSON/中文输出显示异常 | 使用 UTF-8 终端/读取器；CLI 已统一输出 UTF-8，别把显示乱码当作观察失败 |
| 自定义工作盘遇到跨盘错误 | 后续安装时可考虑把 UV 缓存和 UV 管理的 Python 目录设在同一本地盘；这是历史规避经验，本轮未复现，勿盲目迁移现有环境 |

不要为了运行而关闭 TLS、绕过桌面控制保护、强制结束旧进程或尝试不同付费模型。

## 10. 安全配置 API Key

- 配置文件只写环境变量名，密钥使用隐藏交互输入；不要把实际值放进 PowerShell 命令、README、截图、日志或聊天。
- 本项目没有自动加载 .env 的代码；仅创建 .env 不会自动设置环境变量。
- providers.local.toml 和 .env 文件已有忽略规则，但不能因此存放并分享明文秘密。公开 .env.example 也必须没有真实凭据。
- 不把密钥设成长期系统环境变量作为本教程默认步骤；只给当前终端/子进程使用，结束后清理或关闭终端。
- 忽略规则不删除文件、不移除已跟踪内容，也不清除已提交的秘密。若确实泄漏，先撤销/轮换凭据，再由用户本人审查仓库处理。
- 模型 API Key 和 MCP Bearer Token 是不同用途，不能混用。默认离线体验都不需要。

## 11. 可选：真实桌面观察的边界

本节是高级使用说明，不是安装验收必做步骤；本轮没有运行任何真实 MCP 操作。Windows-MCP 0.8.8 需单独由用户准备，Agent 不安装、升级或启动它。

连接现有服务需要有效 WOA_MCP_TOKEN（至少 32 个 ASCII 非空白字符）。在已确认服务唯一、认证安全、输入钩子无冲突的前提下，CLI 形态为：

~~~powershell
# 真实桌面读取：仅供后续明确授权时执行，不属于离线快速开始
.\.venv\Scripts\python.exe -B -m windows_observer_agent --live --endpoint http://127.0.0.1:8765/mcp
~~~

它顺序握手/工具发现，再调用 ControlStatus、Screenshot、Snapshot，仅三工具白名单；user、控制器异常或持续拒绝时停止，不抢控制权。截图数据短暂驻留内存，标准化后丢弃原图，不进行视觉分析。

scripts/Test-Live.ps1 则不同：**直接执行会启动真实测试服务，并在结束时清理本轮服务进程树**。它要求发布副本自己的 .venv 及锁定 live 依赖，不会自动复用任意外层测试解释器。只在用户退出 Codex、确认原 stdio 服务已退出后，于独立 PowerShell 7 中运行；不要同时安装两个服务实例的输入钩子。

如未来单独进行手动联调，可交互指定已安装 Windows-MCP 0.8.8 的解释器：

~~~powershell
$serverPython = Read-Host '输入已安装 Windows-MCP 0.8.8 的 python.exe 完整路径'
pwsh -NoProfile -File .\scripts\Test-Live.ps1 -ServerPython $serverPython
~~~

脚本核对版本、不下载服务，生成随机 Bearer Token、只绑定 127.0.0.1:8765，三工具范围由启动参数指定，保留控制保护。live-server.toml 不能当作独立安全启动配置；它须与认证环境变量、绑定和 --tools 参数一起使用。旧实例或端口占用时停止，不自动杀占用者。

成功需同时看到 Live 观察完成 True、LIVE_TEST_PASS，以及服务/监听退出确认。若出现 LIVE_TEST_STOPPED，则观察受保护或不完整；不应绕过保护。退出码 3 或清理未确认时，保留脚本给出的本轮 PID/启动时间，使用任务管理器核对仅本轮服务及子进程和端口归属，不按 Python/MCP 名称批量结束。Ctrl+C 后也应核对，因为不能保证所有中断路径都清理成功。

只有确认本轮服务及监听已退出后再恢复 Codex；无需修改原 MCP 配置。此处没有提供批量终止或关闭控制保护的命令。

## 12. 命令依据与验证状态

| 命令/流程 | 依据 | 本轮验证 |
| --- | --- | --- |
| WinGet 安装 UV | UV 官方安装文档 | 仅核对文档，未安装 |
| uv python install 3.14.10 | .python-version；UV 本地 install --help | 仅核对参数，未下载 |
| uv venv --python ... --no-python-downloads | UV 0.13.6 本地 venv --help | 仅核对参数，未创建环境 |
| uv sync --locked 与 extras | pyproject 可选依赖、uv.lock 元数据；UV sync --help/官方文档 | 静态核对，未同步或安装 |
| 默认 / Mock LLM / offline Provider | __main__.py 参数和分支 | 借用已有解释器离线入口实际验证，均退出 0 |
| --validate-provider-config | __main__.py、providers.load_provider_config | 实际验证示例，退出 0，不读密钥或联网 |
| --help | argparse 参数定义 | 实际验证，退出 0 |
| 脱敏 fixture Replay | replay.py、本地 fixture、CLI | 实际验证，退出 1，符合样本预期 |
| unittest 全套 | tests/ 与 unittest 标准发现机制 | 本轮未重跑；上一轮发布副本 151 项全部通过 |
| 外部模型命令 | CLI 授权、ProviderAccess、SDK Adapter | 仅代码核对，无真实请求/计费 |
| Live CLI / 手动服务脚本 | transports.py、live.py、Test-Live.ps1 | 仅代码核对；v0.3 有一次历史真实联调，本轮未运行 |

本轮离线检查覆盖 16 个实际加载的应用模块，全部来自发布副本；未导入 mcp/openai SDK，使用内存审计拒绝网络和进程调用。该结果不能替代干净环境安装、全套测试、真实模型兼容或发行包验收。

返回 [README](../README.md)。
