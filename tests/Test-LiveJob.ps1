#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$PythonExecutable,
    [ValidateSet('normal', 'orphan', 'exited-child', 'host-kill', 'host-worker', 'start-failure', 'command-line', 'port-release')]
    [string]$Scenario
)
. (Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1')

function New-FixtureStart([string]$FileName, [string[]]$Arguments) {
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FileName
    $start.WorkingDirectory = $ProjectRoot
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
    return $start
}

function Start-Worker($Job, [string]$Mode, $fixtureArguments = @()) {
    $worker = Join-Path $PSScriptRoot 'fixtures\job_worker.py'
    $start = New-FixtureStart $PythonExecutable (@('-I', '-B', $worker, $Mode) + $fixtureArguments)
    return (Start-LiveProcess $Job $start)
}

function Read-WorkerLine($Process) {
    $line = $Process.StandardOutput.ReadLineAsync()
    if (-not $line.Wait(10000) -or -not $line.Result) { throw 'Fixture did not report readiness' }
    return $line.Result
}

function Open-FixtureProcess([int]$Id) {
    $process = [System.Diagnostics.Process]::GetProcessById($Id)
    # Keep the same process handle even when it exits while we wait.
    $null = $process.SafeHandle
    return $process
}

if ($Scenario -eq 'host-worker') {
    $innerJob = New-LiveJob
    $child = Start-Worker $innerJob 'orphan'
    [Console]::WriteLine((Read-WorkerLine $child))
    if (-not $child.WaitForExit(10000) -or $innerJob.ActiveProcesses -lt 1) {
        throw 'Host fixture has no surviving descendants'
    }
    [Console]::WriteLine('HOST_READY')
    Start-Sleep -Seconds 60
    exit 4
}

$job = New-LiveJob
$unrelatedJob = $null
$root = $null
$unrelated = $null
$held = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
try {
    if ($Scenario -eq 'start-failure') {
        $start = New-FixtureStart (Join-Path $PSScriptRoot 'nonexistent S8 process.exe') @()
        $rejected = $false
        try { $root = Start-LiveProcess $job $start }
        catch { $rejected = $true }
        if (-not $rejected -or $job.ActiveProcesses -ne 0) { throw 'Failed start left a process' }
    } elseif ($Scenario -eq 'port-release') {
        # Use actual socket state without depending on WMI permissions in offline runners.
        function Get-McpProcesses { return @() }
        function Get-PortListeners([int]$Number) {
            [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
                Where-Object Port -EQ $Number
        }
        $root = Start-Worker $job 'listener'
        $fields = (Read-WorkerLine $root).Split(',')
        $port = [int]$fields[0]
        $listener = Open-FixtureProcess ([int]$fields[1])
        $held.Add($listener)
        $matches = @(Get-PortListeners $port)
        if ($matches.Count -ne 1 -or $matches[0].Address.ToString() -cne '127.0.0.1' -or
            -not $job.ContainsProcess($listener.Id, $listener.StartTime.ToUniversalTime())) {
            throw 'Cannot verify the synthetic loopback listener'
        }
        if (-not (Confirm-TestServerCleanup $job $root $port) -or @(Get-PortListeners $port).Count -ne 0) {
            throw 'Cleanup did not verify that the synthetic loopback port was released'
        }
    } elseif ($Scenario -eq 'command-line') {
        $values = @('', 'two words', 'ends in \"', 'C:\sample path\', 'a"b')
        $root = Start-Worker $job 'arguments' $values
        $reported = (Read-WorkerLine $root) | ConvertFrom-Json -NoEnumerate
        if ($reported.Count -ne $values.Count) { throw 'Argument count changed' }
        for ($i = 0; $i -lt $values.Count; $i++) {
            if ($reported[$i] -cne $values[$i]) { throw "Argument $i changed" }
        }
        if (-not $root.WaitForExit(10000) -or $root.ExitCode -ne 0) { throw 'Fixture failed' }
    } elseif ($Scenario -eq 'host-kill') {
        $pwsh = (Get-Process -Id $PID).Path
        $start = New-FixtureStart $pwsh @('-NoProfile', '-NonInteractive', '-File',
            $PSCommandPath, '-PythonExecutable', $PythonExecutable, '-Scenario', 'host-worker')
        $root = Start-LiveProcess $job $start
        $ids = (Read-WorkerLine $root).Split(',')
        foreach ($idText in $ids) {
            $member = Open-FixtureProcess ([int]$idText)
            $held.Add($member)
            if (-not $job.ContainsProcess($member.Id, $member.StartTime.ToUniversalTime())) {
                throw 'Test guardian cannot prove ownership'
            }
        }
        if ((Read-WorkerLine $root) -cne 'HOST_READY') { throw 'Host fixture not ready' }
        $hostProcess = Open-FixtureProcess $root.Id
        $held.Add($hostProcess)
        if ($hostProcess.StartTime.ToUniversalTime() -ne $root.StartTime -or
            -not $job.ContainsProcess($hostProcess.Id, $root.StartTime)) {
            throw 'Test host identity changed'
        }
        # Kill only the held fixture host handle. Leave the outer guardian Job open.
        $hostProcess.Kill()
        foreach ($member in $held) {
            if (-not $member.WaitForExit(5000)) { throw 'Abrupt host exit left a live fixture descendant' }
        }
        if ($job.ActiveProcesses -ne 0) { throw 'Abrupt host exit did not empty the guardian Job' }
    } else {
        $root = Start-Worker $job $Scenario
        $ids = (Read-WorkerLine $root).Split(',')
        if ($Scenario -eq 'orphan') {
            if (-not $root.WaitForExit(10000) -or $root.ExitCode -ne 0 -or $ids.Count -ne 2) {
                throw 'Orphan fixture did not leave two generations'
            }
            if ($job.ActiveProcesses -lt 2) { throw 'Expected living descendants after root exit' }
        }
        if ($Scenario -eq 'exited-child' -and ($root.HasExited -or $ids[0] -ne 'child-exited')) {
            throw 'Fixture child did not exit ahead of its root'
        }
        if ($Scenario -ne 'exited-child') {
            foreach ($idText in $ids) {
                $child = Open-FixtureProcess ([int]$idText)
                $held.Add($child)
                if (-not $job.ContainsProcess($child.Id, $child.StartTime.ToUniversalTime()) -or
                    $job.ContainsProcess($child.Id, $child.StartTime.ToUniversalTime().AddSeconds(-1))) {
                    throw 'Member and reused PID must have different identity results'
                }
            }
        }
        if ($Scenario -eq 'orphan') {
            $unrelatedJob = New-LiveJob
            $unrelated = Start-Worker $unrelatedJob 'unrelated'
            if ((Read-WorkerLine $unrelated) -ne 'ready' -or
                $job.ContainsProcess($unrelated.Id, $unrelated.StartTime)) {
                throw 'Same executable outside the service Job was recognized as a member'
            }
        }
        if ($Scenario -eq 'normal' -and (-not $root.WaitForExit(10000) -or $root.ExitCode -ne 0)) {
            throw 'Normal fixture did not exit cleanly'
        }
    }

    Stop-TestServer $job $root
    Stop-TestServer $job $root
    foreach ($member in $held) {
        if (-not $member.HasExited) { throw 'Cleanup left a member alive' }
    }
    if ($null -ne $unrelated -and $unrelated.HasExited) { throw 'Cleanup killed an unrelated fixture' }
} finally {
    $job.Dispose()
    if ($null -ne $root) { $root.Dispose() }
    if ($null -ne $unrelatedJob) { $unrelatedJob.Dispose() }
    if ($null -ne $unrelated) { $unrelated.Dispose() }
    foreach ($member in $held) { $member.Dispose() }
}
Write-Output "LIVE_JOB_TEST_PASS:$Scenario"
