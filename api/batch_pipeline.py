"""
チャンネル一括処理パイプライン
複数の動画を順番に処理し、バッチジョブの進捗を管理する
"""
import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from dateutil import parser as dateutil_parser

from .job_manager import job_manager, BatchJob, BatchVideoItem, ClipOutput, JobStatus

load_dotenv()

JST = timezone(timedelta(hours=9))


async def run_batch_pipeline(batch_job: BatchJob) -> None:
    """バッチパイプラインを非同期で実行"""
    await job_manager.update_batch(
        batch_job,
        status=JobStatus.RUNNING,
        message=f"一括処理を開始します（{len(batch_job.videos)}本の動画）",
    )

    settings = batch_job.settings
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    whisper_model = settings.get("whisper_model", os.environ.get("WHISPER_MODEL", "small"))
    whisper_device = settings.get("device", os.environ.get("WHISPER_DEVICE", "cpu"))
    max_height = int(os.environ.get("MAX_VIDEO_HEIGHT", 1080))
    temp_dir = os.environ.get("TEMP_DIR", "temp")
    output_dir = os.environ.get("OUTPUT_DIR", "output")
    client_secrets = os.environ.get("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")

    clips_count = settings.get("clips_count", 3)
    min_duration = settings.get("min_duration", 60)
    max_duration = settings.get("max_duration", 300)
    output_format = settings.get("output_format", "horizontal")
    show_title = settings.get("show_title", True)
    privacy = settings.get("privacy", "private")
    do_upload = settings.get("do_upload", False)
    schedule_date = settings.get("schedule_date", None)
    posts_per_day = int(settings.get("posts_per_day", 1))

    total = len(batch_job.videos)
    # 固定時刻スロットでスケジュールを生成
    scheduled_times: Optional[list] = _resolve_schedule_fixed(
        schedule_date,
        total * clips_count,
        posts_per_day,
    )
    schedule_cursor = 0

    try:
        from src.downloader import YouTubeDownloader
        from src.transcriber import Transcriber
        from src.analyzer import VideoAnalyzer
        from src.clipper import VideoCutter
        from src.metadata_generator import MetadataGenerator

        loop = asyncio.get_event_loop()

        for vi, video_item in enumerate(batch_job.videos):
            batch_job.current_video_index = vi
            video_item.status = "running"
            base_progress = int(vi / total * 100)

            await job_manager.update_batch(
                batch_job,
                progress=base_progress,
                message=f"[{vi+1}/{total}] {video_item.title or video_item.url} の処理を開始",
            )

            try:
                # ── STEP 1: ダウンロード ──────────────────────────────
                await job_manager.update_batch(
                    batch_job,
                    message=f"[{vi+1}/{total}] ダウンロード中...",
                )
                downloader = YouTubeDownloader(output_dir=temp_dir, max_height=max_height)
                video_info = await loop.run_in_executor(
                    None, lambda u=video_item.url: downloader.download_video(u)
                )
                video_item.video_id = video_info.video_id
                video_item.title = video_info.title
                video_item.thumbnail = video_info.thumbnail_url

                await job_manager.update_batch(
                    batch_job,
                    progress=base_progress + int(1 / total * 10),
                    message=f"[{vi+1}/{total}] ダウンロード完了: {video_info.title}",
                )

                # ── STEP 2: 文字起こし ────────────────────────────────
                await job_manager.update_batch(
                    batch_job,
                    message=f"[{vi+1}/{total}] Whisperで文字起こし中...",
                )
                transcript_path = Path(temp_dir) / f"{video_info.video_id}_transcript.json"
                if transcript_path.exists():
                    transcript = Transcriber.load_transcript(transcript_path)
                else:
                    transcriber = Transcriber(
                        model_size=whisper_model,
                        device=whisper_device,
                        language="ja",
                        vad_filter=True,
                    )
                    transcript = await loop.run_in_executor(
                        None, lambda: transcriber.transcribe_video(
                            video_info.local_path, video_id=video_info.video_id
                        )
                    )
                    transcriber.save_transcript(transcript, transcript_path)

                await job_manager.update_batch(
                    batch_job,
                    progress=base_progress + int(1 / total * 40),
                    message=f"[{vi+1}/{total}] 文字起こし完了",
                )

                # ── STEP 3: AI分析 ────────────────────────────────────
                await job_manager.update_batch(
                    batch_job,
                    message=f"[{vi+1}/{total}] GPTで切り抜き箇所を分析中...",
                )
                candidates_path = Path(temp_dir) / f"{video_info.video_id}_candidates.json"
                if candidates_path.exists():
                    candidates = VideoAnalyzer.load_candidates(candidates_path)
                else:
                    analyzer = VideoAnalyzer(
                        openai_api_key=openai_api_key,
                        model=openai_model,
                        clips_per_video=clips_count,
                        min_clip_duration=min_duration,
                        max_clip_duration=max_duration,
                    )
                    candidates = await loop.run_in_executor(
                        None, lambda: analyzer.analyze(
                            transcript=transcript,
                            video_title=video_info.title,
                            video_description=video_info.description,
                        )
                    )
                    if candidates:
                        analyzer.save_candidates(candidates, candidates_path)

                if not candidates:
                    raise ValueError("切り抜き候補が見つかりませんでした")

                await job_manager.update_batch(
                    batch_job,
                    progress=base_progress + int(1 / total * 55),
                    message=f"[{vi+1}/{total}] {len(candidates)}個の候補を特定",
                )

                # ── STEP 4: 切り抜き ──────────────────────────────────
                await job_manager.update_batch(
                    batch_job,
                    message=f"[{vi+1}/{total}] 動画を切り抜き中...",
                )
                aspect_ratio = "9:16" if output_format == "vertical" else "16:9"
                cutter = VideoCutter(
                    output_dir=output_dir,
                    aspect_ratio=aspect_ratio,
                    show_title=show_title,
                )
                clip_results = await loop.run_in_executor(
                    None, lambda: cutter.cut_all_clips(
                        video_path=video_info.local_path,
                        candidates=candidates,
                        video_id=video_info.video_id,
                    )
                )

                # ── STEP 5: メタデータ生成 ────────────────────────────
                await job_manager.update_batch(
                    batch_job,
                    message=f"[{vi+1}/{total}] タイトル・概要欄を生成中...",
                )
                meta_gen = MetadataGenerator(
                    openai_api_key=openai_api_key,
                    model=openai_model,
                )
                metadata_list = await loop.run_in_executor(
                    None, lambda: meta_gen.generate_batch(
                        candidates=candidates,
                        transcript=transcript,
                        source_video_title=video_info.title,
                        source_channel_name=video_info.channel_title,
                        source_video_url=video_item.url,
                    )
                )

                # 結果をバッチジョブに登録
                for i, (clip_result, meta) in enumerate(zip(clip_results, metadata_list)):
                    if not clip_result.success:
                        continue
                    thumb_url = ""
                    if clip_result.thumbnail_path and clip_result.thumbnail_path.exists():
                        thumb_url = f"/output/{clip_result.thumbnail_path.name}"
                    clip_out = ClipOutput(
                        index=len(batch_job.all_clips),
                        filename=clip_result.output_path.name,
                        title=meta.title,
                        start=candidates[i].start,
                        end=candidates[i].end,
                        duration=candidates[i].duration,
                        score=candidates[i].score,
                        file_size_mb=clip_result.file_size_mb,
                        thumbnail_url=thumb_url,
                    )
                    batch_job.all_clips.append(clip_out)
                    video_item.clips_count += 1

                batch_job.total_clips += video_item.clips_count

                # ── STEP 6: アップロード（オプション） ────────────────
                if do_upload:
                    await job_manager.update_batch(
                        batch_job,
                        message=f"[{vi+1}/{total}] YouTubeにアップロード中...",
                    )
                    from src.uploader import YouTubeUploader
                    uploader = YouTubeUploader(
                        client_secrets_file=client_secrets,
                        default_privacy=privacy,
                    )
                    for i, (clip_result, meta) in enumerate(zip(clip_results, metadata_list)):
                        if not clip_result.success:
                            continue
                        scheduled_at = scheduled_times[schedule_cursor] if scheduled_times else None
                        schedule_cursor += 1
                        result = await loop.run_in_executor(
                            None, lambda cr=clip_result, m=meta, s=scheduled_at: uploader.upload(
                                clip_result=cr,
                                metadata=m,
                                privacy=privacy,
                                scheduled_at=s,
                            )
                        )
                        # all_clipsから対応エントリを探して更新
                        target_filename = clip_result.output_path.name
                        for co in batch_job.all_clips:
                            if co.filename == target_filename:
                                if result.success:
                                    co.youtube_url = result.video_url
                                    co.youtube_video_id = result.video_id
                                    if result.scheduled_at:
                                        co.scheduled_at = result.scheduled_at.isoformat()
                                break

                video_item.status = "completed"
                await job_manager.update_batch(
                    batch_job,
                    progress=int((vi + 1) / total * 100),
                    message=f"[{vi+1}/{total}] 完了: {video_info.title}（{video_item.clips_count}本）",
                )

            except Exception as e:
                import traceback
                video_item.status = "failed"
                video_item.error = str(e)
                await job_manager.update_batch(
                    batch_job,
                    message=f"[{vi+1}/{total}] エラー: {e} — 次の動画へ続行",
                )
                print(traceback.format_exc())

            finally:
                _cleanup(video_item.video_id, temp_dir)

        # 全動画完了
        await job_manager.update_batch(
            batch_job,
            status=JobStatus.COMPLETED,
            progress=100,
            message=f"全{total}本の動画を処理完了。切り抜き合計: {batch_job.total_clips}本",
        )

    except Exception as e:
        import traceback
        await job_manager.update_batch(
            batch_job,
            status=JobStatus.FAILED,
            error=str(e),
            message=f"致命的エラーが発生しました: {e}",
        )
        print(traceback.format_exc())


# 1日あたりの投稿時刻（JST固定）
DAILY_SLOTS = {
    1: [12],
    2: [12, 18],
    3: [12, 15, 18],
}


def _resolve_schedule_fixed(
    start_date: Optional[str],
    count: int,
    posts_per_day: int,
) -> Optional[list]:
    """投稿開始日と1日あたり投稿回数から固定時刻スケジュールを生成する"""
    if not start_date:
        return None
    try:
        from datetime import date as date_cls
        hours = DAILY_SLOTS.get(posts_per_day, [12])
        base = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=JST)
        result = []
        day_offset = 0
        slot_idx = 0
        while len(result) < count:
            dt = (base + timedelta(days=day_offset)).replace(
                hour=hours[slot_idx], minute=0, second=0, microsecond=0
            )
            result.append(dt.astimezone(timezone.utc))
            slot_idx += 1
            if slot_idx >= len(hours):
                slot_idx = 0
                day_offset += 1
        return result
    except Exception:
        return None


def _cleanup(video_id: str, temp_dir: str) -> None:
    if not video_id:
        return
    temp_path = Path(temp_dir)
    for pattern in [f"{video_id}.mp4", f"{video_id}_audio.wav", f"{video_id}.wav"]:
        f = temp_path / pattern
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass
