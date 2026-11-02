#Requires -Version 7.0
[CmdletBinding()]
param([string]$PythonExecutable)

# 自动化测试传 sys.executable；手动运行也必须显式指定，不搜索环境或下载解释器。
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    throw 'PythonExecutable is required; pass -PythonExecutable with the full Python path.'
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw 'PythonExecutable must point to an existing file.'
}
$PythonExecutable = (Resolve-Path -LiteralPath $PythonExecutable).ProviderPath
. (Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1')
$start = [System.Diagnostics.ProcessStartInfo]::new()
$start.FileName = $PythonExecutable
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.ArgumentList.Add('-B')
$start.ArgumentList.Add('-c')
# 只运行合成 stdout/stderr，不导入 Agent/SDK、不连接网络、不启动 MCP。
$start.ArgumentList.Add('import os,sys; print("MOCK_AGENT_REPORT: 观察失败"); print("MOCK_CONTROL_STATE=user"); print("TOKEN="+os.environ["WOA_MCP_TOKEN"]); print("Live 读取/解析失败：Authorization: Bearer "+os.environ["WOA_MCP_TOKEN"], file=sys.stderr); sys.exit(1)')
$fakeSecret = 'TEST_ONLY_' + ('x' * 48)
$start.Environment['WOA_MCP_TOKEN'] = $fakeSecret
$start.Environment['PYTHONUTF8'] = '1'
$report = & { $code = Invoke-AgentProcess $start $fakeSecret; Write-Output "CHILD_EXIT=$code" } 6>&1 | Out-String
if ($report -notlike '*MOCK_AGENT_REPORT: 观察失败*' -or $report -notlike '*MOCK_CONTROL_STATE=user*') {
    throw 'Agent stdout must be forwarded even when exit code is 1'
}
if ($report -notlike '*CHILD_EXIT=1*') { throw 'Report text must not contaminate the returned exit code' }
if ($report.Contains($fakeSecret)) { throw 'Token leaked into terminal output' }
if ($report -notlike '*[[]REDACTED[]]*' -or $report -notlike '*Live 读取/解析失败：*') {
    throw 'Expected safe stderr and explicit token masking'
}
Write-Output 'AGENT_OUTPUT_PASS: report visible, exit=1, token masked'
