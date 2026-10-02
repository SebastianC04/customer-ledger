"""
Entry point for the packaged desktop app.

Starts the FastAPI/Uvicorn server on a background thread, then opens a
native window (via pywebview) pointed at it — so the person using the app
sees a normal desktop application window: no browser address bar, no
terminal, nothing to type.

This is the file PyInstaller bundles into CustomerLedger.exe (see
build_installer/build.bat).
"""
import os
import sys
import threading
import time

# Make sure sibling modules (main.py, models.py, etc.) are importable
# regardless of the working directory the exe is launched from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
import webview

import main as app_module  # the FastAPI app instance defined in main.py

HOST = "127.0.0.1"
PORT = 8000


def run_server():
    uvicorn.run(app_module.app, host=HOST, port=PORT, log_level="warning")


def wait_for_server(url: str, timeout: float = 15.0):
    import urllib.request
    import urllib.error

    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    return False


if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    url = f"http://{HOST}:{PORT}"
    wait_for_server(url)

    webview.create_window(
        "TARJETAS",
        url,
        width=1280,
        height=850,
        min_size=(900, 600),
    )
    webview.start()