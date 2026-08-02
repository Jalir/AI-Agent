"""参考音截取：转写前最多保留前 N 秒，避免长音频耗尽 ASR 额度。"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

import requests

from backend.common.oss import build_file_url, put_object

logger = logging.getLogger(__name__)

# 声音克隆转写上限（秒）
TRANSCRIBE_MAX_SEC = 20.0


def probe_duration_sec(path: Path) -> float | None:
    """ffprobe 读取时长；失败返回 None。"""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("ffprobe unavailable: %s", e)
        return None
    if proc.returncode != 0:
        logger.warning("ffprobe failed: %s", (proc.stderr or "")[:300])
        return None
    raw = (proc.stdout or "").strip()
    try:
        return float(raw)
    except ValueError:
        return None


def trim_mp3_first_seconds(src: Path, dst: Path, *, max_sec: float) -> None:
    """截取前 max_sec 秒为 mp3（优先流复制，失败再重编码）。"""
    cmd_copy = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-t",
        str(max_sec),
        "-c",
        "copy",
        str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd_copy, capture_output=True, text=True, timeout=120, check=False
        )
    except FileNotFoundError as e:
        raise RuntimeError("未安装 ffmpeg，无法截取长音频前 20 秒") from e
    if proc.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
        return

    cmd_re = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-t",
        str(max_sec),
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        str(dst),
    ]
    proc2 = subprocess.run(
        cmd_re, capture_output=True, text=True, timeout=180, check=False
    )
    if proc2.returncode != 0 or not dst.is_file() or dst.stat().st_size <= 0:
        detail = (proc2.stderr or proc.stderr or "")[:400]
        raise RuntimeError(f"截取音频失败: {detail}")


def user_voice_clone_prefix(user_id: int) -> str:
    """用户隔离前缀：voice-clone/{user_id}/"""
    return f"voice-clone/{int(user_id)}/"


def prepare_audio_for_transcribe(
    audio_url: str,
    *,
    user_id: int,
    max_sec: float = TRANSCRIBE_MAX_SEC,
) -> tuple[str, bool, float | None]:
    """下载参考音；超过 max_sec 则截取前段并上传到该用户目录。

    Returns:
        (asr_audio_url, truncated, duration_sec)
    """
    url = (audio_url or "").strip()
    if not url:
        raise ValueError("音频 URL 为空")

    with tempfile.TemporaryDirectory(prefix="vc-trim-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / "src.mp3"
        try:
            resp = requests.get(url, timeout=120)
        except requests.RequestException as e:
            raise RuntimeError(f"下载参考音失败: {e}") from e
        if resp.status_code >= 400 or not resp.content:
            raise RuntimeError(f"下载参考音失败（HTTP {resp.status_code}）")
        src.write_bytes(resp.content)

        duration = probe_duration_sec(src)
        if duration is not None and duration <= max_sec + 0.05:
            return url, False, duration

        # 探测失败时仍截一段，避免超长音频整段送 ASR
        need_trim = duration is None or duration > max_sec + 0.05
        if not need_trim:
            return url, False, duration

        dst = tmp_dir / "trim.mp3"
        trim_mp3_first_seconds(src, dst, max_sec=max_sec)
        object_key = (
            f"{user_voice_clone_prefix(user_id)}"
            f"trim/{uuid.uuid4().hex[:16]}_{int(max_sec)}s.mp3"
        )
        put_object(object_key, dst.read_bytes(), "audio/mpeg")
        trimmed_url = build_file_url(object_key)
        logger.info(
            "voice-clone trim user=%s duration=%s max=%s -> %s",
            user_id,
            duration,
            max_sec,
            object_key,
        )
        return trimmed_url, True, duration
