# YouTube 切り抜き動画 自動生成ツール

YouTubeの動画から切り抜き動画を自動生成し、指定したチャンネルへ自動アップロードするツールです。

## できること

- YouTube動画を自動ダウンロード
- Whisperで動画を文字起こし（ローカル処理・日本語対応）
- GPT-4oが内容を分析して「盛り上がり箇所」「重要箇所」を自動特定
- 指定した本数・時間の切り抜き動画を一括生成（フェードイン/アウト付き）
- 切り抜き動画ごとにタイトル・概要欄・タグをAIが自動生成
- YouTubeへ自動アップロード（プライベート/公開/限定公開）
- 公開予約（日時指定・複数動画の間隔指定）

## セットアップ

### 1. 必要なソフトウェア

- Python 3.10以上
- ffmpeg（動画処理）

**ffmpegのインストール (Windows)**
```
winget install FFmpeg
```
または [https://ffmpeg.org/download.html](https://ffmpeg.org/download.html) からダウンロード

### 2. Pythonパッケージのインストール

```bash
pip install -r requirements.txt
```

### 3. 環境変数の設定

`.env.example` をコピーして `.env` を作成し、APIキーを設定してください。

```bash
copy .env.example .env
```

`.env` ファイルを編集：

```env
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx   # OpenAI APIキー
WHISPER_MODEL=large-v3               # Whisperモデル（精度/速度のバランス）
WHISPER_DEVICE=cpu                   # cpu または cuda (GPU)
```

### 4. YouTube APIの設定

**Google Cloud Consoleで設定が必要です：**

1. [Google Cloud Console](https://console.cloud.google.com/) にアクセス
2. 新しいプロジェクトを作成（または既存のプロジェクトを選択）
3. 「APIとサービス」→「ライブラリ」→「YouTube Data API v3」を有効化
4. 「APIとサービス」→「認証情報」→「認証情報を作成」→「OAuthクライアントID」
5. アプリケーションの種類：「デスクトップアプリ」を選択
6. 作成したクライアントIDのJSONをダウンロードし、`client_secrets.json` という名前でプロジェクトルートに保存

## 使い方

### 基本的な使い方

```bash
# YouTube動画から切り抜きを3本作成してアップロード（デフォルト設定）
python main.py run https://www.youtube.com/watch?v=XXXXXXXXXXX

# 切り抜き本数を指定
python main.py run https://youtu.be/XXXXXXXXXXX --clips 5

# アップロードせずに切り抜きファイルだけ作成
python main.py run https://youtu.be/XXXXXXXXXXX --no-upload

# 公開設定を変更してアップロード
python main.py run https://youtu.be/XXXXXXXXXXX --privacy public
```

### 公開予約

```bash
# 2026年5月1日10:00から24時間ごとに公開予約
python main.py run https://youtu.be/XXXXXXXXXXX \
  --schedule "2026-05-01 10:00" \
  --schedule-interval 24

# 12時間ごとに5本公開予約
python main.py run https://youtu.be/XXXXXXXXXXX \
  --clips 5 \
  --schedule "2026-05-01 09:00" \
  --schedule-interval 12
```

### 切り抜き時間の指定

```bash
# 1分〜5分の切り抜きを作成
python main.py run https://youtu.be/XXXXXXXXXXX --min-duration 60 --max-duration 300

# 30秒〜3分の短い切り抜き
python main.py run https://youtu.be/XXXXXXXXXXX --min-duration 30 --max-duration 180
```

### その他のコマンド

```bash
# 動画情報を確認（ダウンロードなし）
python main.py info https://youtu.be/XXXXXXXXXXX

# 文字起こしのみ実行
python main.py transcribe https://youtu.be/XXXXXXXXXXX
python main.py transcribe https://youtu.be/XXXXXXXXXXX --whisper-model medium  # 軽量モデル使用
```

### すべてのオプション

```bash
python main.py run --help
```

```
Options:
  -n, --clips INTEGER          切り抜き本数 [default: 3]
  --min-duration INTEGER       切り抜き最短時間（秒） [default: 60]
  --max-duration INTEGER       切り抜き最長時間（秒） [default: 600]
  --privacy [public|private|unlisted]  プライバシー設定 [default: private]
  --schedule TEXT              最初の公開予約日時 (例: '2026-05-01 10:00')
  --schedule-interval INTEGER  公開間隔（時間） [default: 24]
  --no-upload                  アップロードをスキップ
  --output-dir TEXT            出力ディレクトリ
  --config TEXT                設定ファイルパス [default: config.yaml]
  --keep-original              元動画を保持
  --whisper-model TEXT         Whisperモデルサイズ
  --device [cpu|cuda]          処理デバイス
```

## Whisperモデルの選択

| モデル | 精度 | 速度 | VRAMの目安 | 推奨用途 |
|--------|------|------|-----------|---------|
| `tiny` | 低 | 最速 | ~1GB | テスト |
| `base` | 低〜中 | 速い | ~1GB | 簡易確認 |
| `small` | 中 | 普通 | ~2GB | バランス重視 |
| `medium` | 高 | 遅い | ~5GB | 精度重視 |
| `large-v3` | 最高 | 最遅 | ~10GB | 本番推奨 |

CPUでも動作しますが、`large-v3`は時間がかかります。GPUがある場合は`.env`で`WHISPER_DEVICE=cuda`に設定してください。

## 設定ファイル (config.yaml)

`config.yaml` で詳細な動作をカスタマイズできます：

- **切り抜き設定**: デフォルトの長さ、フェードイン/アウト、動画品質
- **分析設定**: GPTへの指示プロンプト、スコア閾値
- **アップロード設定**: カテゴリ、言語、概要欄フッター
- **文字起こし設定**: 言語、VADフィルター

## ディレクトリ構成

```
Auto-youtube-kirinuki/
├── main.py                   # メインエントリーポイント
├── requirements.txt          # 依存パッケージ
├── config.yaml               # 設定ファイル
├── .env                      # APIキー等（.gitignoreに含めること）
├── client_secrets.json       # YouTube OAuth設定（.gitignoreに含めること）
├── src/
│   ├── downloader.py         # YouTube動画ダウンロード
│   ├── transcriber.py        # Whisper文字起こし
│   ├── analyzer.py           # GPT切り抜き箇所分析
│   ├── clipper.py            # ffmpeg動画切り抜き
│   ├── metadata_generator.py # タイトル・概要欄生成
│   └── uploader.py           # YouTube APIアップロード
├── output/                   # 切り抜き動画の出力先
└── temp/                     # 一時ファイル（元動画、文字起こし等）
```

## 注意事項

- **著作権**: 切り抜き動画を公開する際は、元動画の著作権・利用規約を必ず確認してください
- **YouTube利用規約**: YouTubeのAPIサービス利用規約を遵守してください
- **OpenAI APIコスト**: GPT-4oの使用には費用が発生します（1動画あたり数十円程度）
- **認証トークン**: 初回アップロード時にブラウザでGoogle認証が必要です（`youtube_token.pickle`に保存）
- `.env` と `client_secrets.json` と `youtube_token.pickle` は絶対にGitにコミットしないでください

## トラブルシューティング

**ffmpegが見つからない**: PATHにffmpegが通っているか確認してください
```bash
ffmpeg -version
```

**文字起こしが遅い**: `--whisper-model small` または `--whisper-model medium` を試してください

**YouTube APIエラー `quotaExceeded`**: YouTube API の1日あたりのクォータ(10,000ユニット)を超えました。翌日に再試行してください

**認証エラー**: `youtube_token.pickle` を削除して再認証してください
```bash
del youtube_token.pickle
```
