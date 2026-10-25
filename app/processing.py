"""
Image handling + metric recording. No Streamlit imports in here so it can be
tested on its own.
"""
import io
import os
import threading
import time
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from PIL import Image

import metrics as m

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_IMAGES_PER_ZIP = 200
MAX_ENTRY_BYTES = 15 * 1024 * 1024        # per image inside a zip
MAX_TOTAL_BYTES = 500 * 1024 * 1024       # whole zip, uncompressed
MAX_SIDE = 1024                           # downscale huge photos before the model sees them

# The model is not happy being hit from several threads at once on a small CPU,
# so inference is serialised. Waiting time is tracked by LOCK_WAIT.
_infer_lock = threading.Lock()


@dataclass
class Client:
    ip: str = "unknown"
    session_id: str = "unknown"
    browser: str = "other"


@dataclass
class Result:
    name: str
    caption: str
    seconds: float
    status: str
    error: str = ""


def _caption_one(captioner, name: str, raw: bytes, mode: str, client: Client) -> Result:
    started = time.perf_counter()
    try:
        img = Image.open(io.BytesIO(raw))
        width, height = img.size
        m.IMAGE_MEGAPIXELS.labels(mode).observe(width * height / 1e6)
        img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))

        queued_at = time.perf_counter()
        m.QUEUE_WAITING.inc()
        with _infer_lock:
            m.QUEUE_WAITING.dec()
            m.LOCK_WAIT.observe(time.perf_counter() - queued_at)
            t0 = time.perf_counter()
            caption = captioner.caption(img)
            infer_time = time.perf_counter() - t0

        m.INFERENCE_SECONDS.labels(mode).observe(infer_time)
        m.CAPTION_CHARS.labels(mode).observe(len(caption))
        m.IMAGES.labels(mode, "success").inc()
        m.CLIENT_IMAGES.labels(mode, client.ip, client.browser).inc()
        return Result(name, caption, time.perf_counter() - started, "success")
    except Exception as exc:  # a bad file should never take the app down
        m.IMAGES.labels(mode, "error").inc()
        m.ERRORS.labels(mode, type(exc).__name__).inc()
        return Result(name, "", time.perf_counter() - started, "error", f"{type(exc).__name__}: {exc}")


def _start_request(mode: str, client: Client, nbytes: int):
    m.REQUESTS.labels(mode, client.ip, client.session_id).inc()
    m.UPLOAD_BYTES.labels(mode).inc(nbytes)
    m.UPLOAD_SIZE.labels(mode).observe(nbytes)


def _finish_request(mode: str, started: float):
    elapsed = time.perf_counter() - started
    m.REQUEST_SECONDS.labels(mode).observe(elapsed)
    m.REQUEST_SUMMARY.labels(mode).observe(elapsed)


def caption_single(captioner, name: str, raw: bytes, client: Client) -> Result:
    _start_request("single", client, len(raw))
    started = time.perf_counter()
    with m.ACTIVE.labels("single").track_inprogress():
        try:
            return _caption_one(captioner, name, raw, "single", client)
        finally:
            _finish_request("single", started)


def caption_zip(
    captioner,
    raw_zip: bytes,
    client: Client,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[List[Result], List[Tuple[str, str]]]:
    """Caption every image in a ZIP.

    Returns (results, skipped) where skipped is a list of (filename, reason).
    Raises ValueError if the upload is not a usable ZIP.
    """
    _start_request("bulk", client, len(raw_zip))
    started = time.perf_counter()
    results: List[Result] = []
    skipped: List[Tuple[str, str]] = []

    with m.ACTIVE.labels("bulk").track_inprogress():
        try:
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw_zip))
            except zipfile.BadZipFile:
                m.ERRORS.labels("bulk", "BadZipFile").inc()
                raise ValueError("That file is not a valid ZIP archive.")

            entries = []
            total = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base = os.path.basename(info.filename)
                ext = os.path.splitext(base)[1].lower()

                reason = None
                if info.filename.startswith("__MACOSX") or base.startswith("."):
                    reason = "hidden"
                elif ext not in ALLOWED_EXT:
                    reason = "unsupported_type"
                elif info.file_size > MAX_ENTRY_BYTES:
                    reason = "too_large"
                elif total + info.file_size > MAX_TOTAL_BYTES:
                    reason = "zip_budget"
                elif len(entries) >= MAX_IMAGES_PER_ZIP:
                    reason = "over_limit"

                if reason:
                    m.ZIP_SKIPPED.labels(reason).inc()
                    skipped.append((info.filename, reason))
                else:
                    entries.append(info)
                    total += info.file_size

            m.LAST_BATCH_SIZE.set(len(entries))
            m.BATCH_SIZE.observe(len(entries))
            m.BATCH_PROGRESS.set(0)

            for i, info in enumerate(entries, start=1):
                # read at most limit+1 bytes so a lying header can't blow up memory
                with zf.open(info) as fh:
                    raw = fh.read(MAX_ENTRY_BYTES + 1)
                if len(raw) > MAX_ENTRY_BYTES:
                    m.ZIP_SKIPPED.labels("too_large").inc()
                    skipped.append((info.filename, "too_large"))
                else:
                    results.append(_caption_one(captioner, os.path.basename(info.filename), raw, "bulk", client))
                m.BATCH_PROGRESS.set(i / len(entries))
                if on_progress:
                    on_progress(i, len(entries))
            return results, skipped
        finally:
            m.BATCH_PROGRESS.set(0)
            _finish_request("bulk", started)
