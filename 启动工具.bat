
@echo off
chcp 65001 >nul
title 舆情监控工具

echo ╔══════════════════════════════════╗
echo ║       舆情监控工具  启动中        ║
echo ╚══════════════════════════════════╝
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 安装依赖（首次运行）
echo [1/2] 检查并安装依赖...
pip install -r requirements.txt -q --disable-pip-version-check

echo [2/2] 启动服务...
echo.
echo  请在浏览器访问：http://localhost:5000
echo  按 Ctrl+C 关闭服务
echo.

:: 自动打开浏览器（延迟 2 秒）
start "" cmd /c "timeout /t 2 >nul && start http://localhost:5000"

python app.py
pause
