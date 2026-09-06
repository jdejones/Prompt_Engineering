@echo off
setlocal

set "PROJECT_DIR=C:\Users\jdejo\Prompt_Engineering"
set "VENV_PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_DIR=%PROJECT_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\grok_mcp_news_server.log"

set "MCP_SERVER_NAME=Grok Read-Only MySQL News MCP"
set "MCP_HOST=127.0.0.1"
set "MCP_PORT=8002"
set "MCP_TRANSPORT=streamable-http"
set "MCP_ENABLE_WRITE_TOOLS=0"

set "MCP_AUTH_MODE=static"
set "MCP_AUTH_ENABLED=1"
if not defined GROK_MCP_BASE_URL set "GROK_MCP_BASE_URL=https://home-pc.tail701e72.ts.net/mcp"
set "MCP_BASE_URL=%GROK_MCP_BASE_URL%"
set "MCP_STATIC_BEARER_TOKEN=%GROK_MCP_BEARER_TOKEN%"
set "AUTH_REQUIRED_SCOPES=news.read"

set "MCP_ALLOWED_HOSTS=127.0.0.1:8002,localhost:8002,home-pc.tail701e72.ts.net"
set "MCP_ALLOWED_ORIGINS=http://127.0.0.1:8002,http://localhost:8002"
if defined GROK_MCP_ALLOWED_HOSTS set "MCP_ALLOWED_HOSTS=%GROK_MCP_ALLOWED_HOSTS%"
if defined GROK_MCP_ALLOWED_ORIGINS set "MCP_ALLOWED_ORIGINS=%GROK_MCP_ALLOWED_ORIGINS%"

set "MYSQL_HOST=127.0.0.1"
set "MYSQL_PORT=3306"
set "MYSQL_USER=grokdb"
set "MYSQL_DATABASE=news"
set "MYSQL_CONNECT_TIMEOUT=8"
set "MYSQL_READ_TIMEOUT=15"
set "MYSQL_PASSWORD=%grokdb%"

set "MCP_MAX_ROWS=1200"
set "MCP_MAX_SCAN_SYMBOLS=50"

title Grok Read-Only MCP Server
cls
echo ==========================================
echo Grok Read-Only MCP Server
echo Started by Windows Task Scheduler or manually
echo.
echo Local URL: http://%MCP_HOST%:%MCP_PORT%/mcp
echo Public URL: %MCP_BASE_URL%
echo MySQL user: %MYSQL_USER%
echo Write tools: disabled
echo MCP authentication: static Bearer token
echo Log: %LOG_FILE%
echo.
echo Expose this server only through Tailscale Funnel.
echo Do not expose MySQL port 3306.
echo ==========================================
echo.

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] Starting Grok read-only MCP server startup script...> "%LOG_FILE%"

if not exist "%VENV_PYTHON%" (
    echo [%date% %time%] ERROR: Virtual environment Python was not found at "%VENV_PYTHON%".>> "%LOG_FILE%"
    exit /b 1
)

if "%MYSQL_PASSWORD%"=="" (
    echo [%date% %time%] ERROR: Environment variable grokdb is not set.>> "%LOG_FILE%"
    echo ERROR: Environment variable grokdb is not set.
    exit /b 1
)

if "%MCP_STATIC_BEARER_TOKEN%"=="" (
    echo [%date% %time%] ERROR: Environment variable GROK_MCP_BEARER_TOKEN is not set.>> "%LOG_FILE%"
    echo ERROR: Environment variable GROK_MCP_BEARER_TOKEN is not set.
    exit /b 1
)

cd /d "%PROJECT_DIR%"

echo [%date% %time%] Installing/updating requirements...>> "%LOG_FILE%"
"%VENV_PYTHON%" -m pip install -r requirements.txt >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    echo [%date% %time%] ERROR: pip install failed. See log output above.>> "%LOG_FILE%"
    exit /b 1
)

echo [%date% %time%] Launching Grok python -m mcp_news_server...>> "%LOG_FILE%"
"%VENV_PYTHON%" -m mcp_news_server >> "%LOG_FILE%" 2>&1

echo [%date% %time%] Grok MCP news server process exited with code %errorlevel%.>> "%LOG_FILE%"
exit /b %errorlevel%
