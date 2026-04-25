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

from .job_manager import job_manager, JobStatus
from .pipeline import run_pipeline

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
