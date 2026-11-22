#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1')

$secret = 'S9_SYNTHETIC_' + [Guid]::NewGuid().ToString('N')
$encoded = ($secret.ToCharArray() | ForEach-Object { '\u{0:x4}' -f [int]$_ }) -join ''
$bad = "C:\synthetic-private\$secret`r`n$([char]27)[2K$([char]0x202e)$encoded"
function Assert-Safe([string]$Text, [string]$Clue) {
    if (-not $Text.Contains($Clue)) { throw 'Expected fixed diagnostic' }
    foreach ($marker in @($secret, $encoded, 'synthetic-private', [string][char]27, [string][char]0x202e)) {
        if ($Text.Contains($marker)) { throw 'Public output exposed a fixture marker' }
    }
}

$report = & { Write-AgentSummary "WOA_REPORT incomplete user" "WOA_ERROR live mcp_tool" 1 } 6>&1 | Out-String
Assert-Safe $report '控制器：user'
Assert-Safe $report 'MCP 工具调用或结果处理失败'

foreach ($payload in @(
    "WOA_ERROR live mcp_tool $bad",
    "WOA_ERROR live mcp_tool`r`n$bad",
    "Live 读取/解析失败：Bearer $bad",
    "WOA_ERROR live $bad"
)) {
    $report = & { Write-AgentSummary "WOA_REPORT incomplete user`n$bad" $payload 1 } 6>&1 | Out-String
    Assert-Safe $report '无法判定子进程错误类别'
    if ($report.Contains('控制器：')) { throw 'Unrecognized report was accepted' }
}

# Test top-level and cleanup boundaries with substitutes. No child service is started.
function New-LiveJob { throw ("未知 Windows 错误 $bad") }
$report = & {
    $result = Invoke-ContainedLiveTest 8765 ([pscustomobject]@{ LaunchPath = 'stub' }) 'stub' $true
    if ($result -ne 2) { throw 'Startup exit code changed' }
} 6>&1 | Out-String
Assert-Safe $report '无法创建受约束的 Windows Job'

function Stop-TestServer { throw "LIVE_CLEANUP_TIMEOUT：$bad" }
function Get-PortListeners { return @() }
function Get-McpProcesses { return @() }
$report = & {
    if (Confirm-TestServerCleanup 'stub' $null 8765) { throw 'False cleanup success' }
} 6>&1 | Out-String
Assert-Safe $report 'LIVE_CLEANUP_UNVERIFIED'
if ($report.Contains('LIVE_CLEANUP_TIMEOUT')) { throw 'An untyped exception impersonated a cleanup category' }

Write-Output 'S9_POWERSHELL_ERRORS_PASS'
