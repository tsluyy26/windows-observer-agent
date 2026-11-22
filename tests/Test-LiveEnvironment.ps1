#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$PythonExecutable,
    [ValidateSet('venv', 'base', 'missing-folder', 'native-failure')]
    [string]$Scenario
)
. (Join-Path $PSScriptRoot '..\scripts\Test-Live.ps1')

function Get-Digest([string]$Text) {
    return [Convert]::ToHexString(
        [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($Text))
    ).ToLowerInvariant()
}

$locations = @(
    [Environment]::GetFolderPath('Windows'),
    [Environment]::GetFolderPath('UserProfile'),
    [Environment]::GetFolderPath('LocalApplicationData')
)
$locations += [IO.Path]::Combine($locations[2], 'Temp')
$systemDigest = Get-Digest ($locations -join [char]10)
$basePython = & $PythonExecutable -I -B -c 'import sys; print(sys._base_executable)'
if ($LASTEXITCODE -ne 0 -or -not [IO.File]::Exists($basePython)) { throw 'Cannot locate test base interpreter' }

$secret = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$keyDigest = Get-Digest $secret
$providerOne = 'S7_PROVIDER_' + [Guid]::NewGuid().ToString('N')
$providerTwo = 'S7_PROVIDER_' + [Guid]::NewGuid().ToString('N')
$markers = @($secret, $providerOne, $providerTwo)
$parentValues = @{}
# Values are synthetic and set only in this disposable test host.
foreach ($name in @('WINDOWS_MCP_AUTH_KEY', 'WOA_MCP_TOKEN', 'WOA_LLM_API_KEY', $providerOne, $providerTwo,
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'PYTHONPATH', 'PYTHONHOME',
        'PYTHONWARNINGS', 'PYTHONBREAKPOINT', 'WINDOWS_MCP_DEBUG', 'FASTMCP_ENV_FILE', 'ANONYMIZED_TELEMETRY',
        'PATH', 'TEMP', 'TMP', 'SYSTEMROOT', 'WINDIR', 'USERPROFILE', 'LOCALAPPDATA')) {
    $marker = 'S7_SYNTHETIC_' + [Guid]::NewGuid().ToString('N')
    $markers += $marker
    $parentValues[$name] = $marker
    [Environment]::SetEnvironmentVariable($name, $marker, 'Process')
}
$worker = Join-Path $PSScriptRoot 'fixtures\live_environment_worker.py'
function Get-TestServerArguments([int]$Number) {
    # Replace only the service payload. Start-TestServer builds the normal environment.
    @('-I', '-B', $worker, $Scenario, $keyDigest, $systemDigest)
}

$nativeJob = New-LiveJob
$process = $null
$script:nativeStarts = 0
$job = [pscustomobject]@{ Native = $nativeJob }
$job | Add-Member ScriptMethod Start {
    param($Start, $Environment)
    $script:nativeStarts++
    if (($Start.ArgumentList -join ' ').Contains($secret)) { throw 'Test token leaked into child argv' }
    $Start.RedirectStandardOutput = $true
    $Start.RedirectStandardError = $true
    return $this.Native.Start($Start, $Environment)
}

try {
    if ($Scenario -in @('missing-folder', 'native-failure')) {
        $path = Join-Path $PSScriptRoot 's7 nonexistent interpreter.exe'
        if ($Scenario -eq 'missing-folder') {
            function Get-LiveServerWindowsFolders {
                @{ SYSTEMROOT = ''; USERPROFILE = $locations[1]; LOCALAPPDATA = $locations[2] }
            }
            $path = $PythonExecutable
        }
        $failed = $false
        try { $process = Start-TestServer $job $path 8765 $secret }
        catch {
            foreach ($marker in $markers) {
                if ($_.Exception.Message.Contains($marker)) { throw 'Launch error exposed a test marker' }
            }
            $failed = $true
        }
        $expectedStarts = if ($Scenario -eq 'native-failure') { 1 } else { 0 }
        if (-not $failed -or $script:nativeStarts -ne $expectedStarts -or $nativeJob.ActiveProcesses -ne 0) {
            throw 'Failed start did not fail closed'
        }
    } else {
        $exe = if ($Scenario -eq 'venv') { $PythonExecutable } else { $basePython }
        $process = Start-TestServer $job $exe 8765 $secret
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000) -or -not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) {
            throw 'S7 child exited or drained too slowly'
        }
        $output = $stdout.Result + $stderr.Result
        foreach ($marker in $markers) {
            if ($output.Contains($marker)) { throw 'S7 child exposed a test marker' }
        }
        if ($process.ExitCode -ne 0 -or $output.Trim() -cne 'S7_CHILD_PASS') {
            throw 'S7 child did not verify the exact environment'
        }
        if ($script:nativeStarts -ne 1) { throw 'Unexpected native start count' }
    }
} finally {
    Stop-TestServer $nativeJob $process
}
foreach ($name in $parentValues.Keys) {
    if ([Environment]::GetEnvironmentVariable($name, 'Process') -cne $parentValues[$name]) {
        throw 'The launcher changed its caller environment'
    }
}
Write-Output "S7_LIVE_ENVIRONMENT_PASS:$Scenario"
