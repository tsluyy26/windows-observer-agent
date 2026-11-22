#Requires -Version 7.0
param(
    [ValidateSet('identity', 'cleanup', 'startup')]
    [string]$Scenario
)
. (Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1')

function Assert-Blocked($Action, [string]$Expected) {
    try { & $Action } catch {
        if ($_.Exception.Message -notlike "*$Expected*") { throw }
        return
    }
    throw 'Expected the trust check to stop'
}

if ($Scenario -eq 'identity') {
    $path = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'Synthetic Python\python.exe'))
    $interpreter = [pscustomobject]@{ LaunchPath = $path; BasePath = $path }
    $start = [DateTime]::UtcNow
    $root = [pscustomobject]@{ Id = 101; StartTime = $start; HasExited = $false }
    $arguments = Get-TestServerArguments 8765
    $command = '"' + $path + '" "' + ($arguments -join '" "') + '"'
    $entry = [pscustomobject]@{ ProcessId = 101; ParentProcessId = 99; CreationDate = $start;
        ExecutablePath = $path; CommandLine = $command }
    function Get-CimInstance { return $entry }
    function Get-PortListeners { return [pscustomobject]@{ OwningProcess = 101; LocalAddress = '127.0.0.1' } }
    $job = [pscustomobject]@{}
    $job | Add-Member ScriptMethod ContainsProcess {
        param($Id, $Created)
        if ($script:deny) { throw [System.ComponentModel.Win32Exception]::new(5) }
        return $script:lookupTime -eq $Created
    }
    $script:deny = $false
    $script:lookupTime = $start
    $identity = Get-VerifiedTestListener $root 8765 $interpreter $job
    $script:lookupTime = $start.AddSeconds(10)
    Assert-Blocked { Assert-TestListenerIdentity $root 8765 $interpreter $identity $job } '身份与本轮 Windows Job 不符'
    $script:deny = $true
    Assert-Blocked { Assert-TestListenerIdentity $root 8765 $interpreter $identity $job } '无法读取监听进程'
    $script:deny = $false
    $script:lookupTime = $start
    $entry.CreationDate = $start.AddSeconds(10)
    Assert-Blocked { Assert-TestListenerIdentity $root 8765 $interpreter $identity $job } '身份与本轮 Windows Job 不符'
    Write-Output 'LIVE_CLEANUP_TEST_PASS:identity'
    return
}

# All process operations here are doubles. The fake unrelated listener has no method that could kill it.
$script:mode = 'ok'
$script:closed = 0
$script:rootClosed = 0
$script:started = 0
$script:verified = 0
$script:portChecks = 0
$script:secret = $null
$fakeJob = [pscustomobject]@{}
$fakeJob | Add-Member ScriptMethod Stop {
    param($Timeout, $Root)
    if ($Timeout -ne 5000 -or ($null -ne $Root -and -not [object]::ReferenceEquals($Root, $fakeRoot))) {
        throw 'Used a different process or an unbounded wait'
    }
    switch ($script:mode) {
        'timeout' { return $false }
        'access-denied' { throw [System.ComponentModel.Win32Exception]::new(5) }
        'partial-failure' { throw ('S8_SYNTHETIC_PRIVATE_' + 'native_error') }
    }
    return $true
}
$fakeJob | Add-Member ScriptMethod Dispose {
    $script:closed++
    if ($script:mode -eq 'dispose-error') { throw 'S8_SYNTHETIC_PRIVATE_dispose_error' }
}
$fakeRoot = [pscustomobject]@{ Id = 101; HasExited = $true }
$fakeRoot | Add-Member ScriptMethod Dispose { $script:rootClosed++ }
function Get-PortListeners {
    $script:portChecks++
    if ($script:mode -eq 'port-in-use') { return [pscustomobject]@{ OwningProcess = 424242 } }
    if ($script:mode -eq 'port-query-failed') { throw 'S8_SYNTHETIC_PRIVATE_port_error' }
    return @()
}
function Get-McpProcesses { return @() }

function Assert-Report([string]$Report, [string]$Marker, [int]$Result, [int]$Expected) {
    if ($Result -ne $Expected -or ($Marker -and $Report -notlike "*$Marker*")) { throw 'Unexpected outcome' }
    if (($Result -eq 0) -ne $Report.Contains('LIVE_CLEANUP_PASS')) { throw 'False success or missing success' }
    if ($Report.Contains('S8_SYNTHETIC_PRIVATE_') -or ($script:secret -and $Report.Contains($script:secret))) {
        throw 'Cleanup report included synthetic private data'
    }
}

if ($Scenario -eq 'startup') {
    function New-LiveJob {
        if ($script:mode -eq 'job-create-failed') { throw 'S8_JOB_SETUP_FAILED' }
        return $fakeJob
    }
    function Start-TestServer($Job, $Python, $Number, $Secret) {
        if (-not [object]::ReferenceEquals($Job, $fakeJob)) { throw 'Service received a different Job' }
        $script:secret = $Secret
        $script:started++
        if ($script:mode -eq 'job-admission-failed') {
            # Exercise the actual launcher wrapper without a Win32 API side effect.
            $failingJob = [pscustomobject]@{}
            $failingJob | Add-Member ScriptMethod Start {
                param($Start)
                throw [System.ComponentModel.Win32Exception]::new(5)
            }
            return (Start-LiveProcess $failingJob ([System.Diagnostics.ProcessStartInfo]::new()))
        }
        return $fakeRoot
    }
    function Wait-TestServer { return 'verified-listener' }
    function Invoke-TestLiveChecks {
        $script:verified++
        if ($script:mode -eq 'cancelled' -or $script:mode -eq 'dispose-error') {
            throw [OperationCanceledException]::new('S8_OPERATION_INTERRUPTED')
        }
        return 0
    }
    foreach ($case in @(
        @{ Mode = 'job-create-failed'; Start = 0; Verified = 0; Closed = 0; RootClosed = 0; Code = 2; Tag = '无法创建受约束的 Windows Job' },
        @{ Mode = 'job-admission-failed'; Start = 1; Verified = 0; Closed = 1; RootClosed = 0; Code = 2; Tag = '无法在 Windows Job 中创建' },
        @{ Mode = 'cancelled'; Start = 1; Verified = 1; Closed = 1; RootClosed = 1; Code = 2; Tag = 'Live 测试已取消' },
        @{ Mode = 'dispose-error'; Start = 1; Verified = 1; Closed = 1; RootClosed = 1; Code = 3; Tag = 'LIVE_CLEANUP_UNVERIFIED' },
        @{ Mode = 'ok'; Start = 1; Verified = 1; Closed = 1; RootClosed = 1; Code = 0; Tag = 'LIVE_CLEANUP_PASS' }
    )) {
        $script:mode = $case.Mode
        $script:started = $script:verified = $script:closed = $script:rootClosed = 0
        $report = & {
            $script:actual = Invoke-ContainedLiveTest 8765 ([pscustomobject]@{ LaunchPath = 'stub' }) 'stub' $true
        } 6>&1 | Out-String
        if ($script:actual -ne $case.Code -or $script:started -ne $case.Start -or $script:verified -ne $case.Verified -or
            $script:closed -ne $case.Closed -or $script:rootClosed -ne $case.RootClosed -or $report -notlike "*$($case.Tag)*") {
            throw "Wrong S8 lifecycle outcome for $($case.Mode)"
        }
        if ($report.Contains('S8_SYNTHETIC_PRIVATE_') -or $report.Contains('S8_JOB_SETUP_FAILED') -or
            $report.Contains('S8_OPERATION_INTERRUPTED') -or ($script:secret -and $report.Contains($script:secret))) {
            throw 'Launcher exposed synthetic private data'
        }
    }
} else {
    foreach ($case in @(
        @{ Mode = 'ok'; Tag = 'LIVE_CLEANUP_PASS' },
        @{ Mode = 'port-in-use'; Tag = 'LIVE_CLEANUP_PORT_IN_USE' },
        @{ Mode = 'port-query-failed'; Tag = 'LIVE_CLEANUP_UNVERIFIED' },
        @{ Mode = 'timeout'; Tag = 'LIVE_CLEANUP_TIMEOUT' },
        @{ Mode = 'access-denied'; Tag = 'LIVE_CLEANUP_ACCESS_DENIED' },
        @{ Mode = 'partial-failure'; Tag = 'LIVE_CLEANUP_UNVERIFIED' },
        @{ Mode = 'dispose-error'; Tag = 'LIVE_CLEANUP_UNVERIFIED' }
    )) {
        $script:mode = $case.Mode
        $script:closed = $script:rootClosed = $script:portChecks = 0
        $report = & {
            $script:actual = Confirm-TestServerCleanup $fakeJob $fakeRoot 8765
        } 6>&1 | Out-String
        $exitCode = if ($script:actual) { 0 } else { 3 }
        $expected = if ($case.Mode -eq 'ok') { 0 } else { 3 }
        Assert-Report $report $case.Tag $exitCode $expected
        if ($script:closed -ne 1 -or $script:rootClosed -ne 1 -or $script:portChecks -ne 1) {
            throw "Cleanup did not release both handles and check the port for $($case.Mode)"
        }
    }
}
Write-Output "LIVE_CLEANUP_TEST_PASS:$Scenario"
