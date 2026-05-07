"""
YouTube OAuth2 トークン生成スクリプト
ローカルPC で実行してください。ブラウザが開くので Google アカウントでログインします。
生成された youtube_token.pickle を VPS の /opt/kirinuki/ に配置してください。

使い方:
  python generate_token.py
  # → youtube_token.pickle が生成される
  # → scp youtube_token.pickle root@auto-kirinuki.site:/opt/kirinuki/
"""
import os
import pickle
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "youtube_token.pickle"
SECRETS_FILE = "client_secrets.json"


def main():
    if not Path(SECRETS_FILE).exists():
        print(f"エラー: {SECRETS_FILE} が見つかりません。")
        print("Google Cloud Console からダウンロードして同じフォルダに置いてください。")
        return

    creds = None

    if Path(TOKEN_FILE).exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
        if creds and creds.valid:
            print("既存のトークンは有効です。再生成しますか？ (y/N): ", end="")
            ans = input().strip().lower()
            if ans != "y":
                print("キャンセルしました。")
                return
        elif creds and creds.expired and creds.refresh_token:
            print("トークンを更新中...")
            creds.refresh(Request())
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)
            print(f"トークンを更新しました: {TOKEN_FILE}")
            print_next_step()
            return

    flow = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
    print("ブラウザが開きます。Google アカウントでログインしてください...")
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)

    print(f"\n完了！ {TOKEN_FILE} を生成しました。")
    print_next_step()


def print_next_step():
    print("\n--- 次のステップ ---")
    print("以下のコマンドでVPSにアップロードしてください（ローカルPCのターミナルで実行）:")
    print()
    print("  scp youtube_token.pickle root@auto-kirinuki.site:/opt/kirinuki/")
    print()
    print("VPS側でパーミッションを設定:")
    print("  ssh root@auto-kirinuki.site")
    print("  chown kirinuki:kirinuki /opt/kirinuki/youtube_token.pickle")
    print("  chmod 644 /opt/kirinuki/youtube_token.pickle")
    print()
    print("その後、WebUIの「再認証」ボタンを押してください。")


if __name__ == "__main__":
    main()
