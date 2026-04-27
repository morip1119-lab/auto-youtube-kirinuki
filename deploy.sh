#!/bin/bash
# ============================================================
# デプロイスクリプト (GitHubから最新を反映)
# サーバー上で実行: bash deploy.sh
# ============================================================

APP_DIR="/opt/kirinuki"

echo "=== [1/3] 最新コードを取得 ==="
cd "$APP_DIR"
git pull origin master

echo "=== [2/3] 依存関係を更新 ==="
source venv/bin/activate
pip install -r requirements.txt --quiet

echo "=== [3/3] サービスを再起動 ==="
systemctl restart kirinuki
systemctl status kirinuki --no-pager

echo ""
echo "デプロイ完了！"
