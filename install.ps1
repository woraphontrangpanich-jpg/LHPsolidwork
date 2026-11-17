# SheetFlat installer / updater for Windows (no git needed).
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/woraphontrangpanich-jpg/LHPsolidwork/main/install.ps1 | iex"
#
# Downloads the latest version from GitHub into %LOCALAPPDATA%\SheetFlat, keeps the installed
# components (.venv) and settings (data\), adds a "SheetFlat" shortcut on the desktop and starts it.
# Running the same command again updates the program.
param(
    [string]$Dir = "$env:LOCALAPPDATA\SheetFlat",
    [switch]$NoStart,
    [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$zipUrl = 'https://github.com/woraphontrangpanich-jpg/LHPsolidwork/archive/refs/heads/main.zip'

$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
$tmp = Join-Path $env:TEMP ("sheetflat_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Write-Host ''
    Write-Host ' Downloading SheetFlat ...'
    Invoke-WebRequest -Uri $zipUrl -OutFile "$tmp\sheetflat.zip" -UseBasicParsing
    Expand-Archive -Path "$tmp\sheetflat.zip" -DestinationPath $tmp
    $src = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    # copy the new version, keep installed components and settings
    robocopy $src.FullName $Dir /E /XD .venv data /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copy failed ($LASTEXITCODE)" }
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

Write-Host " Installed to $Dir"
if (-not $NoStart) {
    Write-Host ' Starting SheetFlat (first start installs components: 2-5 minutes) ...'
    Start-Process -FilePath (Join-Path $Dir 'start.bat') -WorkingDirectory $Dir
}
