"""ffmpeg helpers.

Ported from youtube-clone/src/lib/pipeline/media.ts.

Every spawn has a wall-clock timeout: a hung ffmpeg is killed and the call
raises, so the step fails fast and retries instead of hanging the worker
forever.

`source` may be a local path or a presigned HTTPS URL. Reading remotely is the
trick that keeps multi-GB videos off disk — ffmpeg seeks the slice it needs over
HTTP range requests, so per-step disk use is the small OUTPUT (a ~2MB audio
slice or a JPEG), never the source.
"""

import logging
import os
import re
import shutil
import subprocess
from functools import lru_cache

from app.pipeline.types import SILENCE_MIN_SECS, SILENCE_NOISE_DB, SilentWindow

log = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"Duration:\s+(\d+):(\d+):([\d.]+)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@lru_cache
def ffmpeg_bin() -> str:
    """Resolve the ffmpeg binary.

    FFMPEG_PATH wins as an escape hatch, then a system ffmpeg. The container
    installs the system build deliberately: it reads HTTPS sources reliably,
    which static builds do not.
    """
    override = os.environ.get("FFMPEG_PATH")
    if override:
        return override

    found = shutil.which("ffmpeg")
    if found:
        return found

    for candidate in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
        if os.path.exists(candidate):
            return candidate

    return "ffmpeg"  # let the spawn fail with a clear error


def is_url(source: str) -> bool:
    return bool(_URL_RE.match(source))


def reconnect_flags(source: str) -> list[str]:
    """Remote reads retry mid-stream instead of failing the whole call."""
    if not is_url(source):
        return []
    return ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]


def run_ffmpeg(args: list[str], timeout_secs: float) -> tuple[int, str]:
    """Run ffmpeg with a hard timeout.

    Returns (exit code, stderr). Raises only on timeout or spawn failure, so
    callers decide what a non-zero exit code means.
    """
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), *args],
            capture_output=True,
            timeout=timeout_secs,
        )
    except subprocess.TimeoutExpired as err:
        raise RuntimeError(
            f"ffmpeg timed out after {timeout_secs}s: {' '.join(args)[:80]}"
        ) from err
    return proc.returncode, proc.stderr.decode("utf-8", errors="replace")


# ─── parsing (pure) ───────────────────────────────────────────────────────────


def parse_duration(stderr: str) -> float:
    m = _DURATION_RE.search(stderr)
    if not m:
        return 0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def parse_silence_windows(stderr: str) -> list[SilentWindow]:
    windows: list[SilentWindow] = []
    pending: float | None = None
    for line in stderr.split("\n"):
        s = _SILENCE_START_RE.search(line)
        e = _SILENCE_END_RE.search(line)
        if s:
            pending = float(s.group(1))
        if e and pending is not None:
            windows.append({"start": pending, "end": float(e.group(1))})
            pending = None
    if pending is not None:
        windows.append({"start": pending, "end": None})
    return windows


# ─── operations ───────────────────────────────────────────────────────────────


def probe_duration(source: str) -> float:
    """Read the container's duration without decoding.

    `ffmpeg -i <file>` prints Duration to stderr and exits IMMEDIATELY with a
    non-zero code, since no output file was given. Do NOT add `-f null -`: that
    decodes the entire video, taking minutes on a long one, timing out, and
    returning 0 — which silently breaks the whole pipeline.
    """
    try:
        _, stderr = run_ffmpeg([*reconnect_flags(source), "-i", source], 60)
        return parse_duration(stderr)
    except Exception as err:
        log.warning("[media] probe failed for %s: %s", source, err)
        return 0


def extract_audio_slice(source: str, start: float, dur: float, output_path: str) -> None:
    """Extract [start, start+dur] as 16kHz mono 32k MP3, timestamps reset to 0."""
    code, stderr = run_ffmpeg(
        [
            "-y",
            *reconnect_flags(source),
            "-ss",
            str(start),
            "-t",
            str(dur),
            "-i",
            source,
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "32k",
            output_path,
        ],
        240,
    )
    if code != 0:
        raise RuntimeError(f"ffmpeg audio slice exit {code}: {stderr[-300:]}")


def extract_frame_at(source: str, time: float, output_path: str) -> None:
    code, stderr = run_ffmpeg(
        [
            "-y",
            *reconnect_flags(source),
            "-ss",
            str(time),
            "-i",
            source,
            "-vframes",
            "1",
            "-q:v",
            "2",
            output_path,
        ],
        60,
    )
    if code != 0:
        raise RuntimeError(f"ffmpeg frame exit {code}: {stderr[-300:]}")


def detect_silent_windows(audio_path: str) -> list[SilentWindow]:
    """Best-effort: a failure returns no windows rather than failing the video."""
    try:
        _, stderr = run_ffmpeg(
            [
                "-i",
                audio_path,
                "-af",
                f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECS}",
                "-f",
                "null",
                "-",
            ],
            180,
        )
    except Exception as err:
        log.warning("[media] silence detection failed for %s: %s", audio_path, err)
        return []
    return parse_silence_windows(stderr)
