@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title research-tool 一键调研

echo ============================================================
echo            research-tool  一键调研工具
echo   主题/PDF  ^|  搜索 + 清洗 + 知识树 + 报告  ^|  DeepSeek
echo ============================================================
echo.

REM ---------- 1. 检查 Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python。请先安装 Python 3.11+ 并勾选 "Add to PATH"。
    echo        下载: https://www.python.org/downloads/
    pause & exit /b 1
)

REM ---------- 2. 首次安装（创建 venv + 装依赖）----------
if not exist ".venv\Scripts\research.exe" (
    echo [首次运行] 创建虚拟环境并安装依赖，约 2-5 分钟...
    if not exist ".venv\Scripts\python.exe" python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install -U pip >nul
    echo   安装 research-tool + 搜索后端 + LLM 客户端...
    pip install -e ".[search]" openai
    if errorlevel 1 ( echo [错误] 依赖安装失败 & pause & exit /b 1 )
    echo [完成] 安装成功。
    echo.
) else (
    call ".venv\Scripts\activate.bat"
)

REM ---------- 3. 载入 .env 里的密钥（小写名 → 大写环境变量）----------
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
    echo [配置] 已从 !ENVFILE! 载入密钥。
)
if not defined DEEPSEEK_API_KEY (
    echo.
    echo 未找到 DeepSeek API Key。请输入（输入后会保存到 .env 供下次使用）：
    set /p DEEPSEEK_API_KEY=DeepSeek API Key:
    >>".env" echo deepseek_api_key=!DEEPSEEK_API_KEY!
)

REM 选搜索来源：有 Tavily key 用 tavily（稳定），否则用免费 web
if defined TAVILY_API_KEY ( set "SRC=-s tavily" ) else ( set "SRC=-s web" )

REM PDF 模式用的 mineru（默认指向 pdf2zh 的虚拟环境）
set "MINERU=C:\Users\yhn\pdf2zh\.venv\Scripts\mineru.exe"

:menu
echo.
echo ------------------------------------------------------------
echo   1. 网页调研   （输入主题，自动搜索→报告）
echo   2. PDF 调研    （选本地 PDF 文件夹，MinerU 解析→报告）
echo   3. 查看进度    （某主题做到哪一步了）
echo   4. 高级命令行  （手动敲 research ...）
echo   0. 退出
echo ------------------------------------------------------------
set "choice="
set /p choice=请选择 [1/2/3/4/0]:

if "%choice%"=="1" goto web
if "%choice%"=="2" goto pdf
if "%choice%"=="3" goto status
if "%choice%"=="4" goto shell
if "%choice%"=="0" goto end
echo 无效选择。& goto menu

:web
echo.
set "topic="
set /p topic=请输入调研主题（如：扩散模型综述）:
if "!topic!"=="" ( echo 主题不能为空。& goto menu )
set "rounds="
set /p rounds=搜索轮次 1-3（直接回车=2）:
if "!rounds!"=="" set "rounds=2"
echo.
echo 开始调研：!topic!   来源 !SRC!   轮次 !rounds!
research run "!topic!" !SRC! -r !rounds! --skip extract
echo.
echo 完成。输出在 research-output\ 下对应主题文件夹（report.md 即报告）。
pause & goto menu

:pdf
echo.
set "pdfdir="
set /p pdfdir=请输入 PDF 文件或文件夹路径:
if "!pdfdir!"=="" ( echo 路径不能为空。& goto menu )
set "ptopic="
set /p ptopic=给这批 PDF 起个主题名:
if "!ptopic!"=="" set "ptopic=pdf-调研"
set "tr="
set /p tr=是否把英文 PDF 翻成中文? (y/N):
set "TRFLAG="
if /i "!tr!"=="y" set "TRFLAG=--translate"

set "MCMD="
if exist "!MINERU!" (
    set "MCMD=--mineru-cmd "!MINERU!""
) else (
    where mineru >nul 2>&1
    if errorlevel 1 (
        echo.
        echo 未找到 mineru。请输入 mineru.exe 完整路径（pdf2zh 的 .venv\Scripts\mineru.exe）：
        set /p MPATH=mineru 路径:
        if not "!MPATH!"=="" set "MCMD=--mineru-cmd "!MPATH!""
    )
)
echo.
echo 开始 PDF 调研：!ptopic!   !TRFLAG!
research run "!ptopic!" --pdf-dir "!pdfdir!" !MCMD! !TRFLAG! --skip extract
echo.
echo 完成。报告在 research-output\ 下对应主题文件夹的 report.md。
pause & goto menu

:status
echo.
set "spath="
set /p spath=请输入主题输出目录（research-output\xxx）:
research status "!spath!"
pause & goto menu

:shell
echo.
echo 已进入 research-tool 环境。可直接用 research 命令，例如：
echo    research run "主题" -s web -r 2 --skip extract
echo    research ingest-pdf .\papers -T "主题" --mineru-cmd "...\mineru.exe"
echo    research --help
echo 输入 exit 退出该命令行后回到菜单。
cmd /k
goto menu

:end
echo 再见。
endlocal
