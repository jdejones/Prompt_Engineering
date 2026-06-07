@echo off
setlocal

set "PROJECT_DIR=C:\Users\jdejo\Prompt_Engineering"
set "VENV_PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_DIR=%PROJECT_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\mcp_news_server.log"

title gptdb MCP Server
cls
echo ==========================================
echo gptdb MCP Server
echo Started by Windows Task Scheduler
echo.
echo URL: http://127.0.0.1:8000/mcp
echo Log: %LOG_FILE%
echo.
echo Do not close this window while using the server.
echo ==========================================
echo.

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] Starting gptdb MCP server startup script...> "%LOG_FILE%"

if not exist "%VENV_PYTHON%" (
    echo [%date% %time%] ERROR: Virtual environment Python was not found at "%VENV_PYTHON%".>> "%LOG_FILE%"
    exit /b 1
)

cd /d "%PROJECT_DIR%"

echo [%date% %time%] Installing/updating requirements...>> "%LOG_FILE%"
"%VENV_PYTHON%" -m pip install -r requirements.txt >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    echo [%date% %time%] ERROR: pip install failed. See log output above.>> "%LOG_FILE%"
    exit /b 1
)

echo [%date% %time%] Launching python -m mcp_news_server...>> "%LOG_FILE%"
"%VENV_PYTHON%" -m mcp_news_server >> "%LOG_FILE%" 2>&1

echo [%date% %time%] MCP news server process exited with code %errorlevel%.>> "%LOG_FILE%"
exit /b %errorlevel%
