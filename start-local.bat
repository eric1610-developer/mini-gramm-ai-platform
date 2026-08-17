@echo off
setlocal
cd /d %~dp0
if not exist .env (
  copy .env.example .env >nul
  echo [Mini Gramm] Created .env from .env.example
  echo [Mini Gramm] For real deployment, change JWT_SECRET and POSTGRES_PASSWORD in .env.
)
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo Docker start failed. Make sure Docker Desktop is running.
  pause
  exit /b 1
)
echo.
echo Mini Gramm is starting.
echo Open: http://localhost
echo API health: http://localhost/api/v1/health
pause
