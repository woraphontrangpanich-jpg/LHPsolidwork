@echo off
rem Update SheetFlat in this folder to the latest version on GitHub (keeps settings and installed components).
cd /d "%~dp0"
echo.
echo  Updating SheetFlat ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Invoke-RestMethod 'https://raw.githubusercontent.com/woraphontrangpanich-jpg/LHPsolidwork/main/install.ps1'))) -Dir '%~dp0.' -NoStart"
if errorlevel 1 (
  echo  Update failed - check the internet connection.
  pause
  exit /b 1
)
echo  Done. Double-click start.bat to run the new version.
pause
