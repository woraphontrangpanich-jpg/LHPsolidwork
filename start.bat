@echo off
setlocal EnableExtensions
rem ===================================================================
rem  SheetFlat - double-click to start.
rem  First run: finds (or installs) Python, then installs the program.
rem ===================================================================
cd /d "%~dp0"
title SheetFlat
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"

if exist "%VPY%" goto haveenv

echo.
echo  [1/3] Looking for Python 3.10 or newer ...
call :findpy
if defined PY goto makeenv

echo  Python not found. Installing Python 3.12 (needs internet, about 1-2 minutes) ...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :findpy
if defined PY goto makeenv

echo.
echo  ------------------------------------------------------------------
echo   Could not install Python automatically.
echo   1. Download Python 3.12 from  https://www.python.org/downloads/
echo   2. In the installer tick  "Add python.exe to PATH"  then Install
echo   3. Double-click start.bat again
echo  ------------------------------------------------------------------
pause
exit /b 1

:makeenv
echo  [2/3] Using %PY%
"%PY%" -m venv "%VENV%"
if errorlevel 1 goto fail_env

:haveenv
fc /b requirements.txt "%VENV%\installed-requirements.txt" >nul 2>&1
if not errorlevel 1 goto run
echo.
echo  [3/3] Installing program components - first time takes 2-5 minutes, please wait ...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail_pip
copy /y requirements.txt "%VENV%\installed-requirements.txt" >nul

:run
if defined SHEETFLAT_NO_RUN exit /b 0
echo.
echo  ==================================================================
echo   SheetFlat is running:  http://127.0.0.1:8765
echo   The browser opens automatically. Keep this window open while
echo   you work - close it to stop the program.
echo  ==================================================================
"%VPY%" run.py
if errorlevel 1 pause
exit /b 0

:fail_env
echo  Could not create the Python environment. Try running start.bat again.
pause
exit /b 1

:fail_pip
echo.
echo  Installing components failed - check the internet connection and run start.bat again.
rd /s /q "%VENV%" 2>nul
pause
exit /b 1

rem ------------------------------------------------------------------- find a usable python
:findpy
set "PY="
for %%V in (3.12 3.13 3.11 3.14 3.10) do call :trypy py -%%V
if not defined PY call :trypy python
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY call :trypy "%%D\python.exe"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-3*") do if not defined PY call :trypy "%%D\python.exe"
exit /b 0

:trypy
if defined PY exit /b 0
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
for /f "delims=" %%P in ('%* -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
exit /b 0
