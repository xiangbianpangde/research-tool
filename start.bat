@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title research-tool

echo ============================================================
echo            research-tool  (one-click launcher)
echo   topic / PDF  -^>  search + clean + tree + report  (DeepSeek)
echo ============================================================
echo.

REM ---------- 1. check Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+ and check "Add to PATH".
    echo         https://www.python.org/downloads/
    pause & exit /b 1
)

REM ---------- 2. first-run setup (venv + deps) ----------
if not exist ".venv\Scripts\research.exe" (
    echo [setup] Creating venv and installing deps, 2-5 min...
    if not exist ".venv\Scripts\python.exe" python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install -U pip >nul
    echo   installing research-tool + search backends + web UI + LLM client...
    pip install -e ".[search,ui]" openai
    if errorlevel 1 ( echo [ERROR] dependency install failed & pause & exit /b 1 )
    echo [done] installed.
    echo.
) else (
    call ".venv\Scripts\activate.bat"
)

REM ensure the package is importable even though the project path contains
REM non-ASCII chars (editable-install .pth fails to resolve such paths)
set "PYTHONPATH=%~dp0"

REM ---------- 3. load API keys from .env ----------
set "ENVFILE="
if exist ".env"            set "ENVFILE=.env"
if not defined ENVFILE if exist "..\.env"             set "ENVFILE=..\.env"
if not defined ENVFILE if exist "%USERPROFILE%\.env"  set "ENVFILE=%USERPROFILE%\.env"
if defined ENVFILE (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in ("!ENVFILE!") do (
        set "k=%%a" & set "v=%%b"
        if /i "!k!"=="deepseek_api_key" set "DEEPSEEK_API_KEY=!v!"
        if /i "!k!"=="tavily_api_key"   set "TAVILY_API_KEY=!v!"
        if /i "!k!"=="DEEPSEEK_API_KEY" set "DEEPSEEK_API_KEY=!v!"
        if /i "!k!"=="TAVILY_API_KEY"   set "TAVILY_API_KEY=!v!"
    )
    echo [config] keys loaded from !ENVFILE!
)
if not defined DEEPSEEK_API_KEY (
    echo.
    echo No DeepSeek API key found. Enter it now ^(saved to .env for next time^):
    set /p DEEPSEEK_API_KEY=DeepSeek API Key:
    >>".env" echo deepseek_api_key=!DEEPSEEK_API_KEY!
)

REM search source: tavily if key present, else free web
if defined TAVILY_API_KEY ( set "SRC=-s tavily" ) else ( set "SRC=-s web" )

REM mineru for PDF mode (points at pdf2zh venv by default)
set "MINERU=C:\Users\yhn\pdf2zh\.venv\Scripts\mineru.exe"

:menu
echo.
echo ------------------------------------------------------------
echo   1. Web UI        (recommended, point-and-click in browser)
echo   2. Web research  (CLI: enter a topic)
echo   3. PDF research  (CLI: local PDF folder via MinerU)
echo   4. Show progress (how far a topic got)
echo   5. Advanced shell(type research ... yourself)
echo   0. Exit
echo ------------------------------------------------------------
set "choice="
set /p choice=Select [1/2/3/4/5/0]:

if "%choice%"=="1" goto webui
if "%choice%"=="2" goto web
if "%choice%"=="3" goto pdf
if "%choice%"=="4" goto status
if "%choice%"=="5" goto shell
if "%choice%"=="0" goto end
echo Invalid choice.& goto menu

:webui
echo.
echo Starting Web UI. The browser opens automatically; the actual
echo address (http://127.0.0.1:PORT) is printed below.
echo (back to menu: press Ctrl+C in this window to stop the server)
research ui
pause & goto menu

:web
echo.
set "topic="
set /p topic=Research topic (e.g. diffusion models survey):
if "!topic!"=="" ( echo Topic cannot be empty.& goto menu )
set "rounds="
set /p rounds=Search rounds 1-3 (Enter = 2):
if "!rounds!"=="" set "rounds=2"
echo.
echo Researching: !topic!   source !SRC!   rounds !rounds!
research run "!topic!" !SRC! -r !rounds! --skip extract
echo.
echo Done. See research-output\<topic>\report.md
pause & goto menu

:pdf
echo.
set "pdfdir="
set /p pdfdir=PDF file or folder path:
if "!pdfdir!"=="" ( echo Path cannot be empty.& goto menu )
set "ptopic="
set /p ptopic=Topic name for these PDFs:
if "!ptopic!"=="" set "ptopic=pdf-research"
set "tr="
set /p tr=Translate English PDF to Chinese? (y/N):
set "TRFLAG="
if /i "!tr!"=="y" set "TRFLAG=--translate"

set "MCMD="
if exist "!MINERU!" (
    set "MCMD=--mineru-cmd "!MINERU!""
) else (
    where mineru >nul 2>&1
    if errorlevel 1 (
        echo.
        echo mineru not found. Enter full path to mineru.exe
        echo  ^(pdf2zh's .venv\Scripts\mineru.exe^):
        set /p MPATH=mineru path:
        if not "!MPATH!"=="" set "MCMD=--mineru-cmd "!MPATH!""
    )
)
echo.
echo PDF research: !ptopic!   !TRFLAG!
research run "!ptopic!" --pdf-dir "!pdfdir!" !MCMD! !TRFLAG! --skip extract
echo.
echo Done. See research-output\<topic>\report.md
pause & goto menu

:status
echo.
set "spath="
set /p spath=Topic output dir (research-output\xxx):
research status "!spath!"
pause & goto menu

:shell
echo.
echo research-tool environment is active. Examples:
echo    research run "topic" -s web -r 2 --skip extract
echo    research ingest-pdf .\papers -T "topic" --mineru-cmd "...\mineru.exe"
echo    research --help
echo Type exit to leave this shell and return to the menu.
cmd /k
goto menu

:end
echo Bye.
endlocal
