@echo off
setlocal

set "PROJECT_DIR=C:\Users\jdejo\Prompt_Engineering"
set "VENV_PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_DIR=%PROJECT_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\openclaw_mcp_news_server.log"

set "MCP_SERVER_NAME=OpenClaw MySQL News MCP"
set "MCP_HOST=127.0.0.1"
set "MCP_PORT=8001"
set "MCP_TRANSPORT=streamable-http"
set "MCP_AUTH_ENABLED=0"
set "MCP_BASE_URL="
set "MCP_ALLOWED_HOSTS=127.0.0.1:8001,localhost:8001,home-pc.tail701e72.ts.net:8001,100.78.220.116:8001"
set "MCP_ALLOWED_ORIGINS=http://127.0.0.1:8001,http://localhost:8001,http://home-pc.tail701e72.ts.net:8001,http://100.78.220.116:8001"
set "MCP_ENABLE_WRITE_TOOLS=1"

set "MYSQL_HOST=127.0.0.1"
set "MYSQL_PORT=3306"
set "MYSQL_USER=openclawdb"
set "MYSQL_DATABASE=news"
set "MYSQL_CONNECT_TIMEOUT=8"
set "MYSQL_READ_TIMEOUT=15"
set "MYSQL_PASSWORD=%openclawdb%"

set "MCP_MAX_ROWS=1200"
set "MCP_MAX_SCAN_SYMBOLS=50"

title OpenClaw MCP Server
cls
echo ==========================================
echo OpenClaw MCP Server
echo Started by Windows Task Scheduler or manually
echo.
echo URL: http://%MCP_HOST%:%MCP_PORT%/mcp
echo Log: %LOG_FILE%
echo.
echo Do not close this window while using the server.
echo ==========================================
echo.

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] Starting OpenClaw MCP server startup script...> "%LOG_FILE%"

if not exist "%VENV_PYTHON%" (
    echo [%date% %time%] ERROR: Virtual environment Python was not found at "%VENV_PYTHON%".>> "%LOG_FILE%"
    exit /b 1
)

if "%MYSQL_PASSWORD%"=="" (
    echo [%date% %time%] ERROR: Environment variable openclawdb is not set.>> "%LOG_FILE%"
    echo ERROR: Environment variable openclawdb is not set.
    exit /b 1
)

cd /d "%PROJECT_DIR%"

echo [%date% %time%] Installing/updating requirements...>> "%LOG_FILE%"
"%VENV_PYTHON%" -m pip install -r requirements.txt >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    echo [%date% %time%] ERROR: pip install failed. See log output above.>> "%LOG_FILE%"
    exit /b 1
)

echo [%date% %time%] Launching OpenClaw python -m mcp_news_server...>> "%LOG_FILE%"
"%VENV_PYTHON%" -m mcp_news_server >> "%LOG_FILE%" 2>&1

echo [%date% %time%] OpenClaw MCP news server process exited with code %errorlevel%.>> "%LOG_FILE%"
exit /b %errorlevel%
