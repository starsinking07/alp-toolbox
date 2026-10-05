@echo off
rem 黑鲨风神Pro 工具箱 启动脚本
rem 以管理员运行可读取 CPU 温度 (智能变频用); 不提权则使用 GPU 温度
cd /d "%~dp0"
python main.py
if errorlevel 1 pause
