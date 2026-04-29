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


def _cookie_file_opts() -> dict:
    """YouTube がボット検出する環境では Netscape 形式の cookies ファイルが必要なことがあります。

    環境変数 YOUTUBE_COOKIES_FILE または YT_DLP_COOKIES_FILE にパスを指定。
    未指定の場合は既定パスを自動検索する。
    """
    # 検索順: 環境変数 → VPS 固定パス → カレントディレクトリ
    candidates = [
        os.environ.get("YOUTUBE_COOKIES_FILE"),
        os.environ.get("YT_DLP_COOKIES_FILE"),
        "/opt/kirinuki/youtube_cookies.txt",
        "youtube_cookies.txt",
        "cookies.txt",
    ]
    for raw in candidates:
        if not raw:
            continue
        p = Path(raw).expanduser()
        if p.is_file() and p.stat().st_size > 0:
            console.print(f"[green]Cookie ファイルを使用: {p}[/green]")
            return {"cookiefile": str(p.resolve())}
    console.print("[yellow]Cookie ファイルが見つかりません（ボット検出が起きやすくなります）[/yellow]")
    return {}


def _base_opts() -> dict:
    """ボット検出を回避するための基本オプション。
    android / web の順で試行し、Cookie ファイルがあれば追加する。
    tv_embedded は利用可能フォーマットが極めて少ないため除外。
    """
    opts: dict = {
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
        "http_headers": {
            "User-Agent": (
                "com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip"
            ),
        },
    }
    opts.update(_cookie_file_opts())
    return opts


def _with_cookies(opts: dict) -> dict:
    merged = dict(opts)
    merged.update(_base_opts())
    return merged


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
        ydl_opts = _with_cookies({
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "format": "bestvideo+bestaudio/best",
            "check_formats": False,
            "ignore_no_formats_error": True,
        })
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

        # Android クライアントは分離ストリーム非対応のため、結合済みストリームを優先
        fmt = (
            f"best[height<={self.max_height}][ext=mp4]"
            f"/best[height<={self.max_height}]"
            f"/best"
        )
        ydl_opts = _with_cookies({
            "format": fmt,
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
        })

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

        ydl_opts = _with_cookies({
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
        })

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
        sort_order: str = "newest",
    ) -> list[VideoInfo]:
        """チャンネルの動画リストを取得（期間フィルター付き）

        Args:
            channel_url: チャンネルURL または @handle
            date_from: 開始日 YYYY-MM-DD 形式（この日以降）
            date_to: 終了日 YYYY-MM-DD 形式（この日以前）
            max_videos: フィルター後の最大件数
            sort_order: "newest" | "oldest" | "views"
        """
        channel_url = channel_url.strip()
        if channel_url.startswith("@"):
            channel_url = f"https://www.youtube.com/{channel_url}"

        base_url = channel_url.rstrip("/")
        # /videos タブを指定（yt-dlp が認識できる形式）
        if (
            "youtube.com/@" in base_url or
            "youtube.com/channel/" in base_url or
            "youtube.com/c/" in base_url or
            "youtube.com/user/" in base_url
        ):
            # 既存のタブ指定・クエリを除去してから /videos を付ける
            for tab in ["/videos", "/shorts", "/streams", "/playlists"]:
                if tab in base_url:
                    base_url = base_url[:base_url.index(tab)]
                    break
            base_url = base_url.rstrip("/") + "/videos"
        # 並び順は取得後にPythonで処理するため URL パラメータは使わない

        def to_ydl_date(d: str) -> str:
            return d.replace("-", "") if d else ""

        date_from_ydl = to_ydl_date(date_from or "")
        date_to_ydl   = to_ydl_date(date_to   or "")
        has_date_filter = bool(date_from_ydl or date_to_ydl)

        # ─────────────────────────────────────────────────────────────────
        # 重要: extract_flat では upload_date が取れないケースが多い。
        # そのため extract_flat を使わず個別メタデータを取得する。
        # ただし全件フェッチは遅いので、日付フィルターなしの場合のみ
        # 高速な flat モードを使い、フィルターあり時は daterange に頼る。
        # ─────────────────────────────────────────────────────────────────
        # extract_flat では upload_date が含まれないため daterange は使わず手動フィルターを行う。
        # 日付フィルターあり時は多めにフェッチしてから絞り込む。
        fetch_limit = min(max(200, max_videos * 10), 800) if has_date_filter else max_videos
        ydl_opts: dict = _with_cookies({
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": fetch_limit,
            "ignoreerrors": True,
            "socket_timeout": 30,
        })

        videos: list[VideoInfo] = []
        channel_title_fallback = ""
        channel_id_fallback    = ""
        extra_meta_fetches = 0
        extra_meta_limit = max(80, max_videos * 4)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(base_url, download=False)
            if not info:
                console.print(f"[red]チャンネル情報の取得に失敗: info=None url={base_url}[/red]")
                return []
            channel_title_fallback = info.get("channel", info.get("uploader", ""))
            channel_id_fallback    = info.get("channel_id", "")
            entries = [e for e in (info.get("entries") or []) if e]
            console.print(f"[cyan]取得エントリ数: {len(entries)} (日付フィルター: {date_from_ydl}〜{date_to_ydl})[/cyan]")

            for entry in entries:
                video_id    = entry.get("id", "")
                upload_date = (entry.get("upload_date") or "").strip()

                # extract_flat で upload_date が取れなかった場合: 個別取得（件数上限あり）
                if has_date_filter and not upload_date and video_id and extra_meta_fetches < extra_meta_limit:
                    try:
                        extra_meta_fetches += 1
                        single_opts = _with_cookies({
                            "quiet": True,
                            "no_warnings": True,
                            "socket_timeout": 20,
                        })
                        with yt_dlp.YoutubeDL(single_opts) as ydl2:
                            full = ydl2.extract_info(
                                f"https://www.youtube.com/watch?v={video_id}",
                                download=False,
                            )
                        if full:
                            upload_date = (full.get("upload_date") or "").strip()
                            entry.update(full)
                    except Exception:
                        pass

                # 手動日付フィルター（個別取得で日付が取れた場合）
                if has_date_filter and upload_date:
                    if date_from_ydl and upload_date < date_from_ydl:
                        continue
                    if date_to_ydl and upload_date > date_to_ydl:
                        continue
                elif has_date_filter and not upload_date:
                    # プレイリストは yt-dlp の daterange で既に期間内に絞られている。
                    # flat 抽出では upload_date が空のことが多いが、そのまま除外すると0件になりがちなので採用する。
                    pass

                thumb = (
                    entry.get("thumbnail")
                    or f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                )
                videos.append(VideoInfo(
                    video_id=video_id,
                    title=entry.get("title", ""),
                    description=entry.get("description", ""),
                    duration=entry.get("duration", 0) or 0,
                    channel_title=entry.get("channel", channel_title_fallback),
                    channel_id=entry.get("channel_id", channel_id_fallback),
                    upload_date=upload_date,
                    view_count=entry.get("view_count", 0) or 0,
                    tags=[],
                    thumbnail_url=thumb,
                ))

                if len(videos) >= max_videos:
                    break

        console.print(f"[cyan]日付フィルター後: {len(videos)}件[/cyan]")

        # 並び順をPythonで処理
        if sort_order == "oldest":
            videos.sort(key=lambda v: v.upload_date or "")
        elif sort_order == "views":
            videos.sort(key=lambda v: v.view_count, reverse=True)
        else:  # newest
            videos.sort(key=lambda v: v.upload_date or "", reverse=True)

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
