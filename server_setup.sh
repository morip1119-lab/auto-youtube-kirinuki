#!/bin/bash
# ============================================================
# Xserver VPS 初期セットアップスクリプト
# Ubuntu 22.04 LTS 用
# 実行方法: bash server_setup.sh
# ============================================================

set -e

echo "=== [1/7] システム更新 ==="
apt-get update && apt-get upgrade -y

echo "=== [2/7] 必要パッケージをインストール ==="
apt-get install -y \
    python3.11 python3.11-venv python3-pip \
    ffmpeg \
    git \
    nginx \
    fonts-noto-cjk \
    curl \
    unzip

echo "=== [3/7] アプリユーザーを作成 ==="
id -u kirinuki &>/dev/null || useradd -m -s /bin/bash kirinuki

echo "=== [4/7] アプリをクローン ==="
mkdir -p /opt/kirinuki
cd /opt/kirinuki
if [ ! -d ".git" ]; then
    git clone https://github.com/morip1119-lab/auto-youtube-kirinuki.git .
else
    echo "既にクローン済み — git pull を実行"
    git pull
fi

echo "=== [5/7] Python 仮想環境 & 依存関係インストール ==="
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== [6/7] ディレクトリ作成 ==="
mkdir -p /opt/kirinuki/{output,temp,static}
chown -R kirinuki:kirinuki /opt/kirinuki

echo "=== [7/7] systemd サービスを登録 ==="
cat > /etc/systemd/system/kirinuki.service << 'EOF'
[Unit]
Description=YouTube Kirinuki Tool
After=network.target

[Service]
Type=simple
User=kirinuki
WorkingDirectory=/opt/kirinuki
EnvironmentFile=/opt/kirinuki/.env
ExecStart=/opt/kirinuki/venv/bin/uvicorn api.server:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable kirinuki

echo ""
echo "============================================"
echo " セットアップ完了！"
echo ""
echo " 次のステップ:"
echo "   1. /opt/kirinuki/.env を作成 (cp .env.example .env && nano .env)"
echo "   2. systemctl start kirinuki"
echo "   3. nginx を設定 (run_nginx_setup.sh を実行)"
echo "============================================"
