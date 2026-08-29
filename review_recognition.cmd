@echo off
chcp 65001 >nul
cd /d "%~dp0"
python tools\recognition_review.py
if errorlevel 1 pause
