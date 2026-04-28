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
import platform
import urllib.request
import tempfile

from rich.console import Console
from .analyzer import ClipCandidate

console = Console()


def _get_font_path(bold: bool = True) -> str:
    """OS に応じた CJK フォントパスを返す。見つからない場合は空文字。"""
    if platform.system() == "Windows":
        name = "YuGothB.ttc" if bold else "YuGothM.ttc"
        p = Path("C:/Windows/Fonts") / name
        return str(p).replace("\\", "/").replace(":", "\\:") if p.exists() else ""
    # Linux (Ubuntu / Debian)
    candidates_bold = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    candidates_regular = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    candidates = candidates_bold if bold else candidates_regular
    for path in candidates:
        if Path(path).exists():
            return path
    # どちらでもなければ bold 側も試す
    for path in candidates_bold:
        if Path(path).exists():
            return path
    return ""


def _download_thumbnail(url: str, dest: Path) -> bool:
    """サムネイル URL を dest にダウンロードする。成功したら True。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            dest.write_bytes(resp.read())
        return dest.stat().st_size > 1000
    except Exception:
        return False


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
        thumbnail_mode: str = "auto",  # "auto" / "none" / "custom"
        custom_thumbnail_path: Optional[str] = None,
        source_thumbnail_url: str = "",   # auto 時に使う YouTube サムネイル URL
        font_size: int = 54,
        font_bold: bool = True,
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
        self.thumbnail_mode = thumbnail_mode
        self.custom_thumbnail_path = Path(custom_thumbnail_path) if custom_thumbnail_path else None
        self.source_thumbnail_url = source_thumbnail_url
        self.font_size = font_size
        self.font_bold = font_bold

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

        if is_vertical and self.thumbnail_mode != "none":
            # 縦動画: サムネイル下部貼り付け
            if self.thumbnail_mode == "custom" and self.custom_thumbnail_path and self.custom_thumbnail_path.exists():
                thumb_temp = self.custom_thumbnail_path
                cleanup_thumb = False
            else:
                # auto: YouTube サムネイル URL → 動画フレーム抽出 の順で試みる
                thumb_temp = output_path.with_name(output_path.stem + "_vthumb.jpg")
                cleanup_thumb = True
                has_thumb = False
                if self.source_thumbnail_url:
                    has_thumb = _download_thumbnail(self.source_thumbnail_url, thumb_temp)
                if not has_thumb:
                    # フォールバック: 動画フレームを抽出
                    has_thumb = self._extract_frame(
                        video_path, start + min(2.0, duration / 2), thumb_temp
                    )
                if not has_thumb:
                    thumb_temp = None
            if thumb_temp:
                try:
                    self._run_ffmpeg_vertical_with_thumb(
                        video_path, start, duration, output_path,
                        title_text, thumb_temp, fade_out_start,
                    )
                    return
                except Exception:
                    pass  # サムネイル失敗時はフォールバック
                finally:
                    if cleanup_thumb:
                        try:
                            Path(thumb_temp).unlink(missing_ok=True)
                        except Exception:
                            pass

        # ─── 通常の vf ベース処理 ─────────────────────────────────────
        vf_parts = []

        if is_vertical:
            vf_parts.append(
                "scale=1080:-2:flags=lanczos,"
                "pad=1080:1920:0:(1920-ih)/2:black,"
                "setsar=1"
            )
        else:
            vf_parts.append(
                "scale=1920:-2:flags=lanczos,"
                "pad=1920:1080:(1920-iw)/2:0:black,"
                "setsar=1"
            )

        if title_text:
            safe_text = title_text.replace("'", "\\'").replace(":", "\\:")
            fp = _get_font_path(self.font_bold)
            font_opt = f":fontfile='{fp}'" if fp else ""
            fs_v = self.font_size
            fs_h = max(24, self.font_size - 10)
            if is_vertical:
                drawtext = (
                    f"drawtext=text='{safe_text}'"
                    f":fontsize={fs_v}:fontcolor=white"
                    f"{font_opt}"
                    ":x=(w-text_w)/2:y=h/8"
                    ":box=1:boxcolor=black@0.6:boxborderw=12"
                )
            else:
                drawtext = (
                    f"drawtext=text='{safe_text}'"
                    f":fontsize={fs_h}:fontcolor=white"
                    f"{font_opt}"
                    ":x=(w-text_w)/2:y=h-text_h-40"
                    ":box=1:boxcolor=black@0.6:boxborderw=10"
                )
            vf_parts.append(drawtext)

        if self.fade_duration > 0:
            vf_parts.append(f"fade=t=in:st=0:d={self.fade_duration}")
            if fade_out_start > 0:
                vf_parts.append(f"fade=t=out:st={fade_out_start:.2f}:d={self.fade_duration}")

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
            "-c:v", self.video_codec, "-crf", str(self.video_crf),
            "-preset", "fast",
            "-c:a", self.audio_codec, "-b:a", self.audio_bitrate,
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpegエラー:\n{result.stderr[-2000:]}")

    def _run_ffmpeg_vertical_with_thumb(
        self,
        video_path: Path,
        start: float,
        duration: float,
        output_path: Path,
        title_text: str,
        thumb_path: Path,
        fade_out_start: float,
    ) -> None:
        """縦動画(9:16)を生成
        レイアウト（上から順）:
          [黒背景 + タイトル文字]  380px  y=0
          [メイン映像 16:9]        607px  y=380
          [サムネイル]             933px  y=987
        """
        TITLE_H = 380            # 上部タイトルエリアの高さ
        VIDEO_H = 607            # 1080px幅に縮小した16:9映像の高さ
        VIDEO_Y = TITLE_H        # 380
        THUMB_Y = TITLE_H + VIDEO_H   # 987
        THUMB_H = 1920 - THUMB_Y      # 933

        fc = []

        # 映像を幅1080に縮小し、1080×1920キャンバスの y=VIDEO_Y に配置（上下は黒）
        fc.append(
            f"[0:v]scale=1080:-2:flags=lanczos,"
            f"pad=1080:1920:0:{VIDEO_Y}:black,setsar=1[canvas]"
        )

        # サムネイルを下部エリア(1080×THUMB_H)に収める
        # force_original_aspect_ratio=decrease で幅1080・高さTHUMB_H に収まるよう縮小し
        # 不足する辺を黒でパディングしてセンタリング
        fc.append(
            f"[1:v]scale=1080:{THUMB_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad=1080:{THUMB_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[thumb_v]"
        )

        # サムネイルを下部に合成
        fc.append(f"[canvas][thumb_v]overlay=0:{THUMB_Y}[with_thumb]")

        cur = "with_thumb"

        # タイトル文字: 上部黒エリア(0〜TITLE_H)の縦中央に白文字
        if title_text:
            safe_text = title_text.replace("'", "\\'").replace(":", "\\:")
            title_y = f"({TITLE_H}-text_h)/2"
            fp = _get_font_path(self.font_bold)
            font_opt = f":fontfile='{fp}'" if fp else ""
            drawtext = (
                f"[{cur}]drawtext=text='{safe_text}'"
                f":fontsize={self.font_size}:fontcolor=white"
                f"{font_opt}"
                f":x=(w-text_w)/2:y={title_y}"
                "[titled]"
            )
            fc.append(drawtext)
            cur = "titled"

        # フェードイン/アウト
        if self.fade_duration > 0:
            fc.append(f"[{cur}]fade=t=in:st=0:d={self.fade_duration}[fin]")
            cur = "fin"
            if fade_out_start > 0:
                fc.append(
                    f"[{cur}]fade=t=out:st={fade_out_start:.2f}:d={self.fade_duration}[fout]"
                )
                cur = "fout"

        af_parts = []
        if self.fade_duration > 0:
            af_parts.append(f"afade=t=in:st=0:d={self.fade_duration}")
            if fade_out_start > 0:
                af_parts.append(
                    f"afade=t=out:st={fade_out_start:.2f}:d={self.fade_duration}"
                )

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-t", str(duration), "-i", str(video_path),
            "-loop", "1", "-i", str(thumb_path),
            "-filter_complex", ";".join(fc),
            "-map", f"[{cur}]",
            "-map", "0:a",
        ]
        if af_parts:
            cmd += ["-af", ",".join(af_parts)]
        cmd += [
            "-t", str(duration),
            "-c:v", self.video_codec, "-crf", str(self.video_crf),
            "-preset", "fast",
            "-c:a", self.audio_codec, "-b:a", self.audio_bitrate,
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpegエラー(縦+サムネイル):\n{result.stderr[-2000:]}")

    def _extract_frame(self, video_path: Path, seek: float, output_path: Path) -> bool:
        """ソース動画から指定時刻の1フレームをJPEGとして抽出する。成功したら True を返す"""
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(max(0, seek)),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(output_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace",
        )
        return result.returncode == 0 and output_path.exists()

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
