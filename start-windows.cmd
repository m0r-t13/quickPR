@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m quickpr %*
    goto finish
)
where python >nul 2>nul
if not errorlevel 1 (
    python -m quickpr %*
    goto finish
)
echo Please install Python 3.9+ from https://www.python.org/downloads/
exit /b 1
:finish
set "quickpr_exit=%errorlevel%"
if "%~1"=="" pause
exit /b %quickpr_exit%
