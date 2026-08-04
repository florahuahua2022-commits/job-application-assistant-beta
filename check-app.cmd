@echo off
setlocal
chcp 65001 >nul
set "PROJECT_DIR=%~dp0"
set "FAILED=0"

echo ==================================
echo Job Application Assistant - Check
echo ==================================

where python >nul 2>nul && (echo [OK] Python found) || (echo [MISSING] Python & set "FAILED=1")
where node >nul 2>nul && (echo [OK] Node.js found) || (echo [MISSING] Node.js & set "FAILED=1")
where pnpm >nul 2>nul && (echo [OK] pnpm found) || (echo [MISSING] pnpm - install-app.cmd can try Corepack)

if exist "%PROJECT_DIR%backend\.venv\Scripts\python.exe" (echo [OK] Backend environment ready) else (echo [MISSING] Backend environment & set "FAILED=1")
if exist "%PROJECT_DIR%frontend\node_modules\next\dist\bin\next" (echo [OK] Frontend packages ready) else (echo [MISSING] Frontend packages & set "FAILED=1")
if exist "%PROJECT_DIR%backend\.env" (echo [OK] Local configuration exists) else (echo [MISSING] backend\.env & set "FAILED=1")

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul && (echo [RUNNING] Backend port 8000) || echo [STOPPED] Backend port 8000
netstat -ano | findstr /R /C:":3000 .*LISTENING" >nul && (echo [RUNNING] Frontend port 3000) || echo [STOPPED] Frontend port 3000

if "%FAILED%"=="1" (
  echo.
  echo Run install-app.cmd to complete missing setup items.
) else (
  echo.
  echo Setup looks ready. Run start-app.cmd.
)
pause
