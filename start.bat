@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

echo.
echo  YouTube 切り抜きツール を起動中...
echo.

REM パスを最新化（winget でインストールしたffmpegを認識させる）
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"

pip install -q fastapi uvicorn[standard] python-multipart 2>nul

echo  ブラウザで http://localhost:8000 を開いてください
echo  終了するには Ctrl+C を押してください
echo.
start "" http://localhost:8000

python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
