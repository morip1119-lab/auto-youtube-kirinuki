"""
メタデータ生成モジュール
GPT-4o-mini を使用して切り抜き動画のタイトル・概要欄・タグを一括生成する
コスト最適化: 分析(GPT-4o) と メタデータ生成(GPT-4o-mini) でモデルを分離
さらに N本分を1回のAPIコールでまとめて生成することでコストを削減
"""
import json
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI
from rich.console import Console

from .analyzer import ClipCandidate
from .transcriber import Transcript

console = Console()

# メタデータ生成は4o-miniで十分（分析精度より文章生成が主タスク）
METADATA_MODEL_DEFAULT = "gpt-4o-mini"


@dataclass
class VideoMetadata:
    """YouTube動画メタデータを保持するデータクラス"""
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)  # 概要欄末尾に追加するハッシュタグ
    category_id: str = "22"
    language: str = "ja"
    # 元動画情報（概要欄に記載する引用情報）
    source_video_title: str = ""
    source_channel_name: str = ""
    source_video_url: str = ""

    def format_description(self, footer: str = "") -> str:
        """最終的な概要欄テキストを生成"""
        parts = [self.description]

        if self.source_video_title or self.source_video_url:
            parts.append("\n---")
            parts.append("【元動画情報】")
            if self.source_video_title:
                parts.append(f"タイトル: {self.source_video_title}")
            if self.source_channel_name:
                parts.append(f"チャンネル: {self.source_channel_name}")
            if self.source_video_url:
                parts.append(f"URL: {self.source_video_url}")

        if footer:
            parts.append(footer)

        # ハッシュタグを末尾に追加（# が付いていなければ付与）
        if self.hashtags:
            ht_line = " ".join(
                h if h.startswith("#") else f"#{h}"
                for h in self.hashtags[:3]
            )
            parts.append(f"\n{ht_line}")

        return "\n".join(parts)


class MetadataGenerator:
    """GPT-4o-miniを使用したYouTubeメタデータ一括生成クラス"""

    def __init__(
        self,
        openai_api_key: str,
        model: str = METADATA_MODEL_DEFAULT,
        max_tags: int = 25,
        description_footer: str = "",
    ):
        self.client = OpenAI(api_key=openai_api_key)
        self.model = model
        self.max_tags = max_tags
        self.description_footer = description_footer

    def generate(
        self,
        candidate: ClipCandidate,
        transcript: Optional[Transcript] = None,
        source_video_title: str = "",
        source_channel_name: str = "",
        source_video_url: str = "",
    ) -> VideoMetadata:
        """
        切り抜き候補のメタデータを生成

        Args:
            candidate: 切り抜き候補
            transcript: 文字起こしデータ（精度向上のため）
            source_video_title: 元動画タイトル
            source_channel_name: 元チャンネル名
            source_video_url: 元動画URL
        """
        clip_text = ""
        if transcript:
            clip_text = transcript.get_text_in_range(candidate.start, candidate.end)
            if len(clip_text) > 3000:
                clip_text = clip_text[:3000] + "..."

        system_prompt = """あなたはYouTube切り抜き動画の専門マーケターです。
視聴回数を最大化するために、魅力的なタイトル・概要欄・タグ・ハッシュタグを生成してください。

必ず以下のJSON形式で回答してください（他のテキストは不要）：
{
  "title": "タイトル（50文字以内、クリックしたくなる魅力的なもの）",
  "description": "概要欄（200〜400文字程度、内容紹介・見どころを記載。タイムスタンプは不要）",
  "tags": ["タグ1", "タグ2", ..., "タグ25"],
  "hashtags": ["ハッシュタグ1", "ハッシュタグ2", "ハッシュタグ3"]
}"""

        user_prompt = f"""以下の切り抜き動画のメタデータを生成してください。

【元動画タイトル】{source_video_title or "不明"}
【チャンネル名】{source_channel_name or "不明"}
【切り抜きタイトル案】{candidate.suggested_title}
【選定理由】{candidate.reason}
【キーワード】{", ".join(candidate.keywords) if candidate.keywords else "なし"}
【切り抜き時間】{int(candidate.duration // 60)}分{int(candidate.duration % 60)}秒
【重要度スコア】{candidate.score:.2f}

【切り抜き部分の内容】
{clip_text if clip_text else "（文字起こしなし）"}

【要件】
- タイトルは視聴者の好奇心を刺激するもの（「〇〇した結果...」「〇〇の真相」等の形式も可）
- 概要欄には内容の見どころを具体的に記載（タイムスタンプは不要）
- タグは検索されやすいワードを20〜25個含める（一般的なワードから具体的なワードまで幅広く）
- ハッシュタグは動画内容に最も関連する3つを選ぶ（# は付けずに単語のみ）
- すべて日本語で作成"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
            )

            result = json.loads(response.choices[0].message.content)

            tags = result.get("tags", [])[:self.max_tags]
            hashtags = result.get("hashtags", [])[:3]

            metadata = VideoMetadata(
                title=result.get("title", candidate.suggested_title)[:100],
                description=result.get("description", ""),
                tags=tags,
                hashtags=hashtags,
                source_video_title=source_video_title,
                source_channel_name=source_channel_name,
                source_video_url=source_video_url,
            )

            console.print(f"[green]メタデータ生成完了: 「{metadata.title}」[/green]")
            return metadata

        except Exception as e:
            console.print(f"[red]メタデータ生成エラー: {e}[/red]")
            # フォールバック: 候補情報から最低限のメタデータを生成
            return VideoMetadata(
                title=candidate.suggested_title[:100],
                description=f"{candidate.reason}\n\n元動画: {source_video_title}",
                tags=candidate.keywords[:self.max_tags],
                source_video_title=source_video_title,
                source_channel_name=source_channel_name,
                source_video_url=source_video_url,
            )

    def generate_batch(
        self,
        candidates: list[ClipCandidate],
        transcript: Optional[Transcript] = None,
        source_video_title: str = "",
        source_channel_name: str = "",
        source_video_url: str = "",
    ) -> list[VideoMetadata]:
        """複数の切り抜き候補のメタデータを一括生成"""
        console.print(f"\n[bold cyan]メタデータ生成: {len(candidates)}本[/bold cyan]")
        results = []
        for i, candidate in enumerate(candidates, 1):
            console.print(f"  [{i}/{len(candidates)}] {candidate.suggested_title}")
            metadata = self.generate(
                candidate=candidate,
                transcript=transcript,
                source_video_title=source_video_title,
                source_channel_name=source_channel_name,
                source_video_url=source_video_url,
            )
            results.append(metadata)
        return results

    def save_metadata(self, metadata_list: list[VideoMetadata], output_path) -> None:
        """メタデータリストをJSONファイルに保存"""
        data = [
            {
                "title": m.title,
                "description": m.format_description(self.description_footer),
                "tags": m.tags,
                "category_id": m.category_id,
                "language": m.language,
                "source_video_title": m.source_video_title,
                "source_channel_name": m.source_channel_name,
                "source_video_url": m.source_video_url,
            }
            for m in metadata_list
        ]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        console.print(f"[green]メタデータ保存: {output_path}[/green]")
