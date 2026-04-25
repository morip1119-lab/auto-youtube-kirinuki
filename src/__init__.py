"""
YouTube切り抜き動画自動生成ツール - srcパッケージ
"""
from .downloader import YouTubeDownloader, VideoInfo
from .transcriber import Transcriber, Transcript, TranscriptSegment
from .analyzer import VideoAnalyzer, ClipCandidate
from .clipper import VideoCutter, ClipResult
from .metadata_generator import MetadataGenerator, VideoMetadata
from .uploader import YouTubeUploader, UploadResult

__all__ = [
    "YouTubeDownloader", "VideoInfo",
    "Transcriber", "Transcript", "TranscriptSegment",
    "VideoAnalyzer", "ClipCandidate",
    "VideoCutter", "ClipResult",
    "MetadataGenerator", "VideoMetadata",
    "YouTubeUploader", "UploadResult",
]
