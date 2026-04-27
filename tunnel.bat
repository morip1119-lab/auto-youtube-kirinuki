@echo off
chcp 65001 > nul
echo ================================================
echo  YouTube切り抜きツール - 外部公開トンネル起動
echo ================================================
echo.

:: cloudflared がなければ自動インストール
where cloudflared >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] cloudflared をインストールします...
    winget install --id Cloudflare.cloudflared -e --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [ERROR] インストールに失敗しました。手動でインストールしてください:
        echo   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
        pause
        exit /b 1
    )
)

:: PATH 更新
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe"

echo [INFO] サーバーが http://localhost:8000 で動いていることを確認してください
echo [INFO] (先に start.bat を実行しておいてください)
echo.
echo [INFO] トンネルを開始します...
echo [INFO] 表示される https://xxxx.trycloudflare.com のURLで誰でもアクセスできます
echo.

cloudflared tunnel --url http://localhost:8000

pause
