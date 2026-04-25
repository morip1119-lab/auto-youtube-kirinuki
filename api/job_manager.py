"""
ジョブ管理システム
非同期処理キューとリアルタイム進捗配信を担当する
"""
import uuid
import json
import asyncio
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from collections import defaultdict


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStep(str, Enum):
    DOWNLOAD = "download"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    CUT = "cut"
    METADATA = "metadata"
    UPLOAD = "upload"
    DONE = "done"


STEP_LABELS = {
    JobStep.DOWNLOAD: "動画をダウンロード中",
    JobStep.TRANSCRIBE: "文字起こし中 (Whisper)",
    JobStep.ANALYZE: "AI分析・切り抜き箇所を特定中",
    JobStep.CUT: "動画を切り抜き中",
    JobStep.METADATA: "タイトル・概要欄を生成中",
    JobStep.UPLOAD: "YouTubeにアップロード中",
    JobStep.DONE: "完了",
}

STEP_WEIGHTS = {
    JobStep.DOWNLOAD: 10,
    JobStep.TRANSCRIBE: 30,
    JobStep.ANALYZE: 10,
    JobStep.CUT: 20,
    JobStep.METADATA: 10,
    JobStep.UPLOAD: 20,
}


@dataclass
class ClipOutput:
    """生成された切り抜き動画の情報"""
    index: int
    filename: str
    title: str
    start: float
    end: float
    duration: float
    score: float
    file_size_mb: float
    thumbnail_url: str = ""
    youtube_url: str = ""
    youtube_video_id: str = ""
    scheduled_at: Optional[str] = None


@dataclass
class Job:
    """ジョブの状態を保持するデータクラス"""
    id: str
    url: str
    status: JobStatus = JobStatus.PENDING
    current_step: Optional[JobStep] = None
    progress: int = 0  # 0-100
    message: str = ""
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    video_title: str = ""
    video_duration: int = 0
    clips: list[ClipOutput] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["current_step"] = self.current_step.value if self.current_step else None
        return d


class JobManager:
    """ジョブの作成・管理・進捗配信を担当するシングルトンクラス"""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        # WebSocket接続リスト (job_id -> set of queues)
        self._subscribers: dict[str, set] = defaultdict(set)

    def create_job(self, url: str, settings: dict) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, url=url, settings=settings)
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[Job]:
        return list(reversed(list(self._jobs.values())))

    def subscribe(self, job_id: str) -> asyncio.Queue:
        """WebSocket用の通知キューを登録"""
        q = asyncio.Queue()
        self._subscribers[job_id].add(q)
        return q

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        self._subscribers[job_id].discard(queue)

    async def update(
        self,
        job: Job,
        status: Optional[JobStatus] = None,
        step: Optional[JobStep] = None,
        progress: Optional[int] = None,
        message: str = "",
        error: str = "",
        **kwargs,
    ) -> None:
        """ジョブ状態を更新してWebSocket購読者に通知"""
        if status:
            job.status = status
            if status == JobStatus.RUNNING and not job.started_at:
                job.started_at = datetime.now().isoformat()
            elif status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                job.completed_at = datetime.now().isoformat()

        if step:
            job.current_step = step
            # ステップに応じた進捗を自動計算
            if progress is None:
                steps = list(STEP_WEIGHTS.keys())
                done_steps = steps[:steps.index(step)]
                base = sum(STEP_WEIGHTS[s] for s in done_steps)
                job.progress = min(base, 95)
            job.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {STEP_LABELS.get(step, step.value)}")

        if progress is not None:
            job.progress = min(progress, 100)

        if message:
            job.message = message
            job.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

        if error:
            job.error = error

        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)

        await self._notify(job)

    async def _notify(self, job: Job) -> None:
        """登録済みのWebSocket購読者にイベントを送信"""
        payload = json.dumps(job.to_dict(), ensure_ascii=False)
        dead_queues = set()
        for q in self._subscribers.get(job.id, set()):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead_queues.add(q)
        for q in dead_queues:
            self._subscribers[job.id].discard(q)


# グローバルシングルトン
job_manager = JobManager()
