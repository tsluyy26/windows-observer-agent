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

# 清理只使用传入的本轮 Process 对象；替身验证 whole-tree=true。
$script:killCalls = 0
$fake = [pscustomobject]@{ HasExited = $false }
$fake | Add-Member ScriptMethod Kill { param($EntireTree) if (-not $EntireTree) { throw 'Expected entire tree' }; $script:killCalls++ }
$fake | Add-Member ScriptMethod WaitForExit { param($Milliseconds) return $true }
$fake | Add-Member ScriptMethod Dispose { }
Stop-TestServer $fake
if ($script:killCalls -ne 1) { throw 'Cleanup did not use owned process object' }

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
