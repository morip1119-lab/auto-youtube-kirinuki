"""
動画切り抜きモジュール
ffmpeg を使用して指定区間の動画を切り出す
縦動画(9:16)リフレーム・タイトルオーバーレイ対応
"""
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import subprocess
import re

from rich.console import Console
from .analyzer import ClipCandidate

console = Console()


@dataclass
class ClipResult:
    """切り抜き結果を表すデータクラス"""
    candidate: ClipCandidate
    output_path: Path
    success: bool
    error_message: str = ""
    file_size_mb: float = 0.0
    thumbnail_path: Optional[Path] = None

    def __str__(self) -> str:
        status = "✓" if self.success else "✗"
        return f"{status} {self.output_path.name} ({self.file_size_mb:.1f}MB)"


class VideoCutter:
    """ffmpegを使用した動画切り抜きクラス"""

    def __init__(
        self,
        output_dir: str = "output",
        video_crf: int = 18,
        video_codec: str = "libx264",
        audio_codec: str = "aac",
        audio_bitrate: str = "192k",
        fade_duration: float = 0.5,
        output_format: str = "mp4",
        auto_thumbnail: bool = True,
        thumbnail_offset: int = 3,
        aspect_ratio: str = "16:9",   # "16:9" or "9:16"
        show_title: bool = True,       # タイトルオーバーレイ表示
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.video_crf = video_crf
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.audio_bitrate = audio_bitrate
        self.fade_duration = fade_duration
        self.output_format = output_format
        self.auto_thumbnail = auto_thumbnail
        self.thumbnail_offset = thumbnail_offset
        self.aspect_ratio = aspect_ratio
        self.show_title = show_title

    def cut_clip(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_filename: Optional[str] = None,
        index: int = 0,
        title_text: str = "",
    ) -> ClipResult:
        """単一の切り抜きを実行"""
        if output_filename is None:
            safe_title = _sanitize_filename(candidate.suggested_title)
            output_filename = f"clip_{index + 1:02d}_{safe_title}.{self.output_format}"

        output_path = self.output_dir / output_filename

        if output_path.exists():
            console.print(f"[yellow]既に存在: {output_path.name}[/yellow]")
            size_mb = output_path.stat().st_size / (1024 * 1024)
            result = ClipResult(
                candidate=candidate,
                output_path=output_path,
                success=True,
                file_size_mb=size_mb,
            )
            if self.auto_thumbnail:
                result.thumbnail_path = self._extract_thumbnail(output_path, candidate)
            return result

        display_title = title_text or candidate.suggested_title
        console.print(
            f"[cyan]切り抜き中: {candidate.start_str}〜{candidate.end_str} "
            f"「{display_title}」[/cyan]"
        )

        try:
            self._run_ffmpeg_cut(
                video_path=video_path,
                start=candidate.start,
                duration=candidate.end - candidate.start,
                output_path=output_path,
                title_text=display_title if self.show_title else "",
            )

            if not output_path.exists():
                raise FileNotFoundError(f"出力ファイルが生成されませんでした: {output_path}")

            size_mb = output_path.stat().st_size / (1024 * 1024)
            result = ClipResult(
                candidate=candidate,
                output_path=output_path,
                success=True,
                file_size_mb=size_mb,
            )

            if self.auto_thumbnail:
                result.thumbnail_path = self._extract_thumbnail(output_path, candidate)

            console.print(f"[green]完了: {output_path.name} ({size_mb:.1f}MB)[/green]")
            return result

        except Exception as e:
            console.print(f"[red]切り抜きエラー: {e}[/red]")
            return ClipResult(
                candidate=candidate,
                output_path=output_path,
                success=False,
                error_message=str(e),
            )

    def cut_all_clips(
        self,
        video_path: Path,
        candidates: list[ClipCandidate],
        video_id: str = "",
    ) -> list[ClipResult]:
        """全候補の切り抜きを実行"""
        results = []
        prefix = f"{video_id}_" if video_id else ""

        console.print(f"\n[bold cyan]切り抜き処理開始: {len(candidates)}本[/bold cyan]")

        for i, candidate in enumerate(candidates):
            safe_title = _sanitize_filename(candidate.suggested_title)
            output_filename = f"{prefix}clip_{i + 1:02d}_{safe_title}.{self.output_format}"

            result = self.cut_clip(
                video_path=video_path,
                candidate=candidate,
                output_filename=output_filename,
                index=i,
            )
            results.append(result)

        success_count = sum(1 for r in results if r.success)
        console.print(
            f"\n[bold green]切り抜き完了: {success_count}/{len(candidates)}本成功[/bold green]"
        )
        return results

    def _run_ffmpeg_cut(
        self,
        video_path: Path,
        start: float,
        duration: float,
        output_path: Path,
        title_text: str = "",
    ) -> None:
        """ffmpegで動画を切り抜く"""
        fade_out_start = max(0, duration - self.fade_duration)
        is_vertical = self.aspect_ratio == "9:16"

        # ビデオフィルター構築
        vf_parts = []

        if is_vertical:
            # 横動画を縦(9:16)にリフレーム
            # step1: 幅1080に縮小（高さは自動・偶数保証）
            # step2: 1080x1920にパディング（上下に黒帯）
            # step3: setsar=1でアスペクト比メタデータを確定
            vf_parts.append(
                "scale=1080:-2:flags=lanczos,"
                "pad=1080:1920:0:(1920-ih)/2:black,"
                "setsar=1"
            )
        else:
            # 横動画: 1920x1080に揃える
            vf_parts.append(
                "scale=1920:-2:flags=lanczos,"
                "pad=1920:1080:(1920-iw)/2:0:black,"
                "setsar=1"
            )

        # タイトルオーバーレイ (drawtext)
        if title_text:
            safe_text = title_text.replace("'", "\\'").replace(":", "\\:")
            if is_vertical:
                # 縦動画: 上部から1/8の位置（約240px）に表示
                drawtext = (
                    f"drawtext=text='{safe_text}'"
                    ":fontsize=52"
                    ":fontcolor=white"
                    ":fontfile='C\\:/Windows/Fonts/YuGothB.ttc'"
                    ":x=(w-text_w)/2"
                    ":y=h/8"
                    ":box=1:boxcolor=black@0.6:boxborderw=12"
                )
            else:
                # 横動画: 下部中央に表示
                drawtext = (
                    f"drawtext=text='{safe_text}'"
                    ":fontsize=44"
                    ":fontcolor=white"
                    ":fontfile='C\\:/Windows/Fonts/YuGothB.ttc'"
                    ":x=(w-text_w)/2"
                    ":y=h-text_h-40"
                    ":box=1:boxcolor=black@0.6:boxborderw=10"
                )
            vf_parts.append(drawtext)

        # フェードイン/アウト
        if self.fade_duration > 0:
            vf_parts.append(f"fade=t=in:st=0:d={self.fade_duration}")
            if fade_out_start > 0:
                vf_parts.append(f"fade=t=out:st={fade_out_start:.2f}:d={self.fade_duration}")

        # 音声フィルター
        af_parts = []
        if self.fade_duration > 0:
            af_parts.append(f"afade=t=in:st=0:d={self.fade_duration}")
            if fade_out_start > 0:
                af_parts.append(f"afade=t=out:st={fade_out_start:.2f}:d={self.fade_duration}")

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(duration),
        ]

        if vf_parts:
            cmd += ["-vf", ",".join(vf_parts)]
        if af_parts:
            cmd += ["-af", ",".join(af_parts)]

        cmd += [
            "-c:v", self.video_codec,
            "-crf", str(self.video_crf),
            "-preset", "fast",
            "-c:a", self.audio_codec,
            "-b:a", self.audio_bitrate,
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode != 0:
            raise RuntimeError(f"ffmpegエラー:\n{result.stderr[-2000:]}")

    def _extract_thumbnail(
        self, video_path: Path, candidate: ClipCandidate
    ) -> Optional[Path]:
        """動画からサムネイル画像を抽出"""
        thumbnail_path = video_path.with_suffix(".jpg")
        offset = min(self.thumbnail_offset, candidate.duration / 2)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(offset),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(thumbnail_path),
        ]

        result = subprocess.run(
            cmd, capture_output=True,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0 and thumbnail_path.exists():
            return thumbnail_path
        return None


def _sanitize_filename(name: str, max_length: int = 50) -> str:
    """ファイル名に使えない文字を除去・変換"""
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r"\s+", "_", name.strip())
    if len(name) > max_length:
        name = name[:max_length]
    return name or "clip"
