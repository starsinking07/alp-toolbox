@echo off
setlocal
cd /d "%~dp0.."
python -B tools/build_release.py %*
exit /b %errorlevel%
