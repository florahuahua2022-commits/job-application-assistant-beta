@echo off
setlocal
chcp 65001 >nul
set "PROJECT_DIR=%~dp0"

echo ========================================
echo Job Application Assistant - First Setup
echo ========================================

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found. Install Python 3.12 or newer, then run this file again.
  pause
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js was not found. Install Node.js 22 or newer, then run this file again.
  pause
  exit /b 1
)

set "PNPM_CMD=pnpm"
where pnpm >nul 2>nul
if errorlevel 1 (
  where corepack >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] pnpm and Corepack were not found. Run: npm install -g pnpm
    pause
    exit /b 1
  )
  set "PNPM_CMD=corepack pnpm"
)

echo [1/3] Preparing Python environment...
cd /d "%PROJECT_DIR%backend"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto :failed
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo [2/3] Preparing frontend packages...
cd /d "%PROJECT_DIR%frontend"
call %PNPM_CMD% install --frozen-lockfile
if errorlevel 1 goto :failed

echo [3/3] Preparing local configuration...
if not exist "%PROJECT_DIR%backend\.env" copy "%PROJECT_DIR%backend\.env.example" "%PROJECT_DIR%backend\.env" >nul

echo.
echo [DONE] Installation completed.
echo Add your API key to backend\.env, then double-click start-app.cmd.
start "" notepad "%PROJECT_DIR%backend\.env"
pause
exit /b 0

:failed
echo.
echo [ERROR] Installation did not complete. Check the message above and try again.
pause
exit /b 1
