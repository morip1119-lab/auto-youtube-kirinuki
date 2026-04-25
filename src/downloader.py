"""
YouTube動画ダウンローダーモジュール
yt-dlp を使用してYouTube動画をダウンロードし、メタデータを取得する
"""
import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yt_dlp
from rich.console import Console

console = Console()


@dataclass
class VideoInfo:
    """動画情報を保持するデータクラス"""
    video_id: str
    title: str
    description: str
    duration: int  # 秒
    channel_title: str
    channel_id: str
    upload_date: str
    view_count: int
    tags: list[str] = field(default_factory=list)
    thumbnail_url: str = ""
    local_path: Optional[Path] = None
    audio_path: Optional[Path] = None


class YouTubeDownloader:
    """YouTube動画のダウンロードとメタデータ取得を担当するクラス"""

    def __init__(self, output_dir: str = "temp", max_height: int = 1080):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_height = max_height

    def get_video_info(self, url: str) -> VideoInfo:
        """動画情報のみ取得（ダウンロードなし）"""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        return VideoInfo(
            video_id=info.get("id", ""),
            title=info.get("title", ""),
            description=info.get("description", ""),
            duration=info.get("duration", 0),
            channel_title=info.get("channel", info.get("uploader", "")),
            channel_id=info.get("channel_id", ""),
            upload_date=info.get("upload_date", ""),
            view_count=info.get("view_count", 0),
            tags=info.get("tags", []) or [],
            thumbnail_url=info.get("thumbnail", ""),
        )

    def download_video(self, url: str, video_id: Optional[str] = None) -> VideoInfo:
        """動画をダウンロードしてVideoInfoを返す"""
        console.print(f"[cyan]動画情報を取得中...[/cyan]")
        info = self.get_video_info(url)
        vid = video_id or info.video_id

        video_path = self.output_dir / f"{vid}.mp4"

        if video_path.exists():
            console.print(f"[yellow]動画は既にダウンロード済みです: {video_path}[/yellow]")
            info.local_path = video_path
            return info

        console.print(f"[cyan]動画をダウンロード中: {info.title}[/cyan]")
        console.print(f"  長さ: {info.duration // 60}分{info.duration % 60}秒")

        ydl_opts = {
            "format": f"bestvideo[height<={self.max_height}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={self.max_height}]+bestaudio/best[height<={self.max_height}]/best",
            "outtmpl": str(self.output_dir / f"{vid}.%(ext)s"),
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }
            ],
            "quiet": False,
            "no_warnings": True,
            "progress_hooks": [self._progress_hook],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # ダウンロードされたファイルを検索
        for ext in ["mp4", "mkv", "webm"]:
            candidate = self.output_dir / f"{vid}.{ext}"
            if candidate.exists():
                video_path = candidate
                break

        info.local_path = video_path
        console.print(f"[green]ダウンロード完了: {video_path}[/green]")
        return info

    def download_audio_only(self, url: str, video_id: Optional[str] = None) -> tuple[VideoInfo, Path]:
        """音声のみダウンロード（文字起こし用・高速）"""
        info = self.get_video_info(url)
        vid = video_id or info.video_id
        audio_path = self.output_dir / f"{vid}_audio.wav"

        if audio_path.exists():
            console.print(f"[yellow]音声ファイルは既に存在します: {audio_path}[/yellow]")
            info.audio_path = audio_path
            return info, audio_path

        console.print(f"[cyan]音声をダウンロード中 (文字起こし用)...[/cyan]")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(self.output_dir / f"{vid}_audio.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                    "preferredquality": "192",
                }
            ],
            "quiet": True,
            "no_warnings": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        info.audio_path = audio_path
        return info, audio_path

    def _progress_hook(self, d: dict) -> None:
        if d["status"] == "downloading":
            percent = d.get("_percent_str", "?%").strip()
            speed = d.get("_speed_str", "?").strip()
            eta = d.get("_eta_str", "?").strip()
            print(f"\r  進捗: {percent}  速度: {speed}  残り: {eta}    ", end="", flush=True)
        elif d["status"] == "finished":
            print()
            console.print("[green]  ダウンロード完了[/green]")

    def get_channel_videos(
        self,
        channel_url: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        max_videos: int = 50,
    ) -> list[VideoInfo]:
        """チャンネルの動画リストを取得（期間フィルター付き）

        Args:
            channel_url: チャンネルURL または @handle
            date_from: 開始日 YYYY-MM-DD 形式（この日以降）
            date_to: 終了日 YYYY-MM-DD 形式（この日以前）
            max_videos: 最大取得件数
        """
        # YYYY-MM-DD → YYYYMMDD 変換
        def to_ydl_date(d: str) -> str:
            return d.replace("-", "") if d else ""

        date_from_ydl = to_ydl_date(date_from or "")
        date_to_ydl = to_ydl_date(date_to or "")

        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": max_videos,
        }
        if date_from_ydl or date_to_ydl:
            try:
                date_range = yt_dlp.utils.DateRange(
                    date_from_ydl or None,
                    date_to_ydl or None,
                )
                ydl_opts["daterange"] = date_range
            except Exception:
                pass

        videos: list[VideoInfo] = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            entries = info.get("entries", []) if info else []
            for entry in entries:
                if not entry:
                    continue
                upload_date = entry.get("upload_date", "")
                # 日付フィルターを手動でも適用（daterangeが効かない場合の保険）
                if date_from_ydl and upload_date and upload_date < date_from_ydl:
                    continue
                if date_to_ydl and upload_date and upload_date > date_to_ydl:
                    continue
                videos.append(VideoInfo(
                    video_id=entry.get("id", ""),
                    title=entry.get("title", ""),
                    description=entry.get("description", ""),
                    duration=entry.get("duration", 0) or 0,
                    channel_title=info.get("channel", info.get("uploader", "")),
                    channel_id=info.get("channel_id", ""),
                    upload_date=upload_date,
                    view_count=entry.get("view_count", 0) or 0,
                    tags=[],
                    thumbnail_url=entry.get("thumbnail", f"https://img.youtube.com/vi/{entry.get('id','')}/hqdefault.jpg"),
                ))
        return videos

    def save_video_info(self, info: VideoInfo, output_path: Path) -> None:
        """VideoInfoをJSONファイルとして保存"""
        data = {
            "video_id": info.video_id,
            "title": info.title,
            "description": info.description,
            "duration": info.duration,
            "channel_title": info.channel_title,
            "channel_id": info.channel_id,
            "upload_date": info.upload_date,
            "view_count": info.view_count,
            "tags": info.tags,
            "thumbnail_url": info.thumbnail_url,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
