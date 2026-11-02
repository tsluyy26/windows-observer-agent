#Requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [string]$ServerPython
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

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
        throw 'Codex 桌面应用仍在运行，请先完全退出。'
    }
    $old = @(Get-McpProcesses)
    if ($old.Count -gt 0) {
        $ids = ($old | ForEach-Object ProcessId) -join ','
        throw "发现旧 Windows-MCP/启动器进程 PID=$ids；已中止，不会自动终止它们。"
    }
    if (@(Get-PortListeners $Number).Count -gt 0) {
        throw "端口 $Number 已被占用；已中止，不会终止占用进程。"
    }
}

function Get-ServerPythonCandidates {
    $cache = Join-Path $env:USERPROFILE '.local\share\uv\cache\archive-v0'
    $metadata = @(Get-ChildItem -Path "$cache\*\Lib\site-packages\windows_mcp-0.8.8.dist-info\METADATA" -File -ErrorAction SilentlyContinue)
    return @($metadata | ForEach-Object {
        Join-Path $_.Directory.Parent.Parent.Parent.FullName 'Scripts\python.exe'
    } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Unique)
}

function Resolve-ServerPython([string]$Explicit) {
    if ($Explicit) {
        $candidate = (Resolve-Path -LiteralPath $Explicit -ErrorAction Stop).Path
    } else {
        $candidates = @(Get-ServerPythonCandidates)
        if ($candidates.Count -eq 0) {
            throw '未找到已安装的 0.8.8 Python 环境，请用 -ServerPython 指定其 python.exe；不会下载或升级服务。'
        }
        if ($candidates.Count -gt 1) {
            throw ("发现 $($candidates.Count) 个候选环境，未自动选择。请用 -ServerPython 显式指定；不会启动、下载或升级服务。`n候选 Python：`n  " + ($candidates -join "`n  "))
        }
        $candidate = $candidates[0]
    }
    # 仅查询发行包元数据，不导入 Windows-MCP，不会启动服务或安装钩子。
    $installed = & $candidate -I -B -c "from importlib.metadata import version; print(version('windows-mcp'))"
    if ($LASTEXITCODE -ne 0 -or ($installed -join '').Trim() -ne '0.8.8') {
        throw '所选环境不是 Windows-MCP 0.8.8，已中止。'
    }
    return $candidate
}

function Start-TestServer([string]$Python, [int]$Number, [string]$Secret) {
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Python
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WorkingDirectory = $ProjectRoot
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in @('-I','-B','-m','windows_mcp','serve','--transport','streamable-http',
            '--host','127.0.0.1','--port',"$Number",'--config',
            (Join-Path $PSScriptRoot 'live-server.toml'),'--tools','Screenshot,Snapshot,ControlStatus')) {
        $start.ArgumentList.Add($argument)
    }
    foreach ($key in @($start.Environment.Keys | Where-Object { $_ -like 'WINDOWS_MCP_*' })) {
        $start.Environment.Remove($key) | Out-Null
    }
    $start.Environment['WINDOWS_MCP_AUTH_KEY'] = $Secret
    $start.Environment['ANONYMIZED_TELEMETRY'] = 'false'
    $start.Environment['WINDOWS_MCP_WATCHDOG'] = 'off'
    $start.Environment['FASTMCP_STREAMABLE_HTTP_PATH'] = '/mcp'
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $start
    $started = $false
    try {
        $started = $process.Start()
        if (-not $started) { throw '测试服务进程未能启动。' }
        # 异步排空并丢弃日志，既不记录密钥/正文，也不因管道填满而阻塞服务。
        $process.BeginOutputReadLine()
        $process.BeginErrorReadLine()
        return $process
    } catch {
        if ($started -and -not $process.HasExited) { $process.Kill($true) }
        $process.Dispose()
        throw
    }
}

function Wait-TestServer($Process, [int]$Number) {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        if ($Process.HasExited) { throw '测试服务在就绪前退出；未记录服务日志，请核对环境与依赖。' }
        $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $owned = @([uint32]$Process.Id)
        do {
            $before = $owned.Count
            $owned += @($all | Where-Object { $_.ParentProcessId -in $owned -and $_.ProcessId -notin $owned } | ForEach-Object ProcessId)
        } while ($owned.Count -gt $before)
        $listeners = @(Get-PortListeners $Number)
        if ($listeners.Count -gt 0) {
            if (@($listeners | Where-Object { $_.OwningProcess -notin $owned -or $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) {
                throw '端口出现非本轮进程或非 127.0.0.1 监听，已中止。'
            }
            $others = @(Get-McpProcesses | Where-Object { $_.ProcessId -notin $owned })
            if ($others.Count -gt 0) { throw '启动期间出现其他 MCP 实例，已中止；不会终止其他实例。' }
            Write-Host ('本轮已核实服务树 PID=' + ($owned -join ',') + '；启动时间=' + $Process.StartTime.ToString('o'))
            return
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw '测试服务 30 秒内未监听指定端口。'
}

function Invoke-AgentProcess([System.Diagnostics.ProcessStartInfo]$Start, [string]$Secret) {
    # Windows 的隐藏子进程不能依赖控制台继承；显式捕获并转发已脱敏的报告。
    $Start.RedirectStandardOutput = $true
    $Start.RedirectStandardError = $true
    $Start.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $Start.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    $process = [System.Diagnostics.Process]::Start($Start)
    try {
        # 同时排空两个管道，避免任一管道填满造成死锁；只在内存中处理。
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit(120000)
        if ($timedOut) {
            $process.Kill($true)
            $null = $process.WaitForExit(5000)
        }
        if ($stdout.Wait(5000)) {
            $text = $stdout.GetAwaiter().GetResult()
            if ($Secret) { $text = $text.Replace($Secret, '[REDACTED]') }
            $text = [regex]::Replace($text, '(?i)Bearer\s+\S+', 'Bearer [REDACTED]')
            if ($text.Trim()) { Write-Host $text.TrimEnd() }
        } else { Write-Host 'Agent 标准输出读取超时。' }
        if ($stderr.Wait(5000)) {
            $errors = $stderr.GetAwaiter().GetResult()
            # 只转发 CLI 自有的安全错误提示；其他堆栈/第三方输出不公开。
            foreach ($line in ($errors -split "`r?`n")) {
                if ($line.StartsWith('Live 读取/解析失败：') -or $line.StartsWith('观察已取消')) {
                    if ($Secret) { $line = $line.Replace($Secret, '[REDACTED]') }
                    Write-Host ([regex]::Replace($line, '(?i)Bearer\s+\S+', 'Bearer [REDACTED]'))
                }
            }
            if ($errors.Trim() -and $errors -notmatch 'Live 读取/解析失败：|观察已取消') {
                Write-Host 'Agent 有额外错误输出，已抑制原始堆栈以保护敏感内容。'
            }
        } else { Write-Host 'Agent 错误输出读取超时。' }
        if ($timedOut) { throw 'Agent 超过 120 秒，已停止本轮 Agent。' }
        return $process.ExitCode
    } finally {
        if (-not $process.HasExited) { $process.Kill($true) }
        $process.Dispose()
    }
}

function Invoke-TestAgent([string]$Python, [int]$Number, [string]$Secret) {
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Python
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WorkingDirectory = $ProjectRoot
    foreach ($argument in @('-B','-m','windows_observer_agent','--live','--endpoint',"http://127.0.0.1:$Number/mcp")) {
        $start.ArgumentList.Add($argument)
    }
    $start.Environment['WOA_MCP_TOKEN'] = $Secret
    $start.Environment['PYTHONUTF8'] = '1'
    return (Invoke-AgentProcess $start $Secret)
}

function Stop-TestServer($Process) {
    # 只使用本轮持有的 Process 对象，不按端口、名称或任意 PID 批量结束进程。
    try {
        if (-not $Process.HasExited) { $Process.Kill($true) }
        if (-not $Process.WaitForExit(5000)) { throw '本轮服务未在 5 秒内退出。' }
    } finally {
        $Process.Dispose()
    }
}

function Invoke-LiveTest([int]$Number, [string]$Python) {
    $serverProcess = $null
    $secret = $null
    $exitCode = 2
    $mutex = [System.Threading.Mutex]::new($false, 'Local\WindowsObserverAgentLiveTest')
    $ownsMutex = $false
    try {
        try { $ownsMutex = $mutex.WaitOne(0) }
        catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { throw '另一个本项目 Live 测试脚本仍在运行，已中止。' }
        Assert-LivePreflight $Number
        $serverPythonPath = Resolve-ServerPython $Python
        $agentPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $agentPython)) { throw '缺少项目 .venv，请先按 README 同步依赖。' }
        & $agentPython -I -B -c "from importlib.metadata import version; assert version('mcp')=='2.3.0'; assert version('httpx2')=='2.16.0'"
        if ($LASTEXITCODE -ne 0) { throw '请先安装锁定 Live 依赖：uv sync --extra live --locked。' }
        Assert-LivePreflight $Number
        $secret = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
        $serverProcess = Start-TestServer $serverPythonPath $Number $secret
        Write-Host "本轮服务启动器 PID=$($serverProcess.Id)，只绑定 127.0.0.1:$Number。"
        Wait-TestServer $serverProcess $Number
        Write-Host '请停止鼠标键盘活动，12 秒后进行一次只读观察；不会申请或绕过控制权。'
        Start-Sleep -Seconds 12
        $exitCode = Invoke-TestAgent $agentPython $Number $secret
        if ($exitCode -eq 0) { Write-Host 'LIVE_TEST_PASS：观察成功，计划仍不执行。' }
        elseif ($exitCode -eq 1) { Write-Host 'LIVE_TEST_STOPPED：观察不完整或控制保护拒绝，请查看报告。' }
        else { Write-Host 'LIVE_TEST_FAILED：连接、认证或环境检查失败。' }
    } catch {
        # 仅打印本脚本自有提示；不转储 ProcessStartInfo、Environment 或第三方堆栈。
        Write-Host ('测试中止：' + $_.Exception.Message)
        $exitCode = 2
    } finally {
        $secret = $null
        if ($null -ne $serverProcess) {
            try { Stop-TestServer $serverProcess }
            catch { Write-Host '自动清理未确认完成，请按 README 人工核对本轮 PID。'; $exitCode = 3 }
            try {
                if (@(Get-McpProcesses).Count -gt 0 -or @(Get-PortListeners $Number).Count -gt 0) {
                    Write-Host '仍检测到 MCP 进程或端口监听；不会自动终止，请人工核对后恢复 Codex。'
                    $exitCode = 3
                } else { Write-Host '测试服务与监听已退出，可以重新打开 Codex。' }
            } catch { Write-Host '无法核实清理结果，请人工检查后再打开 Codex。'; $exitCode = 3 }
        }
        if ($ownsMutex) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
    return $exitCode
}

# 点入仅加载函数，供无服务启动的安全替身测试使用。
if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-LiveTest -Number $Port -Python $ServerPython)
}
