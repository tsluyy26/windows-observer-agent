#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1')

# OS、HTTP、Agent 全部替身。此脚本不执行外部启动命令。
function Start-TestServer { throw 'SERVICE_START_FORBIDDEN' }
function Get-CimInstance($ClassName, $Filter) {
    return @($script:processes | Where-Object ProcessId -EQ ($Filter -replace '^ProcessId = ', ''))
}
function Get-PortListeners { return $script:listeners }

function Assert-Blocked($Action) {
    try { & $Action } catch {
        if ($_.Exception.Message -notlike '*监听*' -and $_.Exception.Message -notlike '*认证*') {
            throw 'Unexpected failure category'
        }
        return
    }
    throw 'Expected S5 verification to stop'
}

$rootPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'Synthetic Venv/python.exe'))
$basePath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'Synthetic Base/python.exe'))
$interpreter = [pscustomobject]@{ LaunchPath = $rootPath; BasePath = $basePath }
$start = [datetime]::UtcNow
$root = [pscustomobject]@{ Id = 111; StartTime = $start; HasExited = $false }
$arguments = @('-I','-B','-m','windows_mcp','serve','--transport','streamable-http',
    '--host','127.0.0.1','--port','8765','--config',
    (Join-Path (Join-Path $ProjectRoot 'scripts') 'live-server.toml'),'--tools','Screenshot,Snapshot,ControlStatus')
$command = '"' + $rootPath + '" ' + (($arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' ')
$rootInfo = [pscustomobject]@{ ProcessId = 111; ParentProcessId = 99; CreationDate = $start;
    ExecutablePath = $rootPath; CommandLine = $command }
$child = [pscustomobject]@{ ProcessId = 222; ParentProcessId = 111; CreationDate = $start.AddMilliseconds(5);
    ExecutablePath = $basePath; CommandLine = $command }
$script:processes = @($rootInfo, $child)
$job = [pscustomobject]@{}
$job | Add-Member ScriptMethod ContainsProcess { param($Id, $Created) return $Id -in (111, 222) }
$listener = [pscustomobject]@{ OwningProcess = 222; LocalAddress = '127.0.0.1' }
$script:listeners = @($listener)
$identity = Get-VerifiedTestListener $root 8765 $interpreter $job
if ($identity.Pid -ne 222) { throw 'The base interpreter should be recognized as the listener' }

$listener.OwningProcess = 333
Assert-Blocked { Get-VerifiedTestListener $root 8765 $interpreter $job }
$listener.OwningProcess = 222
$child.ExecutablePath = (Join-Path $PSScriptRoot 'Other/python.exe')
Assert-Blocked { Get-VerifiedTestListener $root 8765 $interpreter $job }
$child.ExecutablePath = ''
Assert-Blocked { Get-VerifiedTestListener $root 8765 $interpreter $job }
$child.ExecutablePath = $basePath
$child.CommandLine = ''
Assert-Blocked { Get-VerifiedTestListener $root 8765 $interpreter $job }
$child.CommandLine = $command + ' --unexpected'
Assert-Blocked { Get-VerifiedTestListener $root 8765 $interpreter $job }
$child.CommandLine = $command
$child.CreationDate = $start.AddMilliseconds(20)
Assert-Blocked { Assert-TestListenerIdentity $root 8765 $interpreter $identity $job }
$child.CreationDate = $start.AddMilliseconds(5)

$fake = 'a' * 64
$script:calls = [System.Collections.Generic.List[string]]::new()
$script:scenario = 'pass'
function Invoke-AgentProcess($Start, $Job) {
    $mode = $Start.ArgumentList[-1]
    $expected = $mode -eq 'correct'
    if ($Start.ArgumentList.Count -ne 5 -or ($Start.ArgumentList -join ' ').Contains($fake) -or
        $Start.Environment.ContainsKey('WOA_MCP_TOKEN') -ne $expected -or
        ($expected -and $Start.Environment['WOA_MCP_TOKEN'] -cne $fake)) {
        throw 'Unexpected probe subprocess credential boundary'
    }
    return 0
}
Invoke-TestAuthProbe 'agent-python' 8765 'missing'
Invoke-TestAuthProbe 'agent-python' 8765 'wrong'
Invoke-TestAuthProbe 'agent-python' 8765 'correct' $fake

function Invoke-TestAuthProbe($Python, $Number, $Mode, $Secret) {
    $script:calls.Add($Mode)
    if (($Mode -eq 'correct') -ne ($Secret -ceq $fake)) { throw 'Wrong stage for the test credential' }
    if ($Mode -eq 'missing' -and $script:scenario -eq 'swap-before-correct') { $listener.OwningProcess = 111 }
    if ($Mode -eq 'correct' -and $script:scenario -eq 'swap-after-correct') { $listener.OwningProcess = 333 }
    if ($Mode -eq $script:scenario) { throw '认证替身拒绝验收' }
}
function Invoke-TestAgent($Python, $Number, $Secret) {
    $script:calls.Add('agent')
    return 0
}
function Start-Sleep {
    if ($script:scenario -eq 'swap-before-observation') { $listener.OwningProcess = 111 }
}

foreach ($case in @(
        @{ Scenario = 'missing'; Calls = 'missing' },
        @{ Scenario = 'wrong'; Calls = 'missing,wrong' },
        @{ Scenario = 'correct'; Calls = 'missing,wrong,correct' },
        @{ Scenario = 'swap-before-correct'; Calls = 'missing' },
        @{ Scenario = 'swap-after-correct'; Calls = 'missing,wrong,correct' },
        @{ Scenario = 'swap-before-observation'; Calls = 'missing,wrong,correct' })) {
    $script:scenario = $case.Scenario
    $script:calls.Clear()
    $listener.OwningProcess = 222
    Assert-Blocked { Invoke-TestLiveChecks 'agent-python' $root 8765 $interpreter $identity $fake $false $job }
    if (($script:calls -join ',') -ne $case.Calls) { throw 'A failed check allowed a later authentication stage or observation' }
}
$script:scenario = 'pass'
$listener.OwningProcess = 222
$script:calls.Clear()
$report = & {
    $code = Invoke-TestLiveChecks 'agent-python' $root 8765 $interpreter $identity $fake $true $job
    if ($code -ne 0) { throw 'Authentication-only checks failed' }
} 6>&1 | Out-String
if (($script:calls -join ',') -ne 'missing,wrong,correct' -or $report -notlike '*LIVE_AUTH_PASS*' -or $report.Contains($fake)) {
    throw 'Authentication-only flow must pass without observation or credential output'
}
$script:calls.Clear()
$null = Invoke-TestLiveChecks 'agent-python' $root 8765 $interpreter $identity $fake $false $job
if (($script:calls -join ',') -ne 'missing,wrong,correct,agent') { throw 'Observation must follow all authentication checks' }

Write-Output 'LIVE_AUTH_SCRIPT_TESTS_PASS'
