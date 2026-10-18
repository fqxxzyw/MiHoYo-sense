@echo off
setlocal
chcp 65001 >nul

REM 使用启动文件的位置定位项目，不依赖当前工作目录。
cd /d "%~dp0.."
if errorlevel 1 (
    echo 无法进入项目根目录。
    pause
    exit /b 1
)

REM 使用用户已配置的 hoyosense 环境，无需先执行 conda activate。
set "PYTHON_EXE=D:\Anaconda3\envs\hoyosense\python.exe"
if not exist "%PYTHON_EXE%" (
    echo 未找到 Python：%PYTHON_EXE%
    echo 请修改本文件中的 PYTHON_EXE 为你的环境解释器路径。
    pause
    exit /b 1
)

REM 数量上限、请求间隔及超时继续读取 configs\miyoushe.json。
"%PYTHON_EXE%" scripts\01_collect_miyoushe.py --batch --game all
set "RUN_EXIT_CODE=%ERRORLEVEL%"

echo.
echo 程序已退出，退出代码：%RUN_EXIT_CODE%
echo 已有数据及断点由采集器复用；详细结果请查看上方输出。
pause
exit /b %RUN_EXIT_CODE%
