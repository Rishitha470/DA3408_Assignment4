"""
Start the app:  python run.py

Why not just `streamlit run app/app.py`? Streamlit only executes the script when
a browser tab connects, so /metrics would not exist until someone opened the
page and Prometheus would show the app as DOWN. This launcher opens the metrics
port first and then hands over to Streamlit.
"""
import os
import sys

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
sys.path.insert(0, APP_DIR)

import metrics  # noqa: E402

PORT = int(os.getenv("METRICS_PORT", "8000"))

if __name__ == "__main__":
    if not metrics.start_metrics_server(PORT):
        sys.exit(f"Port {PORT} is already in use - is another copy of the app running?")
    print(f"[run.py] metrics on http://localhost:{PORT}/metrics")

    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", os.path.join(APP_DIR, "app.py"),
        "--server.port", os.getenv("APP_PORT", "8501"),
        "--browser.gatherUsageStats", "false",
        *sys.argv[1:],
    ]
    sys.exit(stcli.main())
