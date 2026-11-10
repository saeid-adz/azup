<#
 azup installer for Windows (PowerShell 5.1 or 7).
   irm https://raw.githubusercontent.com/saeid-adz/azup/main/install.ps1 | iex
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Repo   = if ($env:AZUP_REPO)   { $env:AZUP_REPO }   else { 'saeid-adz/azup' }
$Branch = if ($env:AZUP_BRANCH) { $env:AZUP_BRANCH } else { 'main' }
$Dir    = Join-Path $HOME '.azup'
$Bin    = Join-Path $Dir 'bin'

function Say($m) { Write-Host "  > $m" -ForegroundColor Cyan }

function Find-Python {
    foreach ($c in @(@('py','-3'), @('python'), @('python3'))) {
        $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        try {
            $args2 = @($c | Select-Object -Skip 1) + @('-c', 'import sys; sys.exit(sys.version_info < (3,8))')
            & $exe.Source @args2 2>$null
            if ($LASTEXITCODE -eq 0) { return ,@($exe.Source) + @($c | Select-Object -Skip 1) }
        } catch {}
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Python 3.8+ not found and winget is unavailable. Install Python from https://python.org and re-run.'
    }
    Say 'Installing Python 3 with winget'
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
    $py = Find-Python
    if (-not $py) { throw 'Python was installed but is not on PATH yet. Open a new terminal and re-run this installer.' }
}

Say "Downloading azup from github.com/$Repo ($Branch)"
New-Item -ItemType Directory -Force -Path $Dir, $Bin | Out-Null
Invoke-WebRequest "https://raw.githubusercontent.com/$Repo/$Branch/azup.py" -OutFile (Join-Path $Dir 'azup.py') -UseBasicParsing

$launcher = '"' + ($py -join '" "') + '" "%USERPROFILE%\.azup\azup.py" %*'
Set-Content -Path (Join-Path $Bin 'azup.cmd') -Value "@echo off`r`n$launcher" -Encoding ASCII

$user = [Environment]::GetEnvironmentVariable('Path','User')
if (($user -split ';') -notcontains $Bin) {
    [Environment]::SetEnvironmentVariable('Path', ($user.TrimEnd(';') + ';' + $Bin), 'User')
    Say 'Added azup to your PATH – open a new terminal.'
}
Write-Host "`n  OK azup installed. Run: azup`n" -ForegroundColor Green
