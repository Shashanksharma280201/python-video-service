"""Proves ffmpeg reads remote video by RANGE, not by downloading it whole.

This is the load-bearing assumption of the whole pipeline. The Node service
never downloads the source: each step presigns a URL and ffmpeg seeks just the
slice it needs over HTTP range requests, so per-step disk use is the small
OUTPUT (a ~2MB audio slice or a JPEG) rather than a multi-GB source. An earlier
full-download approach overflowed the runtime's disk.

Nothing in the port guarantees this carries over — it depends on the ffmpeg
build honouring range requests — so it is verified here against a real HTTP
server that counts the bytes it actually hands out.

WHY THE ASSERTIONS ARE ABSOLUTE, NOT PERCENTAGES
------------------------------------------------
ffmpeg reads a bounded amount regardless of file size: roughly its probe buffer
(a few MB) plus the slice requested. Measured against this 20MB fixture:

    probe duration    ~2.6 MB
    frame at 5s      ~10.6 MB
    frame at 100s     ~5.4 MB
    audio slice       ~9.1 MB

As a PERCENTAGE of a small fixture those look alarming (12-49%), but the figure
that matters is the absolute ceiling — it stays flat as the source grows, which
is exactly why a 4GB video is safe. So these assert byte ceilings.

A first version of this test used a 1.9MB clip and asserted percentages. It
failed, and correctly so: ffmpeg's default probesize (5MB) exceeded the entire
file, so of course it read all of it. The lesson is that the fixture must be
comfortably larger than the probe buffer for the measurement to mean anything.
"""

import http.server
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from app.pipeline.media import extract_audio_slice, extract_frame_at, probe_duration

HAS_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")

MB = 1024 * 1024

# Ceilings, not percentages — see the module docstring. Generous enough to
# absorb ffmpeg version differences, tight enough that a full download of the
# ~20MB fixture (or any larger source) trips them.
PROBE_CEILING = 8 * MB
FRAME_CEILING = 15 * MB
SLICE_CEILING = 15 * MB


class CountingRangeHandler(http.server.BaseHTTPRequestHandler):
    """Minimal static server WITH range support, tallying bytes actually sent."""

    directory: Path
    bytes_served = 0

    def log_message(self, *args):  # silence per-request logging
        pass

    def _resolve(self) -> Path:
        return self.directory / self.path.lstrip("/")

    def do_HEAD(self):
        path = self._resolve()
        if not path.exists():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "video/mp4")
        self.end_headers()

    def do_GET(self):
        path = self._resolve()
        if not path.exists():
            self.send_error(404)
            return

        size = path.stat().st_size
        rng = self.headers.get("Range")

        if rng and rng.startswith("bytes="):
            spec = rng[len("bytes=") :].split(",")[0]
            start_s, _, end_s = spec.partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            end = min(end, size - 1)
            length = max(0, end - start + 1)

            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(length)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            data = path.read_bytes()

        # Write in chunks and count only what LANDS. ffmpeg closes the socket as
        # soon as it has what it needs; counting the whole intended body would
        # report a full download that never crossed the wire.
        chunk = 16 * 1024
        try:
            for i in range(0, len(data), chunk):
                piece = data[i : i + chunk]
                self.wfile.write(piece)
                self.wfile.flush()
                type(self).bytes_served += len(piece)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture(scope="module")
def big_clip(tmp_path_factory) -> Path:
    """A ~20MB / 120s clip — comfortably larger than ffmpeg's 5MB probe buffer.

    Below that threshold the measurement is meaningless: ffmpeg reads the whole
    file just to probe it.
    """
    out = tmp_path_factory.mktemp("range") / "big.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=30:duration=120",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=120",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-b:v",
            "4000k",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    assert out.stat().st_size > 10 * MB, "fixture too small for the measurement to mean anything"
    return out


@pytest.fixture
def server(big_clip):
    """Serve the clip on a random port, exposing a fresh byte tally per test."""
    handler = type(
        "Handler", (CountingRangeHandler,), {"directory": big_clip.parent, "bytes_served": 0}
    )

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/{big_clip.name}", handler, big_clip.stat().st_size
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_server_actually_serves_ranges(server):
    """Guards the test itself: a server ignoring Range would make every
    byte-count assertion below meaningless."""
    import urllib.request

    url, _handler, _size = server
    req = urllib.request.Request(url, headers={"Range": "bytes=0-99"})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 206
        assert len(resp.read()) == 100


def test_probing_duration_reads_only_the_container_header(server):
    url, handler, size = server
    assert probe_duration(url) == pytest.approx(120, abs=3)
    assert handler.bytes_served < PROBE_CEILING, (
        f"probe pulled {handler.bytes_served / MB:.1f}MB of a {size / MB:.1f}MB file — "
        "it is decoding or downloading rather than reading the header"
    )


def test_extracting_an_early_frame_is_bounded(server, tmp_path):
    url, handler, size = server
    out = tmp_path / "frame.jpg"
    extract_frame_at(url, 5.0, str(out))

    assert out.exists()
    assert out.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic
    assert handler.bytes_served < FRAME_CEILING, (
        f"frame extraction pulled {handler.bytes_served / MB:.1f}MB of {size / MB:.1f}MB"
    )


def test_seeking_late_in_the_file_does_not_stream_everything_before_it(server, tmp_path):
    """The real proof of range-seeking: a frame at 100s must not require
    reading the first 100s of video."""
    url, handler, size = server
    out = tmp_path / "frame.jpg"
    extract_frame_at(url, 100.0, str(out))

    assert out.exists()
    assert handler.bytes_served < FRAME_CEILING, (
        f"late seek pulled {handler.bytes_served / MB:.1f}MB of {size / MB:.1f}MB — "
        "ffmpeg is streaming from the start instead of seeking"
    )


def test_extracting_an_audio_slice_over_http_is_bounded(server, tmp_path):
    """The core pipeline operation, against a remote source."""
    url, handler, size = server
    out = tmp_path / "slice.mp3"
    extract_audio_slice(url, 60, 10, str(out))

    assert out.exists()
    assert probe_duration(str(out)) == pytest.approx(10, abs=1)
    assert handler.bytes_served < SLICE_CEILING, (
        f"audio slice pulled {handler.bytes_served / MB:.1f}MB of {size / MB:.1f}MB"
    )


def test_the_extracted_slice_is_far_smaller_than_the_source(server, tmp_path):
    """Disk footprint per step is the OUTPUT, not the source — this is what
    keeps multi-GB videos inside a small container."""
    url, _handler, size = server
    out = tmp_path / "slice.mp3"
    extract_audio_slice(url, 60, 10, str(out))
    assert out.stat().st_size < size / 10
