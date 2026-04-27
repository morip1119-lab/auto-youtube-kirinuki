#!/bin/bash
# ============================================================
# Nginx リバースプロキシ設定スクリプト
# 実行前に DOMAIN 変数を書き換えてください
# ============================================================

DOMAIN="your-domain.com"   # ← ドメインまたはIPアドレスに変更

cat > /etc/nginx/sites-available/kirinuki << EOF
server {
    listen 80;
    server_name ${DOMAIN};

    # 大きなファイルのアップロード・レスポンスに対応
    client_max_body_size 500M;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sf /etc/nginx/sites-available/kirinuki /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "Nginx 設定完了: http://${DOMAIN} でアクセス可能"
echo ""
echo "HTTPS化する場合 (Let's Encrypt):"
echo "  apt install certbot python3-certbot-nginx"
echo "  certbot --nginx -d ${DOMAIN}"
