#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$Port = '8765',
    [string]$ServerPython,
    [switch]$AuthenticationOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

$PublicLiveErrors = @{
    preflight = '检查旧服务或监听端口失败。'
    old_service = '发现旧 Windows-MCP 服务或启动器，请先确认其已退出。'
    codex = 'Codex 桌面应用仍在运行，请先完全退出。'
    port_busy = '指定端口已被占用。'
    interpreter = '无法核对解释器，请用 -ServerPython 指定 Windows-MCP 0.8.8 的 Python。'
    interpreter_missing = '未找到 Windows-MCP 0.8.8 的 Python。请用 -ServerPython 指定。'
    interpreter_ambiguous = '发现多个 Windows-MCP 0.8.8 环境。请用 -ServerPython 指定。'
    dependencies = '项目解释器或锁定 Live 依赖不可用，请检查安装指南。'
    mutex = '无法取得或释放本轮 Live 测试锁。'
    server_environment = '无法构建服务环境。请检查 Windows 系统目录、用户配置目录和本地 Temp 目录。'
    server_auth = '本轮服务认证 Token 无效，服务未启动。'
    job_setup = '无法创建受约束的 Windows Job。'
    launch = '无法在 Windows Job 中创建本轮进程。'
    listener = '无法核对本轮监听者，或服务未及时就绪。'
    auth_missing = '无 Token 探测未通过，请检查拒绝认证的配置。'
    auth_wrong = '错误 Token 探测未通过，请检查拒绝认证的配置。'
    auth_correct = '正确 Token 探测未通过，请检查服务认证及三工具发现。'
    observation = 'Live 观察进程失败。'
    timeout = 'Agent 运行或输出读取超时。'
    cancelled = 'Live 测试已取消。'
    LIVE_CLEANUP_ACCESS_DENIED = 'LIVE_CLEANUP_ACCESS_DENIED：Windows 拒绝访问本轮 Job 或根进程。'
    LIVE_CLEANUP_TIMEOUT = 'LIVE_CLEANUP_TIMEOUT：5 秒内未确认本轮 Job 清空及根进程退出。'
    LIVE_CLEANUP_UNVERIFIED = 'LIVE_CLEANUP_UNVERIFIED：无法确认本轮 Job 清理完成。'
}

function New-LiveFailure([string]$Code, [string]$InternalMessage) {
    $exception = [System.InvalidOperationException]::new($InternalMessage)
    $exception.Data['WOA.LiveError'] = $Code
    return $exception
}

function Get-PublicLiveFailure($Record, [string]$Fallback) {
    $exception = $Record.Exception
    if ($exception -is [OperationCanceledException]) { return $PublicLiveErrors['cancelled'] }
    $code = $exception.Data['WOA.LiveError']
    if ($code -is [string] -and $PublicLiveErrors.ContainsKey($code)) { return $PublicLiveErrors[$code] }
    return $PublicLiveErrors[$Fallback]
}

function Write-AgentSummary([string]$Report, [string]$Errors, [int]$Code) {
    # Each whole field is validated first. No child text is returned verbatim.
    $reportLine = $Report.TrimEnd([char[]]@(13, 10))
    if ($reportLine -cmatch '\AWOA_REPORT (complete|incomplete) (agent|user|ready|takeover_pending|unavailable|none|unknown)\z') {
        $complete = $Matches[1] -ceq 'complete'
        if (($complete -and $Code -eq 0) -or (-not $complete -and $Code -eq 1)) {
            $owner = $Matches[2]
            Write-Host "Live 观察摘要（退出码 $Code）"
            Write-Host "观察完成：$complete"
            Write-Host "控制器：$owner"
        }
    }
    $categories = @{
        mcp_credentials = '缺少有效 WOA_MCP_TOKEN。'
        mcp_authentication = 'MCP 服务拒绝认证。'
        mcp_policy = 'MCP 地址或策略检查失败。'
        mcp_connection = 'MCP 连接或初始化失败。'
        mcp_discovery = '必需的只读工具未通过发现检查。'
        mcp_controller = '控制器拒绝观察。'
        mcp_snapshot = 'Snapshot 观察结果不完整。'
        mcp_tool = 'MCP 工具调用或结果处理失败。'
        mcp_shutdown = 'MCP 会话关闭失败。'
        resource_limit = '输入或响应超过安全限额，或编码不受支持。'
        timeout = '操作超时。'
        cancelled = '观察已取消。'
    }
    if ($Code -ne 0) {
        $line = $Errors.TrimEnd([char[]]@(13, 10))
        $message = '观察不完整或无法判定子进程错误类别。'
        if ($line -cmatch '\AWOA_ERROR live ([a-z_]+)\z' -and $categories.ContainsKey($Matches[1])) {
            $message = $categories[$Matches[1]]
        }
        Write-Host "Live 读取/解析失败：$message"
    }
}

function Get-McpProcesses {
    # 不打印 CommandLine（可能含其他服务的旧凭据）。无法检查时保守中止。
    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    foreach ($item in $all) {
        if ($item.Name -match '^(python(?:w|[0-9.]*)|pyw?|uvx?|windows[-_]mcp)(\.exe)?$' -and -not $item.CommandLine) {
            throw '无法读取某个 Python/UV 进程命令行，不能确认旧 MCP 已退出。'
        }
    }
    return @($all | Where-Object {
        $_.CommandLine -and $_.CommandLine -match '(?i)windows[-_]mcp(?=$|[^a-z0-9_])'
    })
}

function Get-PortListeners([int]$Number) {
    return @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -EQ $Number)
}

function Assert-LivePreflight([int]$Number) {
    if (Get-Process -Name Codex -ErrorAction SilentlyContinue) {
        throw (New-LiveFailure 'codex' 'Codex 桌面应用仍在运行，请先完全退出。')
    }
    $old = @(Get-McpProcesses)
    if ($old.Count -gt 0) {
        $ids = ($old | ForEach-Object ProcessId) -join ','
        throw (New-LiveFailure 'old_service' "发现旧 Windows-MCP/启动器进程 PID=$ids；已中止。")
    }
    if (@(Get-PortListeners $Number).Count -gt 0) {
        throw (New-LiveFailure 'port_busy' "端口 $Number 已被占用；已中止。")
    }
}

function Get-ServerPythonCandidates {
    $cache = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.local\share\uv\cache\archive-v0'
    $metadata = @(Get-ChildItem -Path "$cache\*\Lib\site-packages\windows_mcp-0.8.8.dist-info\METADATA" -File -ErrorAction SilentlyContinue)
    return @($metadata | ForEach-Object {
        Join-Path $_.Directory.Parent.Parent.Parent.FullName 'Scripts\python.exe'
    } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Unique)
}

function Get-LiveServerWindowsFolders {
    return @{
        SYSTEMROOT = [Environment]::GetFolderPath('Windows')
        USERPROFILE = [Environment]::GetFolderPath('UserProfile')
        LOCALAPPDATA = [Environment]::GetFolderPath('LocalApplicationData')
    }
}

function New-LiveServerEnvironment {
    try {
        $folders = Get-LiveServerWindowsFolders
        $environment = [System.Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($name in @('SYSTEMROOT', 'USERPROFILE', 'LOCALAPPDATA')) {
            $folder = $folders[$name]
            if ($folder -isnot [string] -or -not [IO.Path]::IsPathFullyQualified($folder) -or
                -not [IO.Directory]::Exists($folder)) {
                throw 'Required Windows folder unavailable.'
            }
            $environment.Add($name, $folder)
        }
        $temp = [IO.Path]::Combine($environment['LOCALAPPDATA'], 'Temp')
        if (-not [IO.Directory]::Exists($temp)) { throw 'Required Windows temp folder unavailable.' }
        $environment.Add('WINDIR', $environment['SYSTEMROOT'])
        $environment.Add('TEMP', $temp)
        $environment.Add('TMP', $temp)
        return ,$environment
    } catch {
        throw (New-LiveFailure 'server_environment' '无法构建本轮服务的最小 Windows 环境。')
    }
}

function Read-ServerPythonMetadata([string]$Python) {
    $environment = New-LiveServerEnvironment
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Python
    $start.WorkingDirectory = $ProjectRoot
    $start.RedirectStandardOutput = $true
    # Only package metadata is queried. The server module is never imported.
    foreach ($argument in @('-I', '-B', '-c',
        "import json, sys; from importlib.metadata import version; print(json.dumps([version('windows-mcp'), sys.executable, sys._base_executable]))")) {
        $start.ArgumentList.Add($argument)
    }
    $job = New-LiveJob
    $process = $null
    try {
        $process = Start-LiveProcess $job $start -Environment $environment
        $stdout = $process.StandardOutput.ReadToEndAsync()
        if (-not $process.WaitForExit(10000) -or $process.ExitCode -ne 0 -or -not $stdout.Wait(5000)) {
            throw '无法核对 Windows-MCP 0.8.8 解释器信息。'
        }
        return $stdout.Result
    } finally {
        Stop-TestServer $job $process
    }
}

function Resolve-ServerPython([string]$Explicit) {
    if ($Explicit) {
        $candidate = (Resolve-Path -LiteralPath $Explicit -ErrorAction Stop).Path
    } else {
        $candidates = @(Get-ServerPythonCandidates)
        if ($candidates.Count -eq 0) {
            throw (New-LiveFailure 'interpreter_missing' '未找到已安装的 0.8.8 Python 环境，请用 -ServerPython 指定。')
        }
        if ($candidates.Count -gt 1) {
            throw (New-LiveFailure 'interpreter_ambiguous' ("发现 $($candidates.Count) 个候选环境，未自动选择。请用 -ServerPython 显式指定；不会启动、下载或升级服务。`n候选 Python：`n  " + ($candidates -join "`n  ")))
        }
        $candidate = $candidates[0]
    }
    $installed = Read-ServerPythonMetadata $candidate
    try {
        $runtime = @(ConvertFrom-Json -InputObject ($installed -join '') -ErrorAction Stop)
    } catch { throw '无法核对 Windows-MCP 0.8.8 解释器信息。' }
    if ($runtime.Count -ne 3 -or $runtime[0] -cne '0.8.8' -or $runtime[1] -ine $candidate -or
        $runtime[2] -isnot [string] -or -not [IO.Path]::IsPathFullyQualified($runtime[2]) -or
        -not (Test-Path -LiteralPath $runtime[2] -PathType Leaf)) {
        throw '无法确认所选 Windows-MCP 0.8.8 解释器及基础解释器路径。'
    }
    return [pscustomobject]@{ LaunchPath = $candidate; BasePath = $runtime[2] }
}

function Get-TestServerArguments([int]$Number) {
    @('-I','-B','-m','windows_mcp','serve','--transport','streamable-http',
      '--host','127.0.0.1','--port',"$Number",'--config',
      (Join-Path $PSScriptRoot 'live-server.toml'),'--tools','Screenshot,Snapshot,ControlStatus')
}

function New-LiveJob {
    try {
        if (-not ('WindowsObserverAgent.LiveProcessJob' -as [type])) {
            Add-Type -Path (Join-Path $PSScriptRoot 'LiveProcessJob.cs') -ErrorAction Stop
        }
        return [WindowsObserverAgent.LiveProcessJob]::new()
    } catch { throw (New-LiveFailure 'job_setup' '无法创建受约束的 Windows Job；未启动测试服务。') }
}

function Start-LiveProcess($Job, [System.Diagnostics.ProcessStartInfo]$Start,
    [System.Collections.Generic.IDictionary[string,string]]$Environment) {
    try {
        if ($PSBoundParameters.ContainsKey('Environment')) {
            return $Job.Start($Start, $Environment)
        }
        return $Job.Start($Start)
    } catch { throw (New-LiveFailure 'launch' '无法在 Windows Job 中创建本轮进程。') }
}

function Start-TestServer($Job, [string]$Python, [int]$Number, [string]$Secret) {
    if ($Secret -cnotmatch '\A[0-9A-Fa-f]{64}\z') {
        throw (New-LiveFailure 'server_auth' '本轮服务认证 Token 无效。')
    }
    $environment = New-LiveServerEnvironment
    $environment['WINDOWS_MCP_AUTH_KEY'] = $Secret
    $environment['ANONYMIZED_TELEMETRY'] = 'false'
    $environment['WINDOWS_MCP_WATCHDOG'] = 'off'
    $environment['FASTMCP_STREAMABLE_HTTP_PATH'] = '/mcp'
    # FastMCP otherwise reads a .env in the working directory.
    $environment['FASTMCP_ENV_FILE'] = '\\.\NUL'
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Python
    $start.WorkingDirectory = $ProjectRoot
    foreach ($argument in (Get-TestServerArguments $Number)) {
        $start.ArgumentList.Add($argument)
    }
    # 未重定向到读取器的服务输出直接丢弃，以免记录密钥或正文。
    return (Start-LiveProcess $Job $start -Environment $environment)
}

function Get-VerifiedTestListener($Process, [int]$Number, $Interpreter, $Job) {
    if ($Process.HasExited) { throw '本轮服务已退出，无法核对监听身份。' }
    try {
        $listeners = @(Get-PortListeners $Number)
    } catch { throw '无法查询端口监听或进程身份。' }
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -cne '127.0.0.1') {
        throw '监听必须唯一且绑定 127.0.0.1。'
    }
    $owner = $listeners[0].OwningProcess
    $current = $owner
    $executable = [regex]::Escape($Interpreter.LaunchPath)
    # Windows venv launchers retain the selected interpreter in argv[0].
    $expected = '^(?i:"' + $executable + '"|' + $executable + ')'
    foreach ($argument in (Get-TestServerArguments $Number)) {
        $encoded = [regex]::Escape($argument)
        $expected += '\s+(?:"' + $encoded + '"|' + $encoded + ')'
    }
    $expected += '\s*$'

    $seen = [System.Collections.Generic.HashSet[uint32]]::new()
    $chain = [System.Collections.Generic.List[string]]::new()
    $childStarted = [datetime]::MaxValue
    while ($current -gt 0 -and $seen.Add([uint32]$current)) {
        try {
            $entries = @(Get-CimInstance Win32_Process -Filter "ProcessId = $current" -ErrorAction Stop)
        } catch { throw '无法查询监听进程或其父进程。' }
        if ($entries.Count -ne 1) { throw '无法确认监听进程或其父进程。' }
        $entry = $entries[0]
        $path = $entry.ExecutablePath
        if (-not $path -or $path -notin ($Interpreter.LaunchPath, $Interpreter.BasePath) -or
            $entry.CommandLine -cnotmatch $expected -or $entry.CreationDate -isnot [datetime]) {
            throw '监听进程与本轮预期的可执行文件或启动参数不一致。'
        }
        $started = $entry.CreationDate.ToUniversalTime()
        try { $member = $Job.ContainsProcess([uint32]$current, $started) }
        catch { throw '无法读取监听进程的 Windows Job 归属。' }
        if (-not $member) { throw '监听进程身份与本轮 Windows Job 不符。' }
        if ($started -gt $childStarted) { throw '监听进程链创建时间异常。' }
        $childStarted = $started
        $chain.Add(('{0}|{1}|{2}' -f $current, $path.ToUpperInvariant(), $started.Ticks))
        if ($current -eq $Process.Id) {
            # CIM creation timestamps can be rounded to microseconds.
            $difference = ($started - $Process.StartTime.ToUniversalTime()).TotalMilliseconds
            if ($path -ine $Interpreter.LaunchPath -or [math]::Abs($difference) -ge 1 -or $Process.HasExited) {
                throw '监听进程链未匹配本轮启动器。'
            }
            return [pscustomobject]@{ Pid = $owner; Signature = $chain -join ';' }
        }
        $current = $entry.ParentProcessId
    }
    throw '监听端口不属于本轮服务进程链。'
}

function Assert-TestListenerIdentity($Process, [int]$Number, $Interpreter, $Expected, $Job) {
    $current = Get-VerifiedTestListener $Process $Number $Interpreter $Job
    if ($current.Pid -ne $Expected.Pid -or $current.Signature -cne $Expected.Signature) {
        throw '监听进程身份在认证验收期间发生变化。'
    }
}

function Wait-TestServer($Process, [int]$Number, $Interpreter, $Job) {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        if ($Process.HasExited) { throw '测试服务在就绪前退出；未记录服务日志，请核对环境与依赖。' }
        if (@(Get-PortListeners $Number).Count -gt 0) {
            $verified = Get-VerifiedTestListener $Process $Number $Interpreter $Job
            foreach ($other in @(Get-McpProcesses)) {
                try {
                    $member = $other.CreationDate -is [datetime] -and
                        $Job.ContainsProcess($other.ProcessId, $other.CreationDate.ToUniversalTime())
                } catch { throw '无法核对 MCP 进程的 Windows Job 归属，已中止。' }
                if (-not $member) { throw '启动期间出现其他或无法核实的 MCP 实例，已中止。' }
            }
            Write-Host ('本轮已核实监听 PID=' + $verified.Pid + '；启动时间=' + $Process.StartTime.ToString('o'))
            return $verified
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw '测试服务 30 秒内未监听指定端口。'
}

function Invoke-AgentProcess([System.Diagnostics.ProcessStartInfo]$Start, $Job, [switch]$Summary) {
    $Start.RedirectStandardOutput = $true
    $Start.RedirectStandardError = $true
    $Start.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $Start.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    $process = Start-LiveProcess $Job $Start
    try {
        # Drain both pipes even when the caller only needs the exit status.
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(120000) -or -not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) {
            throw (New-LiveFailure 'timeout' 'Agent 运行或输出读取超时。')
        }
        $code = $process.ExitCode
        if ($Summary) {
            Write-AgentSummary $stdout.GetAwaiter().GetResult() $stderr.GetAwaiter().GetResult() $code
        }
        return $code
    } finally {
        $process.Dispose()
    }
}

function Invoke-TestAgent([string]$Python, [int]$Number, [string]$Secret, $Job) {
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Python
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WorkingDirectory = $ProjectRoot
    foreach ($argument in @('-B','-m','windows_observer_agent','--live','--endpoint',"http://127.0.0.1:$Number/mcp",'--manual-summary')) {
        $start.ArgumentList.Add($argument)
    }
    $start.Environment['WOA_MCP_TOKEN'] = $Secret
    $start.Environment['PYTHONUTF8'] = '1'
    return (Invoke-AgentProcess $start $Job -Summary)
}

function Invoke-TestAuthProbe([string]$Python, [int]$Number,
    [ValidateSet('missing', 'wrong', 'correct')][string]$Mode, [string]$Secret, $Job) {
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Python
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WorkingDirectory = $ProjectRoot
    foreach ($argument in @('-B','-m','windows_observer_agent.live_auth',"$Number",$Mode)) {
        $start.ArgumentList.Add($argument)
    }
    $start.Environment.Remove('WOA_MCP_TOKEN') | Out-Null
    if ($Mode -eq 'correct') { $start.Environment['WOA_MCP_TOKEN'] = $Secret }
    $start.Environment['PYTHONUTF8'] = '1'
    try {
        $code = Invoke-AgentProcess $start $Job
    } catch { throw (New-LiveFailure "auth_$Mode" "认证探测进程失败（$Mode）。") }
    if ($code -ne 0) { throw (New-LiveFailure "auth_$Mode" "服务端认证验收失败（$Mode）。") }
}

function Invoke-TestLiveChecks([string]$Python, $Process, [int]$Number, $Interpreter, $Identity,
    [string]$Secret, [bool]$OnlyAuth, $Job) {
    foreach ($mode in @('missing', 'wrong', 'correct')) {
        Assert-TestListenerIdentity $Process $Number $Interpreter $Identity $Job
        if ($mode -eq 'correct') {
            Invoke-TestAuthProbe $Python $Number $mode $Secret $Job
        } else {
            Invoke-TestAuthProbe $Python $Number $mode -Job $Job
        }
    }
    Assert-TestListenerIdentity $Process $Number $Interpreter $Identity $Job
    Write-Host 'LIVE_AUTH_PASS：无 Token 与错误 Token 均被拒绝；正确 Token 初始化成功并发现三项白名单工具。'
    if ($OnlyAuth) { return 0 }

    Write-Host '请停止鼠标键盘活动，12 秒后进行一次只读观察；不会申请或绕过控制权。'
    Start-Sleep -Seconds 12
    Assert-TestListenerIdentity $Process $Number $Interpreter $Identity $Job
    Invoke-TestAgent $Python $Number $Secret $Job
}

function Stop-TestServer($Job, $Process) {
    try {
        try { $stopped = $Job.Stop(5000, $Process) }
        catch {
            $failure = $_.Exception.GetBaseException()
            if ($failure -is [System.ComponentModel.Win32Exception] -and $failure.NativeErrorCode -eq 5) {
                throw (New-LiveFailure 'LIVE_CLEANUP_ACCESS_DENIED' 'Windows 拒绝访问本轮 Job 或根进程。')
            }
            throw (New-LiveFailure 'LIVE_CLEANUP_UNVERIFIED' '无法验证本轮 Job 或根进程已退出。')
        }
        if (-not $stopped) { throw (New-LiveFailure 'LIVE_CLEANUP_TIMEOUT' '5 秒内未确认本轮 Job 清空及根进程退出。') }
    } finally {
        # 即使终止或查询失败，也释放本轮 Job。关闭最后的句柄是独立的兜底。
        try { $Job.Dispose() }
        finally { if ($null -ne $Process) { $Process.Dispose() } }
    }
}

function Confirm-TestServerCleanup($Job, $Process, [int]$Number) {
    $jobStopped = $false
    try {
        Stop-TestServer $Job $Process
        $jobStopped = $true
    } catch {
        Write-Host (Get-PublicLiveFailure $_ 'LIVE_CLEANUP_UNVERIFIED')
    }

    try {
        if (@(Get-PortListeners $Number).Count -gt 0) {
            Write-Host 'LIVE_CLEANUP_PORT_IN_USE：端口仍在监听，归属待人工核对；未终止监听进程。'
            return $false
        }
        if (@(Get-McpProcesses).Count -gt 0) {
            Write-Host 'LIVE_CLEANUP_UNVERIFIED：发现其他或无法核实的 MCP 进程，请人工核对。'
            return $false
        }
    } catch {
        Write-Host 'LIVE_CLEANUP_UNVERIFIED：无法查询清理后的端口或 MCP 进程。'
        return $false
    }
    if ($jobStopped) { Write-Host 'LIVE_CLEANUP_PASS：本轮 Job 已清空、根进程已退出，监听端口已释放。' }
    return $jobStopped
}

function Invoke-ContainedLiveTest([int]$Number, $ServerInterpreter, [string]$AgentPython, [bool]$OnlyAuth) {
    $serverProcess = $null
    $job = $null
    $secret = $null
    $exitCode = 2
    $stage = 'job_setup'
    try {
        $secret = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
        $job = New-LiveJob
        $stage = 'launch'
        $serverProcess = Start-TestServer $job $serverInterpreter.LaunchPath $Number $secret
        Write-Host "本轮服务启动器 PID=$($serverProcess.Id)，只绑定 127.0.0.1:$Number。"
        $stage = 'listener'
        $identity = Wait-TestServer $serverProcess $Number $serverInterpreter $job
        $stage = 'observation'
        $exitCode = Invoke-TestLiveChecks $agentPython $serverProcess $Number $serverInterpreter $identity $secret $OnlyAuth $job
        if (-not $OnlyAuth) {
            if ($exitCode -eq 0) { Write-Host 'LIVE_TEST_PASS：观察成功，计划仍不执行。' }
            elseif ($exitCode -eq 1) { Write-Host 'LIVE_TEST_STOPPED：观察不完整或控制保护拒绝，请查看报告。' }
            else { Write-Host 'LIVE_TEST_FAILED：连接、认证或环境检查失败。' }
        }
    } catch {
        Write-Host ('测试中止：' + (Get-PublicLiveFailure $_ $stage))
        $exitCode = 2
    } finally {
        $secret = $null
        if ($null -ne $job -and -not (Confirm-TestServerCleanup $job $serverProcess $Number)) { $exitCode = 3 }
    }
    return $exitCode
}

function Invoke-LiveTest([int]$Number, [string]$Python, [bool]$OnlyAuth) {
    $mutex = $null
    $ownsMutex = $false
    $stage = 'mutex'
    try {
        $mutex = [System.Threading.Mutex]::new($false, 'Local\WindowsObserverAgentLiveTest')
        try { $ownsMutex = $mutex.WaitOne(0) }
        catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { throw '另一个本项目 Live 测试脚本仍在运行，已中止。' }
        $stage = 'preflight'
        Assert-LivePreflight $Number
        $stage = 'interpreter'
        $serverInterpreter = Resolve-ServerPython $Python
        $stage = 'dependencies'
        $agentPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $agentPython)) { throw '缺少项目 .venv，请先按 README 同步依赖。' }
        & $agentPython -I -B -c "from importlib.metadata import version; assert version('mcp')=='2.3.0'; assert version('httpx2')=='2.16.0'" *> $null
        if ($LASTEXITCODE -ne 0) { throw '请先安装锁定 Live 依赖：uv sync --extra live --locked。' }
        $stage = 'preflight'
        Assert-LivePreflight $Number
        return (Invoke-ContainedLiveTest $Number $serverInterpreter $agentPython $OnlyAuth)
    } catch {
        Write-Host ('测试中止：' + (Get-PublicLiveFailure $_ $stage))
        return 2
    } finally {
        if ($null -ne $mutex) {
            try {
                try { if ($ownsMutex) { $mutex.ReleaseMutex() } }
                finally { $mutex.Dispose() }
            } catch { Write-Host $PublicLiveErrors['mutex'] }
        }
    }
}

# 点入仅加载函数，供无服务启动的安全替身测试使用。
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $number = 0
        if (-not [int]::TryParse($Port, [ref]$number) -or $number -lt 1024 -or $number -gt 65535) {
            Write-Host '测试中止：Port 必须是 1024 到 65535 的整数。'
            exit 2
        }
        exit (Invoke-LiveTest -Number $number -Python $ServerPython -OnlyAuth $AuthenticationOnly.IsPresent)
    } catch {
        Write-Host ('测试中止：' + (Get-PublicLiveFailure $_ 'observation'))
        exit 2
    }
}
