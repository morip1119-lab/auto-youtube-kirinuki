"""
シンプルなパスワード認証ユーティリティ
Cookie ベースのトークン認証を提供する
"""
import os
import hmac
import hashlib
from datetime import datetime, timezone

COOKIE_NAME = "kirinuki_auth"

# .env から読み込む (未設定なら起動拒否はしないが警告)
def _get_secret() -> str:
    s = os.environ.get("APP_SECRET_KEY", "changeme-please-set-a-secret")
    return s

def _get_password() -> str:
    return os.environ.get("APP_PASSWORD", "")


def make_token(password: str) -> str:
    """正しいパスワードからセッショントークンを生成する"""
    secret = _get_secret()
    raw = f"{password}:{secret}:kirinuki"
    return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


def verify_token(token: str) -> bool:
    """Cookie に保存されたトークンが有効か検証する"""
    expected = make_token(_get_password())
    if not expected:
        return False
    return hmac.compare_digest(token, expected)


def password_configured() -> bool:
    """APP_PASSWORD が設定されているか"""
    return bool(_get_password())
