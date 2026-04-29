"""
YouTubeアップロードモジュール
YouTube Data API v3 を使用して動画をアップロード・公開予約する
"""
import os
import json
import time
import pickle
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TransferSpeedColumn

from .metadata_generator import VideoMetadata
from .clipper import ClipResult

console = Console()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_PICKLE = "youtube_token.pickle"

# アップロードのチャンクサイズ（10MB）
CHUNK_SIZE = 10 * 1024 * 1024
# リトライ最大回数
MAX_RETRIES = 5
# HTTPステータスコード（リトライ対象）
RETRIABLE_STATUS_CODES = {500, 502, 503, 504}


@dataclass
class UploadResult:
    """アップロード結果を表すデータクラス"""
    clip_result: ClipResult
    metadata: VideoMetadata
    video_id: str = ""
    video_url: str = ""
    success: bool = False
    error_message: str = ""
    scheduled_at: Optional[datetime] = None

    def __str__(self) -> str:
        if self.success:
            status = f"✓ {self.video_url}"
            if self.scheduled_at:
                status += f" (公開予約: {self.scheduled_at.strftime('%Y/%m/%d %H:%M')})"
            return status
        return f"✗ アップロード失敗: {self.error_message}"


class YouTubeUploader:
    """YouTube Data API v3 を使用した動画アップロードクラス"""

    def __init__(
        self,
        client_secrets_file: str = "client_secrets.json",
        token_file: str = TOKEN_PICKLE,
        default_privacy: str = "private",
        category_id: str = "22",
        description_footer: str = "",
    ):
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self.default_privacy = default_privacy
        self.category_id = category_id
        self.description_footer = description_footer
        self._youtube = None

    def authenticate(self) -> None:
        """OAuth2認証を実行（初回はブラウザが開く）"""
        creds = None

        if os.path.exists(self.token_file):
            # パーミッションエラーを防ぐため読み取り権限を確保
            try:
                import stat
                os.chmod(self.token_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            except Exception:
                pass
            with open(self.token_file, "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                console.print("[cyan]認証トークンを更新中...[/cyan]")
                creds.refresh(Request())
            else:
                console.print("[cyan]YouTube OAuth2認証を開始します...[/cyan]")
                console.print("  ブラウザが開くので、Googleアカウントでログインしてください。")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(self.token_file, "wb") as f:
                pickle.dump(creds, f)
            try:
                import stat
                os.chmod(self.token_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            except Exception:
                pass
            console.print("[green]認証完了・トークン保存済み[/green]")

        self._youtube = build("youtube", "v3", credentials=creds)
        console.print("[green]YouTube API接続完了[/green]")

    def upload(
        self,
        clip_result: ClipResult,
        metadata: VideoMetadata,
        privacy: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
    ) -> UploadResult:
        """
        動画をYouTubeにアップロード

        Args:
            clip_result: 切り抜き動画ファイル情報
            metadata: YouTube動画メタデータ
            privacy: プライバシー設定 (public/private/unlisted)
            scheduled_at: 公開予約日時（UTC）。指定時はprivacy="private"が自動設定
        """
        if self._youtube is None:
            self.authenticate()

        if not clip_result.output_path.exists():
            return UploadResult(
                clip_result=clip_result,
                metadata=metadata,
                success=False,
                error_message=f"動画ファイルが見つかりません: {clip_result.output_path}",
            )

        # 公開予約の場合は設定を調整
        if scheduled_at:
            privacy_status = "private"
            publish_at = scheduled_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
        else:
            privacy_status = privacy or self.default_privacy
            publish_at = None

        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.format_description(self.description_footer),
                "tags": metadata.tags,
                "categoryId": metadata.category_id or self.category_id,
                "defaultLanguage": metadata.language,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        if publish_at:
            body["status"]["publishAt"] = publish_at

        console.print(f"\n[cyan]アップロード開始: {metadata.title}[/cyan]")
        console.print(f"  ファイル: {clip_result.output_path.name} ({clip_result.file_size_mb:.1f}MB)")
        if scheduled_at:
            console.print(f"  公開予約: {scheduled_at.strftime('%Y/%m/%d %H:%M')}")
        else:
            console.print(f"  プライバシー: {privacy_status}")

        media = MediaFileUpload(
            str(clip_result.output_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=CHUNK_SIZE,
        )

        request = self._youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        video_id = None
        retry = 0

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("アップロード中...", total=100)

            while video_id is None:
                try:
                    status, response = request.next_chunk()
                    if status:
                        pct = int(status.progress() * 100)
                        progress.update(task, completed=pct)
                    if response:
                        video_id = response.get("id")
                        progress.update(task, completed=100)

                except HttpError as e:
                    if e.resp.status in RETRIABLE_STATUS_CODES:
                        retry += 1
                        if retry > MAX_RETRIES:
                            return UploadResult(
                                clip_result=clip_result,
                                metadata=metadata,
                                success=False,
                                error_message=f"最大リトライ回数超過: {e}",
                            )
                        wait = 2 ** retry
                        console.print(f"[yellow]リトライ {retry}/{MAX_RETRIES} ({wait}秒後)[/yellow]")
                        time.sleep(wait)
                    else:
                        return UploadResult(
                            clip_result=clip_result,
                            metadata=metadata,
                            success=False,
                            error_message=f"HTTPエラー: {e}",
                        )
                except Exception as e:
                    return UploadResult(
                        clip_result=clip_result,
                        metadata=metadata,
                        success=False,
                        error_message=str(e),
                    )

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        console.print(f"[green]アップロード完了: {video_url}[/green]")

        # サムネイルをアップロード
        if clip_result.thumbnail_path and clip_result.thumbnail_path.exists():
            self._upload_thumbnail(video_id, clip_result.thumbnail_path)

        return UploadResult(
            clip_result=clip_result,
            metadata=metadata,
            video_id=video_id,
            video_url=video_url,
            success=True,
            scheduled_at=scheduled_at,
        )

    def upload_batch(
        self,
        clip_results: list[ClipResult],
        metadata_list: list[VideoMetadata],
        privacy: Optional[str] = None,
        scheduled_times: Optional[list[Optional[datetime]]] = None,
    ) -> list[UploadResult]:
        """複数の動画を一括アップロード"""
        if self._youtube is None:
            self.authenticate()

        results = []
        total = min(len(clip_results), len(metadata_list))

        console.print(f"\n[bold cyan]アップロード処理開始: {total}本[/bold cyan]")

        for i, (clip, meta) in enumerate(zip(clip_results, metadata_list)):
            if not clip.success:
                console.print(f"[yellow]スキップ (切り抜き失敗): {clip}[/yellow]")
                continue

            scheduled_at = None
            if scheduled_times and i < len(scheduled_times):
                scheduled_at = scheduled_times[i]

            result = self.upload(
                clip_result=clip,
                metadata=meta,
                privacy=privacy,
                scheduled_at=scheduled_at,
            )
            results.append(result)

            # API制限を考慮して少し待機
            if i < total - 1:
                time.sleep(2)

        success_count = sum(1 for r in results if r.success)
        console.print(
            f"\n[bold green]アップロード完了: {success_count}/{total}本成功[/bold green]"
        )
        for r in results:
            console.print(f"  {r}")

        return results

    def _upload_thumbnail(self, video_id: str, thumbnail_path: Path) -> None:
        """サムネイルをアップロード"""
        try:
            self._youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
            ).execute()
            console.print(f"[green]サムネイルをアップロードしました[/green]")
        except Exception as e:
            console.print(f"[yellow]サムネイルアップロード失敗 (スキップ): {e}[/yellow]")

    def save_upload_results(self, results: list[UploadResult], output_path: Path) -> None:
        """アップロード結果をJSONファイルに保存"""
        data = [
            {
                "video_id": r.video_id,
                "video_url": r.video_url,
                "title": r.metadata.title,
                "success": r.success,
                "error_message": r.error_message,
                "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
                "clip_path": str(r.clip_result.output_path),
            }
            for r in results
        ]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        console.print(f"[green]アップロード結果保存: {output_path}[/green]")
