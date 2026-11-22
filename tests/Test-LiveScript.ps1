#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1')

function Assert-Throws($Action, [string]$Expected) {
    try { & $Action } catch {
        if ($_.Exception.Message -notlike "*$Expected*") { throw }
        return
    }
    throw 'Expected preflight to stop'
}

# 所有 OS 查询均为替身；不会调用 Start-TestServer 或启动任何 Python 服务。
function Get-Process { return $null }
function Get-McpProcesses { return @([pscustomobject]@{ ProcessId = 12345 }) }
function Get-PortListeners { return @() }
Assert-Throws { Assert-LivePreflight 8765 } '旧 Windows-MCP'

function Get-McpProcesses { return @() }
function Get-PortListeners { return @([pscustomobject]@{ LocalPort = 8765 }) }
Assert-Throws { Assert-LivePreflight 8765 } '被占用'

function Get-PortListeners { return @() }
Assert-LivePreflight 8765

# 只用本轮 Job 和原始根进程对象。即使根进程已退出也必须清理整个 Job。
$script:stopCalls = 0
$script:disposeCalls = 0
$fake = [pscustomobject]@{ HasExited = $true }
$fake | Add-Member ScriptMethod Dispose { $script:disposeCalls++ }
$ownedJob = [pscustomobject]@{}
$ownedJob | Add-Member ScriptMethod Stop {
    param($Milliseconds, $Root)
    if ($Milliseconds -ne 5000 -or -not [object]::ReferenceEquals($Root, $fake)) {
        throw 'Expected owned process and bounded wait'
    }
    $script:stopCalls++
    return $true
}
$ownedJob | Add-Member ScriptMethod Dispose { $script:disposeCalls++ }
Stop-TestServer $ownedJob $fake
if ($script:stopCalls -ne 1 -or $script:disposeCalls -ne 2) { throw 'Cleanup did not use and dispose owned handles' }

# 语法检查，不执行脚本的入口。
$tokens = $null
$parseErrors = $null
$scriptPath = Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1'
$null = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'PowerShell script has syntax errors' }
$source = Get-Content -Raw -LiteralPath $scriptPath
if ($source.Contains('--no-control') -or $source.Contains("'--auth-key'")) { throw 'Unsafe server arguments' }
if (-not $source.Contains("'WINDOWS_MCP_AUTH_KEY'") -or -not $source.Contains("'127.0.0.1'")) { throw 'Missing auth or binding guard' }
Write-Output 'POWERSHELL_SAFETY_TESTS_PASS (old-instance, occupied-port, owned-cleanup, AST)'
