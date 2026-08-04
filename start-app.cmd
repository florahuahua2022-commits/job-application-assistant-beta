@echo off
setlocal
chcp 65001 >nul
set "PROJECT_DIR=%~dp0"

if not exist "%PROJECT_DIR%backend\.venv\Scripts\python.exe" (
  echo [SETUP REQUIRED] Double-click install-app.cmd first.
  pause
  exit /b 1
)

if not exist "%PROJECT_DIR%frontend\node_modules\next\dist\bin\next" (
  echo [SETUP REQUIRED] Frontend packages are missing. Double-click install-app.cmd first.
  pause
  exit /b 1
)

if not exist "%PROJECT_DIR%backend\.env" (
  copy "%PROJECT_DIR%backend\.env.example" "%PROJECT_DIR%backend\.env" >nul
  echo [CONFIG REQUIRED] Add an API key to backend\.env, save it, then run start-app.cmd again.
  start "" notepad "%PROJECT_DIR%backend\.env"
  pause
  exit /b 1
)

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if errorlevel 1 start "Job Assistant Backend" cmd /k "cd /d ""%PROJECT_DIR%backend"" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

netstat -ano | findstr /R /C:":3000 .*LISTENING" >nul
if errorlevel 1 start "Job Assistant Frontend" cmd /k "cd /d ""%PROJECT_DIR%frontend"" && node node_modules\next\dist\bin\next dev"

timeout /t 5 /nobreak >nul
start "" http://localhost:3000
