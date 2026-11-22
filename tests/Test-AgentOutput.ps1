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
$start.WorkingDirectory = $ProjectRoot
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.ArgumentList.Add('-B')
$start.ArgumentList.Add('-c')
# 只运行合成 stdout/stderr，不导入 Agent/SDK、不连接网络、不启动 MCP。
$start.ArgumentList.Add('import os,sys; print("WOA_REPORT incomplete user"); print("Live 读取/解析失败：Authorization: Bearer "+os.environ["WOA_MCP_TOKEN"], file=sys.stderr); sys.exit(1)')
$fakeSecret = 'S9_SYNTHETIC_' + [Guid]::NewGuid().ToString('N')
$start.Environment['WOA_MCP_TOKEN'] = $fakeSecret
$start.Environment['PYTHONUTF8'] = '1'
$job = New-LiveJob
try {
    $report = & { $code = Invoke-AgentProcess $start $job -Summary; Write-Output "CHILD_EXIT=$code" } 6>&1 | Out-String
} finally {
    Stop-TestServer $job $null
}
if ($report -notlike '*Live 观察摘要*' -or $report -notlike '*观察完成：False*' -or $report -notlike '*控制器：user*') {
    throw 'Agent stdout must be forwarded even when exit code is 1'
}
if ($report -notlike '*CHILD_EXIT=1*') { throw 'Report text must not contaminate the returned exit code' }
if ($report.Contains($fakeSecret)) { throw 'Token leaked into terminal output' }
if ($report -notlike '*Live 读取/解析失败：*无法判定子进程错误类别*' -or $report.Contains('Authorization:')) {
    throw 'Expected an independently rendered diagnostic with the entire payload discarded'
}
Write-Output 'AGENT_OUTPUT_PASS: report visible, exit=1, token masked'
