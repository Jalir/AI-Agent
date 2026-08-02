"""长音频静音切段：优先在静音处切开，避免打断说话。"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

from backend.common.audio_trim import probe_duration_sec
from backend.common.oss import build_file_url, delete_object, put_object

logger = logging.getLogger(__name__)

# 短于此时长（秒）不切段，整段识别
SHORT_AUDIO_SEC = 120.0
# 目标段长 / 硬上限 / 最短段（强制切点前至少保留这么长）
TARGET_SEG_SEC = 90.0
MAX_SEG_SEC = 150.0
MIN_SEG_SEC = 25.0
# 仅在找不到静音、被迫硬切时，前后各重叠一点，降低断句丢字
FORCED_OVERLAP_SEC = 0.35

_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


@dataclass(frozen=True)
class AudioSegment:
    index: int
    start_sec: float
    end_sec: float
    url: str
    object_key: str
    forced_cut: bool = False


def user_transcribe_prefix(user_id: int) -> str:
    return f"transcribe/{int(user_id)}/"


def detect_silence_intervals(
    path: Path,
    *,
    noise_db: float = -32.0,
    min_silence_sec: float = 0.35,
) -> list[tuple[float, float]]:
    """ffmpeg silencedetect，返回 [(start, end), ...]。"""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, check=False
        )
    except FileNotFoundError as e:
        raise RuntimeError("未安装 ffmpeg，无法对长音频分段") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("分析静音超时，请稍后重试") from e

    log = (proc.stderr or "") + "\n" + (proc.stdout or "")
    intervals: list[tuple[float, float]] = []
    start: float | None = None
    for line in log.splitlines():
        m_start = _SILENCE_START_RE.search(line)
        if m_start:
            start = float(m_start.group(1))
            continue
        m_end = _SILENCE_END_RE.search(line)
        if m_end and start is not None:
            end = float(m_end.group(1))
            if end > start:
                intervals.append((start, end))
            start = None
    return intervals


def plan_cut_points(
    duration: float,
    silences: list[tuple[float, float]],
    *,
    target_sec: float = TARGET_SEG_SEC,
    max_sec: float = MAX_SEG_SEC,
    min_sec: float = MIN_SEG_SEC,
) -> list[tuple[float, bool]]:
    """规划切点（不含 0，含 duration）。

    返回 [(cut_at, forced), ...]，forced=True 表示该切点不是静音处、被迫硬切。
    """
    if duration <= 0:
        return [(0.0, False)]

    cuts: list[tuple[float, bool]] = []
    cursor = 0.0
    while cursor < duration - 0.05:
        remain = duration - cursor
        if remain <= max_sec + 0.05:
            cuts.append((duration, False))
            break

        target = cursor + target_sec
        hard = min(cursor + max_sec, duration)
        window_lo = cursor + min_sec
        window_hi = hard

        best_mid: float | None = None
        best_dist = float("inf")
        for s0, s1 in silences:
            mid = (s0 + s1) / 2.0
            if mid < window_lo or mid > window_hi:
                continue
            dist = abs(mid - target)
            # 略偏向更靠近目标、且静音更长的点
            score = dist - min(0.8, (s1 - s0) * 0.15)
            if score < best_dist:
                best_dist = score
                best_mid = mid

        if best_mid is not None:
            cuts.append((best_mid, False))
            cursor = best_mid
        else:
            cuts.append((hard, True))
            cursor = hard

    return cuts


def extract_segment_mp3(
    src: Path,
    dst: Path,
    *,
    start_sec: float,
    end_sec: float,
) -> None:
    """按时间截取一段 mp3（重编码，切点更准）。"""
    dur = max(0.05, end_sec - start_sec)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(src),
        "-t",
        f"{dur:.3f}",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
    except FileNotFoundError as e:
        raise RuntimeError("未安装 ffmpeg，无法截取音频片段") from e
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size <= 0:
        detail = (proc.stderr or "")[:400]
        raise RuntimeError(f"截取音频片段失败: {detail}")


def download_audio(url: str, dst: Path, *, timeout: float = 180.0) -> None:
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"下载音频失败: {e}") from e
    if resp.status_code >= 400 or not resp.content:
        raise RuntimeError(f"下载音频失败（HTTP {resp.status_code}）")
    dst.write_bytes(resp.content)


def prepare_transcribe_segments(
    audio_url: str,
    *,
    user_id: int,
) -> tuple[float | None, list[AudioSegment], list[str]]:
    """下载音频并切段上传。

    Returns:
        (duration_sec, segments, object_keys_to_cleanup)
        短音频返回单段，url 仍为原 audio_url（不产生临时对象）。
    """
    url = (audio_url or "").strip()
    if not url:
        raise ValueError("音频 URL 为空")

    cleanup_keys: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ta-seg-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / "src.mp3"
        download_audio(url, src)

        duration = probe_duration_sec(src)
        if duration is None:
            # 探测失败：不切段，整段送 ASR
            logger.warning("probe duration failed, single-shot ASR url=%s", url[:80])
            return None, [
                AudioSegment(
                    index=0, start_sec=0.0, end_sec=0.0, url=url, object_key=""
                )
            ], cleanup_keys

        if duration <= SHORT_AUDIO_SEC + 0.05:
            return duration, [
                AudioSegment(
                    index=0,
                    start_sec=0.0,
                    end_sec=duration,
                    url=url,
                    object_key="",
                )
            ], cleanup_keys

        try:
            silences = detect_silence_intervals(src)
        except RuntimeError:
            logger.exception("silence detect failed, fallback fixed cuts")
            silences = []

        cut_plan = plan_cut_points(duration, silences)
        ranges: list[tuple[float, float, bool]] = []
        prev = 0.0
        prev_forced = False
        for cut_at, forced in cut_plan:
            start = prev
            end = cut_at
            # 上一切点若是硬切：本段起点略回退，降低断句丢字
            if prev_forced and start > 0:
                start = max(0.0, start - FORCED_OVERLAP_SEC)
            # 本段结束若是硬切：终点略延长
            if forced and end < duration:
                end = min(duration, end + FORCED_OVERLAP_SEC)
            if end - start < 0.2:
                prev = cut_at
                prev_forced = forced
                continue
            ranges.append((start, end, forced or prev_forced))
            prev = cut_at
            prev_forced = forced

        if not ranges:
            ranges = [(0.0, duration, False)]

        batch = uuid.uuid4().hex[:12]
        segments: list[AudioSegment] = []
        for idx, (start, end, forced) in enumerate(ranges):
            if len(ranges) == 1 and abs(start) < 0.01 and abs(end - duration) < 0.05:
                segments.append(
                    AudioSegment(
                        index=0,
                        start_sec=0.0,
                        end_sec=duration,
                        url=url,
                        object_key="",
                        forced_cut=False,
                    )
                )
                break
            part = tmp_dir / f"part_{idx:03d}.mp3"
            extract_segment_mp3(src, part, start_sec=start, end_sec=end)
            object_key = (
                f"{user_transcribe_prefix(user_id)}"
                f"segments/{batch}/p{idx:03d}_{int(start)}-{int(end)}.mp3"
            )
            put_object(object_key, part.read_bytes(), "audio/mpeg")
            cleanup_keys.append(object_key)
            segments.append(
                AudioSegment(
                    index=idx,
                    start_sec=start,
                    end_sec=end,
                    url=build_file_url(object_key),
                    object_key=object_key,
                    forced_cut=forced,
                )
            )

        logger.info(
            "transcribe segments user=%s duration=%.1f parts=%d silences=%d",
            user_id,
            duration,
            len(segments),
            len(silences),
        )
        return duration, segments, cleanup_keys


def cleanup_segment_objects(keys: list[str]) -> None:
    for key in keys:
        k = (key or "").strip()
        if not k:
            continue
        try:
            delete_object(k)
        except Exception:
            logger.exception("cleanup segment oss failed: %s", k)
