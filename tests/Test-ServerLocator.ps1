#Requires -Version 7.0
param([ValidateSet('missing', 'multiple')][string]$Scenario)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1')

# 候选发现为替身，Resolve 不应执行任何候选解释器或启动服务。
function Get-ServerPythonCandidates {
    if ($Scenario -eq 'multiple') {
        return @('C:\test-env-one\python.exe', 'C:\test-env-two\python.exe')
    }
    return @()
}
function Start-TestServer { throw 'SERVICE_START_FORBIDDEN' }

$failure = $null
try { Resolve-ServerPython '' | Out-Null } catch { $failure = $_.Exception.Message }
if (-not $failure) { throw 'Expected locator to stop' }
if ($Scenario -eq 'multiple') {
    if ($failure -notlike '*2 个*' -or $failure -notlike '*C:\test-env-one\python.exe*' -or
        $failure -notlike '*C:\test-env-two\python.exe*' -or $failure -notlike '*-ServerPython*') {
        throw 'Ambiguity error must report count, candidates and explicit override'
    }
} elseif ($failure -notlike '*未找到*') {
    throw 'Missing environment must have a distinct error'
}
Write-Output "SERVER_LOCATOR_PASS:$Scenario"
