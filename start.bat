@echo off
title YouTube Downloader - Starting...

echo 🎥 Starting YouTube Downloader...
echo 🔧 Environment: Local Windows
echo 🌐 Port: 5000
echo.

echo 📦 Installing dependencies...
pip install -r requirements.txt

echo.
echo 🚀 Launching YouTube Downloader...
echo 📱 Access your app at: http://localhost:5000
echo.

python app.py
pause