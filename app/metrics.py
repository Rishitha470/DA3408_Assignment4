"""
All Prometheus metrics for the captioning app live here so the names are
easy to find and so they only get registered once per Python process.

Naming follows the usual convention: captioning_<thing>_<unit>.
(prometheus_client adds the _total suffix to counters by itself.)
"""
import os

import psutil
from prometheus_client import Counter, Gauge, Histogram, Info, Summary, start_http_server

MODES = ("single", "bulk")
STATUSES = ("success", "error")

# --------------------------------------------------------------------------
# Buckets. Picked after timing BLIP-base on a laptop CPU: roughly 1-3 s per
# image, so I wanted a lot of resolution between 0.5 s and 10 s.
# --------------------------------------------------------------------------
INFERENCE_BUCKETS = (0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 12, 20, 30, 60)
REQUEST_BUCKETS = (0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600)
UPLOAD_BUCKETS = (1e4, 1e5, 5e5, 1e6, 5e6, 1e7, 5e7, 1e8, 2e8)
BATCH_BUCKETS = (1, 5, 10, 20, 50, 100, 200)
WAIT_BUCKETS = (0.01, 0.1, 0.5, 1, 2, 5, 10, 30, 60)

# --------------------------------------------------------------------------
# Counters
# --------------------------------------------------------------------------
REQUESTS = Counter(
    "captioning_requests",
    "Upload requests received (one per button click)",
    ["mode", "client_ip", "session_id"],
)
IMAGES = Counter(
    "captioning_images_processed",
    "Images pushed through the model",
    ["mode", "status"],
)
CLIENT_IMAGES = Counter(
    "captioning_client_images",
    "Images processed, broken down by who asked",
    ["mode", "client_ip", "browser"],
)
ERRORS = Counter(
    "captioning_errors",
    "Errors raised while handling uploads",
    ["mode", "error_type"],
)
UPLOAD_BYTES = Counter(
    "captioning_upload_bytes",
    "Bytes uploaded by users",
    ["mode"],
)
ZIP_SKIPPED = Counter(
    "captioning_zip_entries_skipped",
    "Entries in a ZIP that were ignored",
    ["reason"],
)

# --------------------------------------------------------------------------
# Gauges
# --------------------------------------------------------------------------
ACTIVE = Gauge(
    "captioning_active_requests",
    "Requests being handled right now",
    ["mode"],
)
QUEUE_WAITING = Gauge(
    "captioning_inference_queue_waiting",
    "Images waiting for the model lock",
)
MODEL_LOADED = Gauge("captioning_model_loaded", "1 once the model is in memory")
MODEL_LOAD_SECONDS = Gauge("captioning_model_load_seconds", "How long the model took to load")
MODEL_PARAM_BYTES = Gauge(
    "captioning_model_param_bytes", "Size of the model weights in memory (bytes)"
)
LAST_BATCH_SIZE = Gauge("captioning_last_batch_size", "Images in the most recent ZIP")
BATCH_PROGRESS = Gauge(
    "captioning_bulk_progress_ratio", "Progress of the ZIP being processed (0 to 1)"
)
PROCESS_RSS = Gauge(
    "captioning_process_resident_memory_bytes", "Resident memory of the Streamlit process"
)
PROCESS_CPU = Gauge(
    "captioning_process_cpu_percent",
    "CPU used by this process, as a percentage of the whole machine",
)

# --------------------------------------------------------------------------
# Histograms
# --------------------------------------------------------------------------
INFERENCE_SECONDS = Histogram(
    "captioning_inference_seconds",
    "Model time for one image (generate() only)",
    ["mode"],
    buckets=INFERENCE_BUCKETS,
)
REQUEST_SECONDS = Histogram(
    "captioning_request_seconds",
    "End to end time for one upload request",
    ["mode"],
    buckets=REQUEST_BUCKETS,
)
UPLOAD_SIZE = Histogram(
    "captioning_upload_size_bytes",
    "Size of each uploaded file",
    ["mode"],
    buckets=UPLOAD_BUCKETS,
)
BATCH_SIZE = Histogram(
    "captioning_bulk_batch_size",
    "Number of images per ZIP",
    buckets=BATCH_BUCKETS,
)
LOCK_WAIT = Histogram(
    "captioning_lock_wait_seconds",
    "Time an image spent waiting for the model lock",
    buckets=WAIT_BUCKETS,
)

# --------------------------------------------------------------------------
# Summaries (these give us the _sum and _count pair; the python client does
# not do quantiles, which is why the latency percentiles come from the
# histogram instead)
# --------------------------------------------------------------------------
REQUEST_SUMMARY = Summary(
    "captioning_request_duration_seconds",
    "End to end time per request, as a summary",
    ["mode"],
)
CAPTION_CHARS = Summary(
    "captioning_caption_chars",
    "Length of generated captions in characters",
    ["mode"],
)
IMAGE_MEGAPIXELS = Summary(
    "captioning_image_megapixels",
    "Resolution of the uploaded images in megapixels",
    ["mode"],
)

APP_INFO = Info("captioning_app", "Static facts about this deployment")

# --------------------------------------------------------------------------
# Process level gauges are computed on scrape, so they are always fresh.
# --------------------------------------------------------------------------
_proc = psutil.Process()
_proc.cpu_percent(None)  # the first call always returns 0, so prime it
_n_cpu = psutil.cpu_count() or 1

PROCESS_RSS.set_function(lambda: _proc.memory_info().rss)
PROCESS_CPU.set_function(lambda: _proc.cpu_percent(None) / _n_cpu)


def _init_series():
    """Create every label combination up front.

    If a series only appears when it is first incremented, rate() misses that
    first increment and the dashboard under-reports by one.
    """
    for mode in MODES:
        ACTIVE.labels(mode).set(0)
        for status in STATUSES:
            IMAGES.labels(mode, status).inc(0)
        UPLOAD_BYTES.labels(mode).inc(0)
        INFERENCE_SECONDS.labels(mode)
        REQUEST_SECONDS.labels(mode)
        UPLOAD_SIZE.labels(mode)
        REQUEST_SUMMARY.labels(mode)
        CAPTION_CHARS.labels(mode)
        IMAGE_MEGAPIXELS.labels(mode)
    for reason in ("hidden", "unsupported_type", "too_large", "zip_budget", "over_limit"):
        ZIP_SKIPPED.labels(reason).inc(0)
    QUEUE_WAITING.set(0)
    MODEL_LOADED.set(0)
    BATCH_PROGRESS.set(0)
    LAST_BATCH_SIZE.set(0)


_init_series()
APP_INFO.info(
    {
        "model": os.getenv("MODEL_NAME", "Salesforce/blip-image-captioning-base"),
        "device": "cpu",
        "version": "1.0.0",
    }
)


_started = False


def start_metrics_server(port: int) -> bool:
    """Expose /metrics on its own port. Safe to call more than once.

    Returns False only if the port is taken by some other process.
    """
    global _started
    if _started:
        return True
    try:
        start_http_server(port)
        _started = True
        return True
    except OSError:
        return False
