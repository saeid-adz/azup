<#
 azup installer for Windows (PowerShell 5.1 or 7).
   irm https://raw.githubusercontent.com/saeid-adz/azup/main/install.ps1 | iex

 Needs Python 3.8+. If it is missing, this script installs it:
   1. with winget (registering winget first if a fresh Windows hasn't done it yet), or
   2. straight from python.org (per-user, no admin rights needed).
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$Repo   = if ($env:AZUP_REPO)   { $env:AZUP_REPO }   else { 'saeid-adz/azup' }
$Branch = if ($env:AZUP_BRANCH) { $env:AZUP_BRANCH } else { 'main' }
$Dir    = Join-Path $HOME '.azup'
$Bin    = Join-Path $Dir 'bin'
$PyVersion = '3.12.8'

function Say($m)  { Write-Host "  > $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

function Update-SessionPath {
    $env:Path = (@([Environment]::GetEnvironmentVariable('Path','Machine'),
                   [Environment]::GetEnvironmentVariable('Path','User'),
                   (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps')) | Where-Object { $_ }) -join ';'
}

function Find-Python {
    foreach ($c in @(@('py','-3'), @('python'), @('python3'))) {
        $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        try {
            $extra = @($c | Select-Object -Skip 1)
            & $exe.Source @($extra + @('-c', 'import sys; sys.exit(sys.version_info < (3,8))')) 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return ,(@($exe.Source) + $extra) }
        } catch {}
    }
    # python.org per-user install location, in case PATH was not refreshed yet
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe")) {
        if (Test-Path $p) {
            $extra = if ($p -like '*py.exe') { @('-3') } else { @() }
            & $p @($extra + @('-c', 'import sys; sys.exit(sys.version_info < (3,8))')) 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return ,(@($p) + $extra) }
        }
    }
    return $null
}

function Get-Winget {
    Update-SessionPath
    if (Get-Command winget -ErrorAction SilentlyContinue) { return $true }
    # Fresh Windows installs often have App Installer present but not registered for this user yet.
    try {
        Say 'Registering winget (App Installer)...'
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
        Start-Sleep -Seconds 2
    } catch { }
    Update-SessionPath
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Install-PythonDirect {
    $arch = switch ($env:PROCESSOR_ARCHITECTURE) { 'ARM64' { 'arm64' } 'x86' { $null } default { 'amd64' } }
    $suffix = if ($arch) { "-$arch" } else { '' }
    $url = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion$suffix.exe"
    $exe = Join-Path $env:TEMP "python-$PyVersion-setup.exe"
    Say "Downloading Python $PyVersion from python.org"
    Invoke-WebRequest $url -OutFile $exe -UseBasicParsing
    Say 'Installing Python for the current user (no admin rights needed)...'
    $p = Start-Process $exe -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_launcher=1','Include_test=0','Shortcuts=0' -Wait -PassThru
    Remove-Item $exe -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) { throw "The Python installer failed (exit code $($p.ExitCode))." }
}

$py = Find-Python
if (-not $py) {
    $done = $false
    if (Get-Winget) {
        try {
            Say 'Installing Python 3 with winget'
            winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
            $done = ($LASTEXITCODE -eq 0)
        } catch { $done = $false }
        if (-not $done) { Warn 'winget could not install Python - falling back to python.org' }
    } else {
        Warn 'winget is not available on this machine - installing Python from python.org instead'
    }
    if (-not $done) { Install-PythonDirect }
    Update-SessionPath
    $py = Find-Python
    if (-not $py) { throw 'Python was installed but is not visible yet. Open a NEW PowerShell window and run this installer again.' }
}

Say "Downloading azup from github.com/$Repo ($Branch)"
New-Item -ItemType Directory -Force -Path $Dir, $Bin | Out-Null
Invoke-WebRequest "https://raw.githubusercontent.com/$Repo/$Branch/azup.py" -OutFile (Join-Path $Dir 'azup.py') -UseBasicParsing

# Quote only the executable. The py launcher ignores a quoted "-3", and hands it to python.exe instead.
$launcher = '"' + $py[0] + '"' + ((@($py | Select-Object -Skip 1) | ForEach-Object { " $_" }) -join '') + ' "%USERPROFILE%\.azup\azup.py" %*'
Set-Content -Path (Join-Path $Bin 'azup.cmd') -Value "@echo off`r`n$launcher" -Encoding ASCII

$user = [Environment]::GetEnvironmentVariable('Path','User')
if (($user -split ';') -notcontains $Bin) {
    [Environment]::SetEnvironmentVariable('Path', (($user.TrimEnd(';') + ';' + $Bin).TrimStart(';')), 'User')
    Say 'Added azup to your PATH.'
}
$env:Path += ";$Bin"
Write-Host "`n  OK azup installed. Open a new terminal and run: azup`n" -ForegroundColor Green
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Warn 'winget was not found. azup needs it to install CLI tools: update "App Installer" in the Microsoft Store (or https://aka.ms/getwinget), then run: azup doctor'
}
