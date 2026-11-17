@echo off
rem ===================================================================
rem  SheetFlat setup - double-click to install or update SheetFlat.
rem  1. asks where to install   2. downloads the latest version from GitHub
rem  3. adds a desktop shortcut  4. starts the program
rem ===================================================================
title SheetFlat setup
echo.
echo  SheetFlat setup
echo  ---------------
echo  A window will ask where to install SheetFlat ...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; & ([scriptblock]::Create((Invoke-RestMethod 'https://raw.githubusercontent.com/woraphontrangpanich-jpg/LHPsolidwork/main/install.ps1'))) -Pick"
if errorlevel 1 goto failed
echo.
echo  Done. This window can be closed.
timeout /t 8 >nul
exit /b 0

:failed
echo.
echo  Setup failed. Check the internet connection and run SheetFlat-Setup.cmd again.
echo  Guide: https://github.com/woraphontrangpanich-jpg/LHPsolidwork/blob/main/INSTALL.md
pause
exit /b 1
