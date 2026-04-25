"""
YouTube切り抜き動画自動生成・アップロードツール
メインエントリーポイント
"""
import os
import sys
import json

# Windows環境でのUTF-8出力設定
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

import click
import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from dateutil import parser as dateutil_parser

# 環境変数を読み込み
load_dotenv()

console = Console(highlight=False)

JST = timezone(timedelta(hours=9))


def load_config(config_path: str = "config.yaml") -> dict:
    """設定ファイルを読み込む"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def print_banner():
    """起動バナーを表示"""
    console.print(Panel.fit(
        "[bold cyan]YouTube 切り抜き動画 自動生成ツール[/bold cyan]\n"
        "[dim]自動ダウンロード → 文字起こし → AI分析 → 切り抜き → アップロード[/dim]",
        border_style="cyan",
    ))


def resolve_schedule(
    schedule_str: Optional[str],
    clips_count: int,
    interval_hours: int = 24,
) -> list[Optional[datetime]]:
    """
    公開予約日時リストを生成

    Args:
        schedule_str: 最初の公開日時文字列 (例: "2026-05-01 10:00" または "2026-05-01T10:00:00+09:00")
        clips_count: 切り抜き本数
        interval_hours: 動画間の公開間隔（時間）
    """
    if not schedule_str:
        return [None] * clips_count

    try:
        first_dt = dateutil_parser.parse(schedule_str)
        if first_dt.tzinfo is None:
            first_dt = first_dt.replace(tzinfo=JST)
        first_dt_utc = first_dt.astimezone(timezone.utc)

        return [
            first_dt_utc + timedelta(hours=i * interval_hours)
            for i in range(clips_count)
        ]
    except Exception as e:
        console.print(f"[red]公開予約日時のパースエラー: {e}[/red]")
        return [None] * clips_count


@click.group()
def cli():
    """YouTube切り抜き動画自動生成・アップロードツール"""
    pass


@cli.command()
@click.argument("url")
@click.option("--clips", "-n", default=3, show_default=True, help="切り抜き本数")
@click.option("--min-duration", default=60, show_default=True, help="切り抜き最短時間（秒）")
@click.option("--max-duration", default=600, show_default=True, help="切り抜き最長時間（秒）")
@click.option("--privacy", default="private", type=click.Choice(["public", "private", "unlisted"]), show_default=True, help="プライバシー設定")
@click.option("--schedule", default=None, help="最初の公開予約日時 (例: '2026-05-01 10:00')")
@click.option("--schedule-interval", default=24, show_default=True, help="公開間隔（時間）")
@click.option("--no-upload", is_flag=True, default=False, help="アップロードをスキップ（切り抜きのみ）")
@click.option("--output-dir", default=None, help="出力ディレクトリ（デフォルト: config.yamlの設定）")
@click.option("--config", default="config.yaml", show_default=True, help="設定ファイルパス")
@click.option("--keep-original", is_flag=True, default=False, help="元動画をダウンロード後も保持")
@click.option("--whisper-model", default=None, help="Whisperモデルサイズ (tiny/base/small/medium/large-v3)")
@click.option("--device", default=None, type=click.Choice(["cpu", "cuda"]), help="処理デバイス")
def run(
    url: str,
    clips: int,
    min_duration: int,
    max_duration: int,
    privacy: str,
    schedule: Optional[str],
    schedule_interval: int,
    no_upload: bool,
    output_dir: Optional[str],
    config: str,
    keep_original: bool,
    whisper_model: Optional[str],
    device: Optional[str],
):
    """
    YouTube動画URLから切り抜き動画を自動生成・アップロード

    \b
    例:
      python main.py run https://www.youtube.com/watch?v=XXXXXXXXXXX
      python main.py run https://youtu.be/XXXXXXXXXXX --clips 5 --privacy public
      python main.py run https://youtu.be/XXXXXXXXXXX --schedule "2026-05-01 10:00" --schedule-interval 12
      python main.py run https://youtu.be/XXXXXXXXXXX --no-upload
    """
    print_banner()

    # 設定ファイルを読み込み
    cfg = load_config(config)

    # 環境変数から設定取得
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        console.print("[red]エラー: OPENAI_API_KEY が設定されていません。.envファイルを確認してください。[/red]")
        sys.exit(1)

    openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    whisper_model_name = whisper_model or os.environ.get("WHISPER_MODEL", cfg["transcription"].get("model", "large-v3"))
    whisper_device = device or os.environ.get("WHISPER_DEVICE", "cpu")
    max_height = int(os.environ.get("MAX_VIDEO_HEIGHT", 1080))
    effective_output_dir = output_dir or os.environ.get("OUTPUT_DIR", "output")
    temp_dir = cfg["download"].get("temp_dir", "temp")
    client_secrets = os.environ.get("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")

    # 各モジュールを初期化
    from src.downloader import YouTubeDownloader
    from src.transcriber import Transcriber
    from src.analyzer import VideoAnalyzer
    from src.clipper import VideoCutter
    from src.metadata_generator import MetadataGenerator
    from src.uploader import YouTubeUploader

    clip_cfg = cfg.get("clip", {})
    analysis_cfg = cfg.get("analysis", {})
    upload_cfg = cfg.get("upload", {})
    transcription_cfg = cfg.get("transcription", {})

    downloader = YouTubeDownloader(output_dir=temp_dir, max_height=max_height)
    transcriber = Transcriber(
        model_size=whisper_model_name,
        device=whisper_device,
        language=transcription_cfg.get("language", "ja"),
        vad_filter=transcription_cfg.get("vad_filter", True),
    )
    analyzer = VideoAnalyzer(
        openai_api_key=openai_api_key,
        model=openai_model,
        clips_per_video=clips,
        min_clip_duration=min_duration,
        max_clip_duration=max_duration,
        min_score=analysis_cfg.get("min_score_threshold", 0.6),
    )
    cutter = VideoCutter(
        output_dir=effective_output_dir,
        video_crf=clip_cfg.get("video_crf", 18),
        video_codec=clip_cfg.get("video_codec", "libx264"),
        audio_codec=clip_cfg.get("audio_codec", "aac"),
        audio_bitrate=clip_cfg.get("audio_bitrate", "192k"),
        fade_duration=clip_cfg.get("fade_duration", 0.5),
        output_format=clip_cfg.get("output_format", "mp4"),
        auto_thumbnail=upload_cfg.get("auto_thumbnail", True),
        thumbnail_offset=upload_cfg.get("thumbnail_offset_seconds", 3),
    )
    meta_gen = MetadataGenerator(
        openai_api_key=openai_api_key,
        model=openai_model,
        max_tags=upload_cfg.get("max_tags", 15),
        description_footer=upload_cfg.get("description_footer", ""),
    )

    # ============================
    # STEP 1: 動画情報取得 & ダウンロード
    # ============================
    console.print("\n[bold]STEP 1/5: 動画をダウンロード[/bold]")
    video_info = downloader.download_video(url)
    video_path = video_info.local_path

    if not video_path or not video_path.exists():
        console.print("[red]動画のダウンロードに失敗しました[/red]")
        sys.exit(1)

    # ============================
    # STEP 2: 文字起こし
    # ============================
    console.print("\n[bold]STEP 2/5: 文字起こし (Whisper)[/bold]")
    transcript_path = Path(temp_dir) / f"{video_info.video_id}_transcript.json"

    if transcript_path.exists():
        console.print(f"[yellow]文字起こし済みファイルを使用: {transcript_path}[/yellow]")
        transcript = Transcriber.load_transcript(transcript_path)
    else:
        transcript = transcriber.transcribe_video(video_path, video_id=video_info.video_id)
        transcriber.save_transcript(transcript, transcript_path)

    # ============================
    # STEP 3: AI分析・切り抜き箇所特定
    # ============================
    console.print("\n[bold]STEP 3/5: AI分析・切り抜き箇所の特定[/bold]")
    candidates_path = Path(temp_dir) / f"{video_info.video_id}_candidates.json"

    if candidates_path.exists():
        console.print(f"[yellow]分析済みファイルを使用: {candidates_path}[/yellow]")
        from src.analyzer import VideoAnalyzer
        candidates = VideoAnalyzer.load_candidates(candidates_path)
    else:
        candidates = analyzer.analyze(
            transcript=transcript,
            video_title=video_info.title,
            video_description=video_info.description,
        )
        if not candidates:
            console.print("[red]切り抜き候補が見つかりませんでした[/red]")
            sys.exit(1)
        analyzer.save_candidates(candidates, candidates_path)

    _print_candidates_table(candidates)

    # ============================
    # STEP 4: 動画切り抜き
    # ============================
    console.print("\n[bold]STEP 4/5: 動画を切り抜き[/bold]")
    clip_results = cutter.cut_all_clips(
        video_path=video_path,
        candidates=candidates,
        video_id=video_info.video_id,
    )

    # ============================
    # STEP 5: メタデータ生成 & アップロード
    # ============================
    if no_upload:
        console.print("\n[yellow]--no-upload が指定されたため、アップロードをスキップします[/yellow]")
        _print_summary(clip_results, [])
        _cleanup_temp(video_info.video_id, temp_dir, keep_original, video_path)
        return

    console.print("\n[bold]STEP 5/5: メタデータ生成 & YouTubeアップロード[/bold]")

    # メタデータ生成
    metadata_list = meta_gen.generate_batch(
        candidates=candidates,
        transcript=transcript,
        source_video_title=video_info.title,
        source_channel_name=video_info.channel_title,
        source_video_url=url,
    )

    # 公開予約スケジュール生成
    scheduled_times = resolve_schedule(schedule, len(clip_results), schedule_interval)

    # アップロード
    uploader = YouTubeUploader(
        client_secrets_file=client_secrets,
        default_privacy=privacy,
        category_id=upload_cfg.get("category_id", "22"),
        description_footer=upload_cfg.get("description_footer", ""),
    )

    upload_results = uploader.upload_batch(
        clip_results=[r for r in clip_results if r.success],
        metadata_list=metadata_list,
        privacy=privacy,
        scheduled_times=scheduled_times if any(t for t in scheduled_times) else None,
    )

    # 結果を保存
    results_path = Path(effective_output_dir) / f"{video_info.video_id}_upload_results.json"
    uploader.save_upload_results(upload_results, results_path)

    _print_summary(clip_results, upload_results)
    _cleanup_temp(video_info.video_id, temp_dir, keep_original, video_path)


@cli.command()
@click.argument("url")
@click.option("--config", default="config.yaml", show_default=True, help="設定ファイルパス")
def info(url: str, config: str):
    """YouTube動画の情報だけ表示（ダウンロードなし）"""
    print_banner()
    from src.downloader import YouTubeDownloader
    downloader = YouTubeDownloader()
    video_info = downloader.get_video_info(url)

    table = Table(title="動画情報", show_header=False, border_style="cyan")
    table.add_column("項目", style="bold cyan", width=20)
    table.add_column("内容")
    table.add_row("タイトル", video_info.title)
    table.add_row("チャンネル", video_info.channel_title)
    table.add_row("動画ID", video_info.video_id)
    table.add_row("長さ", f"{video_info.duration // 60}分{video_info.duration % 60}秒")
    table.add_row("投稿日", video_info.upload_date)
    table.add_row("再生回数", f"{video_info.view_count:,}")
    table.add_row("説明文", video_info.description[:200] + "..." if len(video_info.description) > 200 else video_info.description)
    console.print(table)


@cli.command()
@click.argument("url")
@click.option("--whisper-model", default=None, help="Whisperモデルサイズ")
@click.option("--device", default="cpu", type=click.Choice(["cpu", "cuda"]), show_default=True)
@click.option("--output", default=None, help="出力JSONファイルパス")
@click.option("--config", default="config.yaml", show_default=True)
def transcribe(url: str, whisper_model: Optional[str], device: str, output: Optional[str], config: str):
    """YouTube動画の文字起こしのみ実行"""
    print_banner()
    load_dotenv()
    cfg = load_config(config)

    from src.downloader import YouTubeDownloader
    from src.transcriber import Transcriber

    temp_dir = cfg["download"].get("temp_dir", "temp")
    model_name = whisper_model or os.environ.get("WHISPER_MODEL", "large-v3")

    downloader = YouTubeDownloader(output_dir=temp_dir)
    video_info, audio_path = downloader.download_audio_only(url)

    transcriber = Transcriber(
        model_size=model_name,
        device=device,
        language=cfg["transcription"].get("language", "ja"),
    )
    transcript = transcriber.transcribe(audio_path, video_id=video_info.video_id)

    output_path = Path(output) if output else Path(temp_dir) / f"{video_info.video_id}_transcript.json"
    transcriber.save_transcript(transcript, output_path)
    console.print(f"\n[green]文字起こし完了: {output_path}[/green]")
    console.print("\n--- 冒頭200文字 ---")
    console.print(transcript.full_text[:200] + "...")


def _print_candidates_table(candidates) -> None:
    """切り抜き候補をテーブル表示"""
    table = Table(title="切り抜き候補", border_style="green")
    table.add_column("#", style="bold", width=4)
    table.add_column("開始〜終了", style="cyan", width=15)
    table.add_column("長さ", width=8)
    table.add_column("スコア", width=8)
    table.add_column("タイトル案", style="yellow")
    table.add_column("理由", style="dim")

    for i, c in enumerate(candidates, 1):
        table.add_row(
            str(i),
            f"{c.start_str}〜{c.end_str}",
            f"{int(c.duration // 60)}分{int(c.duration % 60)}秒",
            f"{c.score:.2f}",
            c.suggested_title,
            c.reason[:50] + "..." if len(c.reason) > 50 else c.reason,
        )

    console.print(table)


def _print_summary(clip_results, upload_results) -> None:
    """最終サマリーを表示"""
    console.print("\n")
    console.print(Panel.fit(
        "[bold green]処理完了！[/bold green]",
        border_style="green",
    ))

    table = Table(title="処理結果サマリー", border_style="green")
    table.add_column("ファイル名", style="cyan")
    table.add_column("サイズ", width=10)
    table.add_column("アップロード", width=15)
    table.add_column("URL / 状態")

    upload_map = {str(r.clip_result.output_path): r for r in upload_results}

    for clip in clip_results:
        status = "✓" if clip.success else "✗"
        size = f"{clip.file_size_mb:.1f}MB" if clip.success else "-"
        up_result = upload_map.get(str(clip.output_path))
        if up_result:
            upload_status = "✓ アップロード済" if up_result.success else "✗ 失敗"
            url = up_result.video_url if up_result.success else up_result.error_message[:50]
        else:
            upload_status = "スキップ"
            url = "-"
        table.add_row(f"{status} {clip.output_path.name}", size, upload_status, url)

    console.print(table)


def _cleanup_temp(video_id: str, temp_dir: str, keep_original: bool, video_path: Optional[Path]) -> None:
    """一時ファイルのクリーンアップ"""
    if not keep_original and video_path and video_path.exists():
        try:
            video_path.unlink()
            console.print(f"[dim]一時ファイルを削除: {video_path.name}[/dim]")
        except Exception:
            pass

    # 音声ファイルも削除
    if video_path:
        audio_wav = video_path.with_name(f"{video_id}_audio.wav")
        if audio_wav.exists():
            try:
                audio_wav.unlink()
            except Exception:
                pass
        # 動画から抽出した音声も削除
        audio_extracted = video_path.with_suffix(".wav")
        if audio_extracted.exists():
            try:
                audio_extracted.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    cli()
