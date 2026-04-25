"""
処理パイプライン
ジョブを受け取り、ダウンロード→文字起こし→分析→切り抜き→アップロードを実行する
"""
import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from dateutil import parser as dateutil_parser

from .job_manager import job_manager, Job, JobStatus, JobStep, ClipOutput

load_dotenv()

JST = timezone(timedelta(hours=9))


async def run_pipeline(job: Job) -> None:
    """メインパイプラインを非同期で実行"""
    await job_manager.update(job, status=JobStatus.RUNNING, message="処理を開始します")

    settings = job.settings
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    whisper_model = settings.get("whisper_model", os.environ.get("WHISPER_MODEL", "small"))
    whisper_device = settings.get("device", os.environ.get("WHISPER_DEVICE", "cpu"))
    max_height = int(os.environ.get("MAX_VIDEO_HEIGHT", 1080))
    temp_dir = "temp"
    output_dir = "output"
    client_secrets = os.environ.get("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")

    # 設定値
    clips_count = settings.get("clips_count", 3)
    min_duration = settings.get("min_duration", 60)
    max_duration = settings.get("max_duration", 300)
    output_format = settings.get("output_format", "horizontal")  # horizontal / vertical
    clip_mode = settings.get("clip_mode", "auto")  # auto / manual
    manual_segments = settings.get("manual_segments", [])
    show_title = settings.get("show_title", True)
    privacy = settings.get("privacy", "private")
    do_upload = settings.get("do_upload", False)
    schedule_str = settings.get("schedule_at", None)
    schedule_interval = int(settings.get("schedule_interval", 24))

    try:
        # モジュールを初期化（importを遅延させてパフォーマンス確保）
        from src.downloader import YouTubeDownloader
        from src.transcriber import Transcriber
        from src.analyzer import VideoAnalyzer
        from src.clipper import VideoCutter
        from src.metadata_generator import MetadataGenerator

        # ==============================
        # STEP 1: ダウンロード
        # ==============================
        await job_manager.update(job, step=JobStep.DOWNLOAD, message="動画をダウンロード中...")

        downloader = YouTubeDownloader(output_dir=temp_dir, max_height=max_height)

        loop = asyncio.get_event_loop()
        video_info = await loop.run_in_executor(
            None, lambda: downloader.download_video(job.url)
        )

        await job_manager.update(
            job,
            message=f"ダウンロード完了: {video_info.title}",
            video_title=video_info.title,
            video_duration=video_info.duration,
        )

        # ==============================
        # STEP 2: 文字起こし
        # ==============================
        await job_manager.update(job, step=JobStep.TRANSCRIBE, message="Whisperで文字起こし中...")

        transcript_path = Path(temp_dir) / f"{video_info.video_id}_transcript.json"

        if transcript_path.exists():
            transcript = Transcriber.load_transcript(transcript_path)
            await job_manager.update(job, message="文字起こし済みファイルを使用")
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

        await job_manager.update(
            job,
            message=f"文字起こし完了: {len(transcript.segments)}セグメント",
            progress=45,
        )

        # ==============================
        # STEP 3: AI分析
        # ==============================
        await job_manager.update(job, step=JobStep.ANALYZE, message="GPTで切り抜き箇所を分析中...")

        candidates_path = Path(temp_dir) / f"{video_info.video_id}_candidates.json"

        if clip_mode == "manual" and manual_segments:
            # 手動指定の場合
            from src.analyzer import ClipCandidate
            candidates = [
                ClipCandidate(
                    start=seg["start"],
                    end=seg["end"],
                    duration=seg["end"] - seg["start"],
                    score=1.0,
                    reason="手動指定",
                    suggested_title=seg.get("title", f"クリップ {i+1}"),
                )
                for i, seg in enumerate(manual_segments)
            ]
        elif candidates_path.exists():
            from src.analyzer import VideoAnalyzer
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

        await job_manager.update(
            job,
            message=f"{len(candidates)}個の切り抜き候補を特定",
            progress=60,
        )

        # ==============================
        # STEP 4: 動画切り抜き
        # ==============================
        await job_manager.update(job, step=JobStep.CUT, message="動画を切り抜き中...")

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

        # ==============================
        # STEP 5: メタデータ生成
        # ==============================
        await job_manager.update(job, step=JobStep.METADATA, message="タイトル・概要欄を生成中...")

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
                source_video_url=job.url,
            )
        )

        # クリップ出力情報をジョブに登録
        clip_outputs = []
        for i, (clip_result, meta) in enumerate(zip(clip_results, metadata_list)):
            if clip_result.success:
                thumb_url = ""
                if clip_result.thumbnail_path and clip_result.thumbnail_path.exists():
                    thumb_url = f"/output/{clip_result.thumbnail_path.name}"
                clip_outputs.append(ClipOutput(
                    index=i,
                    filename=clip_result.output_path.name,
                    title=meta.title,
                    start=candidates[i].start,
                    end=candidates[i].end,
                    duration=candidates[i].duration,
                    score=candidates[i].score,
                    file_size_mb=clip_result.file_size_mb,
                    thumbnail_url=thumb_url,
                ))

        job.clips = clip_outputs
        await job_manager.update(job, message=f"{len(clip_outputs)}本の切り抜き完了", progress=85)

        # ==============================
        # STEP 6: アップロード（オプション）
        # ==============================
        if do_upload:
            await job_manager.update(job, step=JobStep.UPLOAD, message="YouTubeにアップロード中...")

            from src.uploader import YouTubeUploader

            # スケジュール生成
            scheduled_times = _resolve_schedule(schedule_str, len(clip_outputs), schedule_interval)

            uploader = YouTubeUploader(
                client_secrets_file=client_secrets,
                default_privacy=privacy,
            )

            for i, (clip_result, meta, clip_out) in enumerate(
                zip(clip_results, metadata_list, clip_outputs)
            ):
                if not clip_result.success:
                    continue

                scheduled_at = scheduled_times[i] if scheduled_times else None
                result = await loop.run_in_executor(
                    None, lambda cr=clip_result, m=meta, s=scheduled_at: uploader.upload(
                        clip_result=cr,
                        metadata=m,
                        privacy=privacy,
                        scheduled_at=s,
                    )
                )
                if result.success:
                    clip_out.youtube_url = result.video_url
                    clip_out.youtube_video_id = result.video_id
                    if result.scheduled_at:
                        clip_out.scheduled_at = result.scheduled_at.isoformat()

                await job_manager.update(
                    job,
                    message=f"アップロード完了: {meta.title}",
                    progress=85 + int(15 * (i + 1) / len(clip_outputs)),
                )

        # 完了
        await job_manager.update(
            job,
            step=JobStep.DONE,
            status=JobStatus.COMPLETED,
            progress=100,
            message="すべての処理が完了しました",
        )

    except Exception as e:
        import traceback
        await job_manager.update(
            job,
            status=JobStatus.FAILED,
            error=str(e),
            message=f"エラーが発生しました: {e}",
        )
        print(traceback.format_exc())

    finally:
        # 一時ファイル削除
        _cleanup(video_info.video_id if 'video_info' in dir() else "", temp_dir)


def _resolve_schedule(
    schedule_str: Optional[str],
    count: int,
    interval_hours: int,
) -> Optional[list]:
    if not schedule_str:
        return None
    try:
        first = dateutil_parser.parse(schedule_str)
        if first.tzinfo is None:
            first = first.replace(tzinfo=JST)
        utc = first.astimezone(timezone.utc)
        return [utc + timedelta(hours=i * interval_hours) for i in range(count)]
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
