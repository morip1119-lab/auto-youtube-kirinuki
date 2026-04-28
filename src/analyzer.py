"""
切り抜き箇所分析モジュール
GPT-4を使用して文字起こしから最適な切り抜き箇所を特定する
"""
import json
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI
from rich.console import Console

from .transcriber import Transcript

console = Console()


@dataclass
class ClipCandidate:
    """切り抜き候補を表すデータクラス"""
    start: float          # 開始時刻（秒）
    end: float            # 終了時刻（秒）
    duration: float       # 長さ（秒）
    score: float          # 重要度スコア (0.0〜1.0)
    reason: str           # 選定理由
    suggested_title: str  # 提案タイトル
    keywords: list[str] = field(default_factory=list)  # キーワード

    @property
    def start_str(self) -> str:
        return f"{int(self.start // 60):02d}:{int(self.start % 60):02d}"

    @property
    def end_str(self) -> str:
        return f"{int(self.end // 60):02d}:{int(self.end % 60):02d}"

    def __str__(self) -> str:
        return (
            f"[{self.start_str}〜{self.end_str}] "
            f"スコア:{self.score:.2f} - {self.suggested_title}"
        )


class VideoAnalyzer:
    """GPT-4を使用した動画コンテンツ分析クラス"""

    def __init__(
        self,
        openai_api_key: str,
        model: str = "gpt-4o",
        clips_per_video: int = 3,
        min_clip_duration: int = 60,
        max_clip_duration: int = 600,
        min_score: float = 0.4,
    ):
        self.client = OpenAI(api_key=openai_api_key)
        self.model = model
        self.clips_per_video = clips_per_video
        self.min_clip_duration = min_clip_duration
        self.max_clip_duration = max_clip_duration
        self.min_score = min_score

    def analyze(
        self,
        transcript: Transcript,
        video_title: str = "",
        video_description: str = "",
        target_duration: Optional[int] = None,
    ) -> list[ClipCandidate]:
        """
        文字起こしを分析して切り抜き候補を返す

        Args:
            transcript: 文字起こしデータ
            video_title: 動画タイトル（分析精度向上のため）
            video_description: 動画説明文
            target_duration: 切り抜き目標時間（秒）。Noneの場合は自動決定
        """
        if not transcript.segments:
            console.print("[red]文字起こしセグメントが空です[/red]")
            return []

        total_duration = transcript.segments[-1].end if transcript.segments else 0
        min_dur = target_duration or self.min_clip_duration
        max_dur = target_duration or self.max_clip_duration

        console.print(
            f"[cyan]GPTで動画を分析中 (モデル: {self.model})...[/cyan]"
        )
        console.print(
            f"  動画長: {int(total_duration // 60)}分{int(total_duration % 60)}秒"
        )

        timestamped_text = transcript.to_timestamped_text()
        # トークン数制限のため長すぎる場合は要約
        if len(timestamped_text) > 60000:
            timestamped_text = timestamped_text[:60000] + "\n... (以下省略)"

        system_prompt = f"""あなたはYouTube動画の切り抜き専門家です。
動画の文字起こしを分析して、視聴者が最も興味を持つハイライトシーンを特定してください。

【重要】各クリップは必ず {min_dur}秒以上 {max_dur}秒以下の長さにしてください。
start_seconds と end_seconds の差が {min_dur} 以上になるよう設定してください。
例: start_seconds=120, end_seconds=180 → 60秒のクリップ ✓
例: start_seconds=120, end_seconds=122 → 2秒のクリップ ✗（絶対に禁止）

以下の基準で評価してください：
- 盛り上がり・感情的な瞬間（笑い、驚き、感動）
- 重要な情報・有益なノウハウ
- 面白いエピソードや体験談
- インパクトのある発言や名言
- 物語の転換点・クライマックス

必ず以下のJSON形式で回答してください（他のテキストは不要）：
{{
  "clips": [
    {{
      "start_seconds": 開始秒数（整数）,
      "end_seconds": 終了秒数（整数、start_secondsより必ず{min_dur}以上大きい値）,
      "score": 重要度スコア（0.0〜1.0の数値）,
      "reason": "選定理由（日本語）",
      "suggested_title": "切り抜きタイトル案（日本語・30文字以内）",
      "keywords": ["キーワード1", "キーワード2", "キーワード3"]
    }}
  ]
}}"""

        user_prompt = f"""以下の動画を分析して、切り抜き候補を{self.clips_per_video}個程度提案してください。

【動画タイトル】{video_title}
【動画説明】{video_description[:500] if video_description else "なし"}
【動画長】{int(total_duration // 60)}分{int(total_duration % 60)}秒

【制約条件（必ず守ること）】
- 各切り抜きの長さ: 必ず{min_dur}秒以上{max_dur}秒以下
- end_seconds - start_seconds >= {min_dur} を厳守
- 切り抜き同士が重複しないようにする

【文字起こし（タイムスタンプ付き）】
{timestamped_text}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )

            result = json.loads(response.choices[0].message.content)
            raw_clips = result.get("clips", [])

        except Exception as e:
            console.print(f"[red]GPT分析エラー: {e}[/red]")
            return []

        def _parse_time(clip: dict, *keys) -> float:
            """複数のキー名 / MM:SS形式 / 数値をまとめてパース"""
            for k in keys:
                v = clip.get(k)
                if v is None:
                    continue
                try:
                    # "MM:SS" or "HH:MM:SS" 形式を秒に変換
                    if isinstance(v, str) and ":" in v:
                        parts = v.split(":")
                        secs = 0.0
                        for p in parts:
                            secs = secs * 60 + float(p)
                        return secs
                    return float(v)
                except (ValueError, TypeError):
                    continue
            return 0.0

        def _build_candidates(clips: list, score_threshold: float) -> list:
            result = []
            for clip in clips:
                start = _parse_time(clip, "start_seconds", "start", "start_time", "startSeconds")
                end   = _parse_time(clip, "end_seconds",   "end",   "end_time",   "endSeconds")
                duration = end - start
                score = float(clip.get("score", 0))

                # 短すぎる場合は捨てずに中心を保ったまま min_dur に延長
                if duration < min_dur * 0.5:
                    mid = (start + end) / 2 if end > start else start
                    start = max(0, mid - min_dur / 2)
                    end   = start + min_dur
                    duration = min_dur
                    console.print(
                        f"[yellow]短いクリップを延長: {duration:.0f}秒 → {min_dur}秒[/yellow]"
                    )
                if duration > max_dur * 1.2:
                    end = start + max_dur
                    duration = max_dur
                if score < score_threshold:
                    continue
                if end > total_duration:
                    end = total_duration
                    duration = end - start
                if duration <= 0:
                    continue

                result.append(ClipCandidate(
                    start=start,
                    end=end,
                    duration=duration,
                    score=score,
                    reason=clip.get("reason", ""),
                    suggested_title=clip.get("suggested_title", "切り抜き"),
                    keywords=clip.get("keywords", []),
                ))
            return result

        # スコア閾値を段階的に下げてフォールバック
        candidates = _build_candidates(raw_clips, self.min_score)
        if not candidates:
            candidates = _build_candidates(raw_clips, 0.3)
        if not candidates:
            candidates = _build_candidates(raw_clips, 0.0)

        # スコア順にソートして重複除去
        candidates.sort(key=lambda x: x.score, reverse=True)
        candidates = self._remove_overlapping(candidates)

        console.print(f"[green]分析完了: {len(candidates)}個の切り抜き候補[/green]")
        for i, c in enumerate(candidates, 1):
            console.print(f"  {i}. {c}")

        return candidates

    def _remove_overlapping(self, candidates: list[ClipCandidate]) -> list[ClipCandidate]:
        """重複する切り抜き候補を除去（スコアが高い方を優先）"""
        result = []
        for candidate in candidates:
            overlaps = False
            for existing in result:
                # 50%以上重複していれば重複とみなす
                overlap_start = max(candidate.start, existing.start)
                overlap_end = min(candidate.end, existing.end)
                overlap_duration = max(0, overlap_end - overlap_start)
                min_duration = min(candidate.duration, existing.duration)
                if min_duration > 0 and overlap_duration / min_duration > 0.5:
                    overlaps = True
                    break
            if not overlaps:
                result.append(candidate)
        return result

    def save_candidates(self, candidates: list[ClipCandidate], output_path) -> None:
        """切り抜き候補をJSONファイルに保存"""
        data = [
            {
                "start": c.start,
                "end": c.end,
                "duration": c.duration,
                "score": c.score,
                "reason": c.reason,
                "suggested_title": c.suggested_title,
                "keywords": c.keywords,
            }
            for c in candidates
        ]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        console.print(f"[green]候補情報保存: {output_path}[/green]")

    @staticmethod
    def load_candidates(json_path) -> list[ClipCandidate]:
        """JSONファイルから切り抜き候補を読み込み"""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [
            ClipCandidate(
                start=item["start"],
                end=item["end"],
                duration=item["duration"],
                score=item["score"],
                reason=item["reason"],
                suggested_title=item["suggested_title"],
                keywords=item.get("keywords", []),
            )
            for item in data
        ]
