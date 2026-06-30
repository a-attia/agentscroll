@echo off
REM Double-clickable launcher for Windows.
REM Starts the agentscroll web app and opens it in your default browser.

where agentscroll >nul 2>nul
if %errorlevel%==0 (
  agentscroll web
  goto :eof
)

python -c "import agentscroll" >nul 2>nul
if %errorlevel%==0 (
  python -m agentscroll.cli web
  goto :eof
)

echo agentscroll is not installed.
echo Install it with:  pip install "agentscroll[web]"
pause
