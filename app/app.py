import os
import time

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

import captioner as cap
import metrics
import processing

METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))
SINGLE_TYPES = ["jpg", "jpeg", "png", "webp", "bmp"]

st.set_page_config(page_title="Image Captioning", page_icon=None, layout="wide")


@st.cache_resource
def start_metrics(port: int) -> bool:
    return metrics.start_metrics_server(port)


@st.cache_resource(show_spinner="Loading the captioning model (first run downloads ~1 GB)...")
def get_captioner():
    t0 = time.perf_counter()
    model = cap.load_captioner()
    metrics.MODEL_LOAD_SECONDS.set(time.perf_counter() - t0)
    metrics.MODEL_PARAM_BYTES.set(model.param_bytes)
    metrics.MODEL_LOADED.set(1)
    return model


def guess_browser(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    # order matters: Chrome's UA also contains "safari", Edge's contains "chrome"
    for needle, label in (("edg/", "edge"), ("firefox", "firefox"), ("chrome", "chrome"), ("safari", "safari")):
        if needle in ua:
            return label
    return "other"


def get_client() -> processing.Client:
    ip, browser, session = "unknown", "other", "unknown"
    try:
        ip = getattr(st.context, "ip_address", None) or "local"
        browser = guess_browser(st.context.headers.get("User-Agent", ""))
    except Exception:
        pass
    ctx = get_script_run_ctx()
    if ctx is not None:
        session = ctx.session_id[:8]  # short id keeps the label readable
    return processing.Client(ip=ip, session_id=session, browser=browser)


metrics_ok = start_metrics(METRICS_PORT)
captioner = get_captioner()
client = get_client()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Status")
    if metrics_ok:
        st.success(f"Metrics on port {METRICS_PORT}")
    else:
        st.warning(f"Port {METRICS_PORT} was already in use. Is another copy running?")
    st.caption(f"Session: {client.session_id}")
    st.caption(f"Model: {os.getenv('MODEL_NAME', 'Salesforce/blip-image-captioning-base')}")
    st.caption(f"Zip limits: {processing.MAX_IMAGES_PER_ZIP} images, {processing.MAX_ENTRY_BYTES // 2**20} MB each")

st.title("Image Captioning (CPU)")
st.write("Upload one picture, or a ZIP of pictures, and the model will describe them.")

single_tab, bulk_tab = st.tabs(["Single image", "Bulk (ZIP)"])

# ---------------------------------------------------------------- single
with single_tab:
    upload = st.file_uploader("Choose an image", type=SINGLE_TYPES, key="single_upload")
    if upload is not None:
        left, right = st.columns([1, 1])
        with left:
            st.image(upload.getvalue(), width=420)
        with right:
            if st.button("Generate caption", key="single_go", type="primary"):
                with st.spinner("Running the model on CPU..."):
                    st.session_state["single_result"] = processing.caption_single(
                        captioner, upload.name, upload.getvalue(), client
                    )
            res = st.session_state.get("single_result")
            if res is not None:
                if res.status == "success":
                    st.subheader(res.caption)
                    st.caption(f"{res.name} - {res.seconds:.2f} s")
                else:
                    st.error(f"Could not caption {res.name}: {res.error}")

# ---------------------------------------------------------------- bulk
with bulk_tab:
    zip_upload = st.file_uploader("Choose a ZIP of images", type=["zip"], key="bulk_upload")
    if zip_upload is not None and st.button("Process ZIP", key="bulk_go", type="primary"):
        bar = st.progress(0.0, text="Starting...")

        def update(done: int, total: int):
            bar.progress(done / total, text=f"Captioned {done} of {total}")

        t0 = time.perf_counter()
        try:
            results, skipped = processing.caption_zip(captioner, zip_upload.getvalue(), client, update)
            st.session_state["bulk_result"] = (results, skipped, time.perf_counter() - t0)
        except ValueError as exc:
            st.session_state.pop("bulk_result", None)
            st.error(str(exc))
        bar.empty()

    bulk = st.session_state.get("bulk_result")
    if bulk is not None:
        results, skipped, elapsed = bulk
        ok = [r for r in results if r.status == "success"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Captioned", len(ok))
        c2.metric("Failed / skipped", (len(results) - len(ok)) + len(skipped))
        c3.metric("Total time", f"{elapsed:.1f} s")

        if results:
            df = pd.DataFrame(
                [
                    {"file": r.name, "caption": r.caption or r.error, "status": r.status, "seconds": round(r.seconds, 2)}
                    for r in results
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button("Download captions (CSV)", df.to_csv(index=False), "captions.csv", "text/csv")
        if skipped:
            with st.expander(f"{len(skipped)} entries were skipped"):
                st.table(pd.DataFrame(skipped, columns=["entry", "reason"]))
