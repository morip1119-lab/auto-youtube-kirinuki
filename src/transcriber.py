"""
音声文字起こしモジュール
faster-whisper を使用してローカルで高速な文字起こしを実行する
"""
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console()


@dataclass
class TranscriptSegment:
    """文字起こしセグメント（タイムスタンプ付き）"""
    start: float   # 開始時刻（秒）
    end: float     # 終了時刻（秒）
    text: str      # テキスト内容
    confidence: float = 1.0  # 信頼度スコア


@dataclass
class Transcript:
    """文字起こし全体を保持するデータクラス"""
    video_id: str
    language: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    full_text: str = ""

    def to_srt(self) -> str:
        """SRT字幕形式に変換"""
        lines = []
        for i, seg in enumerate(self.segments, 1):
            start = _seconds_to_srt_time(seg.start)
            end = _seconds_to_srt_time(seg.end)
            lines.append(f"{i}\n{start} --> {end}\n{seg.text.strip()}\n")
        return "\n".join(lines)

    def to_timestamped_text(self) -> str:
        """タイムスタンプ付きテキスト形式に変換（GPT分析用）"""
        lines = []
        for seg in self.segments:
            start_min = int(seg.start // 60)
            start_sec = int(seg.start % 60)
            lines.append(f"[{start_min:02d}:{start_sec:02d}] {seg.text.strip()}")
        return "\n".join(lines)

    def get_text_in_range(self, start: float, end: float) -> str:
        """指定時間範囲のテキストを取得"""
        texts = [
            seg.text.strip()
            for seg in self.segments
            if seg.start >= start and seg.end <= end
        ]
        return " ".join(texts)


def _seconds_to_srt_time(seconds: float) -> str:
    """秒をSRT時刻形式（HH:MM:SS,mmm）に変換"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


class Transcriber:
    """faster-whisperを使用した音声文字起こしクラス"""

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cpu",
        language: Optional[str] = "ja",
        vad_filter: bool = True,
    ):
        self.model_size = model_size
        self.device = device
        self.language = language
        self.vad_filter = vad_filter
        self._model = None

    def _load_model(self):
        """モデルを遅延ロード"""
        if self._model is None:
            from faster_whisper import WhisperModel
            console.print(f"[cyan]Whisperモデルをロード中: {self.model_size} ({self.device})[/cyan]")
            compute_type = "float16" if self.device == "cuda" else "int8"
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=compute_type,
            )
            console.print("[green]モデルのロード完了[/green]")

    def transcribe(self, audio_path: Path, video_id: str = "") -> Transcript:
        """音声ファイルを文字起こし"""
        self._load_model()

        console.print(f"[cyan]文字起こしを開始: {audio_path.name}[/cyan]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("文字起こし処理中...", total=None)

            segments_iter, info = self._model.transcribe(
                str(audio_path),
                language=self.language,
                vad_filter=self.vad_filter,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=False,
            )

            detected_lang = info.language
            segments = []
            full_texts = []

            for seg in segments_iter:
                transcript_seg = TranscriptSegment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                    confidence=seg.avg_logprob,
                )
                segments.append(transcript_seg)
                full_texts.append(seg.text.strip())

            progress.update(task, completed=True)

        transcript = Transcript(
            video_id=video_id,
            language=detected_lang,
            segments=segments,
            full_text=" ".join(full_texts),
        )

        console.print(
            f"[green]文字起こし完了: {len(segments)}セグメント, 言語: {detected_lang}[/green]"
        )
        return transcript

    def transcribe_video(self, video_path: Path, video_id: str = "") -> Transcript:
        """動画ファイルから直接文字起こし（音声抽出→文字起こし）"""
        import ffmpeg
        import tempfile

        audio_path = video_path.with_suffix(".wav")
        if not audio_path.exists():
            console.print("[cyan]動画から音声を抽出中...[/cyan]")
            (
                ffmpeg
                .input(str(video_path))
                .output(
                    str(audio_path),
                    acodec="pcm_s16le",
                    ar=16000,
                    ac=1,
                )
                .overwrite_output()
                .run(quiet=True)
            )

        return self.transcribe(audio_path, video_id)

    def save_transcript(self, transcript: Transcript, output_path: Path) -> None:
        """文字起こし結果をJSONファイルとして保存"""
        data = {
            "video_id": transcript.video_id,
            "language": transcript.language,
            "full_text": transcript.full_text,
            "segments": [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "confidence": seg.confidence,
                }
                for seg in transcript.segments
            ],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        console.print(f"[green]文字起こし保存: {output_path}[/green]")

    @staticmethod
    def load_transcript(json_path: Path) -> Transcript:
        """JSONファイルから文字起こしを読み込み"""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        segments = [
            TranscriptSegment(
                start=seg["start"],
                end=seg["end"],
                text=seg["text"],
                confidence=seg.get("confidence", 1.0),
            )
            for seg in data["segments"]
        ]

        return Transcript(
            video_id=data["video_id"],
            language=data["language"],
            segments=segments,
            full_text=data.get("full_text", ""),
        )
