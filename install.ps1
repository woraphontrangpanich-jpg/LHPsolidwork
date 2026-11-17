# SheetFlat installer / updater for Windows (no git needed).
#
#   Easiest:  download SheetFlat-Setup.cmd from the website and double-click it.
#   Or:       powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/woraphontrangpanich-jpg/LHPsolidwork/main/install.ps1 | iex"
#
# Downloads the latest version from GitHub, keeps the installed components (.venv) and settings
# (data\), adds a "SheetFlat" shortcut on the desktop and starts the program.
# Running it again on the same folder updates the program.
#
# Switches: -Pick (ask for the folder with a dialog)  -Dir <folder>  -NoStart  -NoShortcut
# The same can be set with environment variables SHEETFLAT_DIR / SHEETFLAT_NO_START / SHEETFLAT_NO_SHORTCUT.
param(
    [string]$Dir = "",
    [switch]$Pick,
    [switch]$NoStart,
    [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$zipUrl = 'https://github.com/woraphontrangpanich-jpg/LHPsolidwork/archive/refs/heads/main.zip'
$defaultDir = Join-Path $env:LOCALAPPDATA 'SheetFlat'

if (-not $Dir -and $env:SHEETFLAT_DIR) { $Dir = $env:SHEETFLAT_DIR }
if ($env:SHEETFLAT_NO_START) { $NoStart = $true }
if ($env:SHEETFLAT_NO_SHORTCUT) { $NoShortcut = $true }

function Select-InstallDir {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = 'Choose where to install SheetFlat. A "SheetFlat" folder will be created inside the folder you pick.'
    $dlg.ShowNewFolderButton = $true
    $dlg.SelectedPath = $env:LOCALAPPDATA
    $owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true; ShowInTaskbar = $false }
    $res = $dlg.ShowDialog($owner)
    $owner.Dispose()
    if ($res -ne [System.Windows.Forms.DialogResult]::OK) { return $null }
    $p = $dlg.SelectedPath
    # picking an existing SheetFlat folder updates it in place
    if ((Split-Path -Leaf $p) -ieq 'SheetFlat' -or (Test-Path (Join-Path $p 'start.bat'))) { return $p }
    return (Join-Path $p 'SheetFlat')
}

if (-not $Dir) {
    if ($Pick) {
        $Dir = Select-InstallDir
        if (-not $Dir) { Write-Host ' Cancelled - nothing was installed.'; return }
    } else {
        $Dir = $defaultDir
    }
}
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')

$tmp = Join-Path $env:TEMP ("sheetflat_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Write-Host ''
    Write-Host " Installing SheetFlat to: $Dir"
    Write-Host ' Downloading the latest version ...'
    Invoke-WebRequest -Uri $zipUrl -OutFile "$tmp\sheetflat.zip" -UseBasicParsing
    Expand-Archive -Path "$tmp\sheetflat.zip" -DestinationPath $tmp
    $src = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    # copy the new version, keep installed components and settings
    robocopy $src.FullName $Dir /E /XD .venv data /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copy failed ($LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
    Get-ChildItem -Path $Dir -File | Unblock-File      # .bat / .ps1 downloaded from the internet
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path $desktop 'SheetFlat.lnk'))
    $lnk.TargetPath = Join-Path $Dir 'start.bat'
    $lnk.WorkingDirectory = $Dir
    $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,43"
    $lnk.Description = 'SheetFlat'
    $lnk.Save()
    Write-Host ' A "SheetFlat" shortcut was added to the desktop.'
}

Write-Host ' Installed.'
if (-not $NoStart) {
    Write-Host ' Starting SheetFlat (the first start installs components: 3-5 minutes) ...'
    Start-Process -FilePath (Join-Path $Dir 'start.bat') -WorkingDirectory $Dir
}
