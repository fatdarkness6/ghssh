param([Parameter(Mandatory = $true)][string]$Version)

$ErrorActionPreference = 'Stop'
$installer = (Resolve-Path "dist/ghssh-${Version}-windows-x64-setup.exe").Path
$testRoot = Join-Path $env:RUNNER_TEMP 'ghssh installer test'
$installDir = Join-Path $testRoot 'app with spaces'
$sentinelDir = Join-Path $testRoot 'keep this PATH entry'
$userPathBefore = [Environment]::GetEnvironmentVariable('Path', 'User')
$markerKey = 'HKCU:\Software\ghssh'
if (Test-Path $markerKey) { throw 'Refusing to test over an existing ghssh installation' }
New-Item -ItemType Directory -Force $testRoot | Out-Null

function Invoke-Setup {
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$installDir`"", '/TASKS=addtopath')
    $process = Start-Process $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
}

try {
    Invoke-Setup
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($userPath -split ';') -notcontains $installDir) { throw 'Installer did not add user PATH entry' }
    # Simulate the fresh environment received by a newly opened terminal.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + $userPath
    if ((Get-Command ghssh).Source -ne (Join-Path $installDir 'ghssh.exe')) { throw 'Wrong ghssh launcher' }
    & cmd.exe /d /c 'ghssh --version'
    if ($LASTEXITCODE -ne 0) { throw 'CMD launch failed' }
    & powershell.exe -NoProfile -Command 'ghssh --version'
    if ($LASTEXITCODE -ne 0) { throw 'PowerShell launch failed' }
    python packaging/windows/smoke_test.py (Join-Path $installDir 'ghssh.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Installed executable smoke test failed' }

    Invoke-Setup
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (@($userPath -split ';' | Where-Object { $_ -eq $installDir }).Count -ne 1) {
        throw 'Upgrade duplicated the PATH entry'
    }
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$sentinelDir", 'User')
    $uninstaller = Join-Path $installDir 'unins000.exe'
    $process = Start-Process $uninstaller -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw 'Uninstall failed' }
    $after = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($after -split ';') -contains $installDir) { throw 'Uninstall left its PATH entry' }
    if (($after -split ';') -notcontains $sentinelDir) { throw 'Uninstall overwrote another PATH entry' }
    Write-Output 'Installer checks passed.'
}
finally {
    [Environment]::SetEnvironmentVariable('Path', $userPathBefore, 'User')
    if (Test-Path $markerKey) { Remove-Item $markerKey -Recurse -Force }
}
