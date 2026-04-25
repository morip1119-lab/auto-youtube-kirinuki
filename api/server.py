"""
FastAPI サーバー
WebUIのバックエンドAPIとWebSocket進捗配信を担当する
"""
import os
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from dotenv import load_dotenv
load_dotenv()

from .job_manager import job_manager, JobStatus, BatchVideoItem
from .pipeline import run_pipeline
from .batch_pipeline import run_batch_pipeline

app = FastAPI(title="YouTube切り抜きツール", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静的ファイル・出力ファイルの配信
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/output", StaticFiles(directory="output"), name="output")


# ── リクエストモデル ──────────────────────────────────────────────────

class JobCreateRequest(BaseModel):
    url: str
    clips_count: int = 3
    min_duration: int = 60
    max_duration: int = 300
    output_format: str = "horizontal"   # horizontal / vertical
    clip_mode: str = "auto"             # auto / manual
    manual_segments: list[dict] = []
    show_title: bool = True
    title_text: str = ""                # タイトルオーバーレイ文字列（空=AI生成）
    whisper_model: str = "small"
    device: str = "cpu"
    do_upload: bool = False
    privacy: str = "private"
    schedule_at: Optional[str] = None
    schedule_interval: int = 24


# ── API エンドポイント ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """メインのWebUI HTMLを返す"""
    html_path = Path("templates/index.html")
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>テンプレートが見つかりません</h1>")


@app.post("/api/jobs")
async def create_job(req: JobCreateRequest, background_tasks: BackgroundTasks):
    """新しいジョブを作成して処理を開始する"""
    # URLの簡易バリデーション
    if "youtube.com" not in req.url and "youtu.be" not in req.url:
        raise HTTPException(400, "YouTube の URL を入力してください")

    job = job_manager.create_job(url=req.url, settings=req.model_dump())
    background_tasks.add_task(run_pipeline, job)
    return {"job_id": job.id, "status": job.status.value}


@app.get("/api/jobs")
async def list_jobs():
    """ジョブ一覧を返す"""
    jobs = job_manager.get_all_jobs()
    return [j.to_dict() for j in jobs]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    """指定ジョブの状態を返す"""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "ジョブが見つかりません")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/download/{filename}")
async def download_clip(job_id: str, filename: str):
    """切り抜き動画ファイルをダウンロードする"""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "ジョブが見つかりません")
    file_path = Path("output") / filename
    if not file_path.exists():
        raise HTTPException(404, "ファイルが見つかりません")
    return FileResponse(str(file_path), media_type="video/mp4", filename=filename)


@app.get("/api/video-info")
async def get_video_info(url: str):
    """YouTube動画の情報だけ取得（ダウンロードなし）"""
    try:
        from src.downloader import YouTubeDownloader
        loop = asyncio.get_event_loop()
        downloader = YouTubeDownloader()
        info = await loop.run_in_executor(None, lambda: downloader.get_video_info(url))
        return {
            "title": info.title,
            "channel": info.channel_title,
            "duration": info.duration,
            "thumbnail": info.thumbnail_url,
            "video_id": info.video_id,
        }
    except Exception as e:
        raise HTTPException(400, f"動画情報の取得に失敗しました: {e}")


@app.get("/api/channel-videos")
async def get_channel_videos(
    url: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    max_videos: int = 50,
    sort_order: str = "newest",
):
    """チャンネルの動画リストを期間フィルター付きで取得"""
    try:
        from src.downloader import YouTubeDownloader
        loop = asyncio.get_event_loop()
        downloader = YouTubeDownloader()
        videos = await loop.run_in_executor(
            None,
            lambda: downloader.get_channel_videos(
                channel_url=url,
                date_from=date_from,
                date_to=date_to,
                max_videos=max_videos,
                sort_order=sort_order,
            ),
        )
        return [
            {
                "video_id": v.video_id,
                "title": v.title,
                "duration": v.duration,
                "upload_date": v.upload_date,
                "view_count": v.view_count,
                "thumbnail_url": v.thumbnail_url,
                "url": f"https://www.youtube.com/watch?v={v.video_id}",
            }
            for v in videos
        ]
    except Exception as e:
        raise HTTPException(400, f"チャンネル動画リストの取得に失敗しました: {e}")


class BatchJobCreateRequest(BaseModel):
    channel_url: str
    video_urls: list[str]        # 処理する動画URLリスト
    video_titles: list[str] = [] # 各動画のタイトル（表示用）
    video_thumbnails: list[str] = []
    video_upload_dates: list[str] = []
    video_durations: list[int] = []
    # クリップ設定
    clips_count: int = 3
    min_duration: int = 60
    max_duration: int = 300
    output_format: str = "horizontal"
    show_title: bool = True
    whisper_model: str = "small"
    device: str = "cpu"
    # アップロード設定
    do_upload: bool = False
    privacy: str = "private"
    schedule_date: Optional[str] = None   # 投稿開始日 YYYY-MM-DD
    posts_per_day: int = 1                # 1日あたりの投稿回数 (1/2/3)


@app.post("/api/batch-jobs")
async def create_batch_job(req: BatchJobCreateRequest, background_tasks: BackgroundTasks):
    """チャンネル一括処理ジョブを作成して開始する"""
    if not req.video_urls:
        raise HTTPException(400, "処理する動画URLが指定されていません")

    videos = []
    for i, url in enumerate(req.video_urls):
        videos.append(BatchVideoItem(
            index=i,
            url=url,
            title=req.video_titles[i] if i < len(req.video_titles) else "",
            thumbnail=req.video_thumbnails[i] if i < len(req.video_thumbnails) else "",
            upload_date=req.video_upload_dates[i] if i < len(req.video_upload_dates) else "",
            duration=req.video_durations[i] if i < len(req.video_durations) else 0,
        ))

    batch_job = job_manager.create_batch_job(
        channel_url=req.channel_url,
        videos=videos,
        settings=req.model_dump(),
    )
    background_tasks.add_task(run_batch_pipeline, batch_job)
    return {"job_id": batch_job.id, "status": batch_job.status.value, "total": len(videos)}


@app.get("/api/batch-jobs")
async def list_batch_jobs():
    """バッチジョブ一覧を返す"""
    jobs = job_manager.get_all_batch_jobs()
    return [j.to_dict() for j in jobs]


@app.get("/api/batch-jobs/{job_id}")
async def get_batch_job(job_id: str):
    """指定バッチジョブの状態を返す"""
    job = job_manager.get_batch_job(job_id)
    if not job:
        raise HTTPException(404, "バッチジョブが見つかりません")
    return job.to_dict()


@app.websocket("/ws/batch-jobs/{job_id}")
async def batch_job_progress_ws(websocket: WebSocket, job_id: str):
    """バッチジョブの進捗をリアルタイムで配信するWebSocket"""
    await websocket.accept()
    job = job_manager.get_batch_job(job_id)
    if not job:
        await websocket.send_json({"error": "バッチジョブが見つかりません"})
        await websocket.close()
        return
    await websocket.send_text(
        __import__("json").dumps(job.to_dict(), ensure_ascii=False)
    )
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        await websocket.close()
        return
    queue = job_manager.subscribe(job_id)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(payload)
                import json
                data = json.loads(payload)
                if data.get("status") in ("completed", "failed", "cancelled"):
                    break
            except asyncio.TimeoutError:
                await websocket.send_json({"ping": True})
    except WebSocketDisconnect:
        pass
    finally:
        job_manager.unsubscribe(job_id, queue)
        try:
            await websocket.close()
        except Exception:
            pass


@app.delete("/api/youtube/token")
async def delete_youtube_token():
    """YouTube認証トークンを削除して再認証を促す"""
    token_path = Path(os.environ.get("YOUTUBE_TOKEN_FILE", "youtube_token.pickle"))
    if token_path.exists():
        token_path.unlink()
        return {"message": "認証トークンを削除しました。次のアップロード時に再認証が必要です。"}
    return {"message": "トークンは既に未認証状態です。"}


@app.get("/api/youtube/token-status")
async def get_youtube_token_status():
    """YouTube認証済みかどうかを返す"""
    token_path = Path(os.environ.get("YOUTUBE_TOKEN_FILE", "youtube_token.pickle"))
    return {"authenticated": token_path.exists()}


@app.get("/api/settings")
async def get_settings():
    """現在の環境設定を返す（APIキー有無など）"""
    return {
        "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "youtube_configured": Path(os.environ.get("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")).exists(),
        "whisper_model": os.environ.get("WHISPER_MODEL", "small"),
        "device": os.environ.get("WHISPER_DEVICE", "cpu"),
    }


# ── WebSocket ────────────────────────────────────────────────────────

@app.websocket("/ws/jobs/{job_id}")
async def job_progress_ws(websocket: WebSocket, job_id: str):
    """ジョブの進捗をリアルタイムで配信するWebSocket"""
    await websocket.accept()

    job = job_manager.get_job(job_id)
    if not job:
        await websocket.send_json({"error": "ジョブが見つかりません"})
        await websocket.close()
        return

    # まず現在の状態を送信
    await websocket.send_text(
        __import__("json").dumps(job.to_dict(), ensure_ascii=False)
    )

    # 完了済みの場合はそのまま閉じる
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        await websocket.close()
        return

    # 購読開始
    queue = job_manager.subscribe(job_id)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(payload)

                # 完了・失敗したら接続を閉じる
                import json
                data = json.loads(payload)
                if data.get("status") in ("completed", "failed", "cancelled"):
                    break
            except asyncio.TimeoutError:
                # KeepAlive ping
                await websocket.send_json({"ping": True})

    except WebSocketDisconnect:
        pass
    finally:
        job_manager.unsubscribe(job_id, queue)
        try:
            await websocket.close()
        except Exception:
            pass
