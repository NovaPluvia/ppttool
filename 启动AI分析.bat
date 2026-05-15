@echo off
chcp 65001 >nul
title AI 分析服务 - PPT 标注工具

cd /d "%~dp0"

echo ========================================
echo   AI 分析服务启动中...
echo ========================================
echo.

echo 正在启动 Ollama 桥接服务...
echo 请稍候，浏览器将自动打开...
echo.

python scripts\ollama_service.py

if %errorlevel% neq 0 (
    echo.
    echo [错误] 服务启动失败，请确认:
    echo   1. Python 已安装
    echo   2. Ollama 已启动
    echo   3. 执行: pip install Pillow
    echo.
    pause
)
