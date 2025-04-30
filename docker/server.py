#!/usr/bin/env python3
import os
import subprocess
import threading
import time
import logging
from flask import Flask, send_from_directory, Response, abort

# --- Configuration ---
HLS_OUTPUT_DIR = "/hls-web"
STREAM_FILE = "stream.m3u8"
INACTIVITY_TIMEOUT = 60  # Seconds before stopping ffmpeg due to inactivity
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8007))
INPUT_URL = os.environ.get('INPUT_URL', '') # Will be set by entrypoint.sh
HLS_TIME = os.environ.get('HLS_TIME', '4')
HLS_LIST_SIZE = os.environ.get('HLS_LIST_SIZE', '5')
HLS_FLAGS = os.environ.get('HLS_FLAGS', 'delete_segments')
OUTPUT_M3U8_PATH = os.path.join(HLS_OUTPUT_DIR, STREAM_FILE)

# --- Global State ---
ffmpeg_process = None
last_request_time = 0
ffmpeg_lock = threading.Lock()
stop_event = threading.Event()

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Flask App ---
app = Flask(__name__)

# --- FFmpeg Management ---
def start_ffmpeg():
    global ffmpeg_process
    with ffmpeg_lock:
        if ffmpeg_process is None or ffmpeg_process.poll() is not None:
            if not INPUT_URL:
                logging.error("INPUT_URL is not set. Cannot start ffmpeg.")
                return False

            # Clean up old segments before starting
            try:
                for f in os.listdir(HLS_OUTPUT_DIR):
                    if f.endswith(".ts") or f == STREAM_FILE:
                        os.remove(os.path.join(HLS_OUTPUT_DIR, f))
                logging.info("Cleaned old HLS segments.")
            except OSError as e:
                logging.warning(f"Error cleaning HLS directory: {e}")


            ffmpeg_cmd = [
                "ffmpeg",
                "-i", INPUT_URL,
                "-c", "copy",
                "-f", "hls",
                "-hls_time", HLS_TIME,
                "-hls_list_size", HLS_LIST_SIZE,
                "-hls_flags", HLS_FLAGS,
                OUTPUT_M3U8_PATH
            ]
            logging.info(f"Starting ffmpeg command: {' '.join(ffmpeg_cmd)}")
            # Use preexec_fn=os.setsid to create a process group
            # This allows killing ffmpeg and potential child processes it might spawn
            ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)
            logging.info(f"FFmpeg started with PID: {ffmpeg_process.pid}")
            # Small delay to allow ffmpeg to create initial files
            time.sleep(2)
            return True
        else:
            logging.info("FFmpeg is already running.")
            return True # Already running, considered success
    return False

def stop_ffmpeg():
    global ffmpeg_process
    with ffmpeg_lock:
        if ffmpeg_process and ffmpeg_process.poll() is None:
            logging.info(f"Stopping ffmpeg process group (PGID: {os.getpgid(ffmpeg_process.pid)})...")
            try:
                # Send SIGTERM to the entire process group
                os.killpg(os.getpgid(ffmpeg_process.pid), signal.SIGTERM)
                ffmpeg_process.wait(timeout=10) # Wait for graceful shutdown
                logging.info("FFmpeg process terminated.")
            except ProcessLookupError:
                 logging.warning("FFmpeg process group already gone.")
            except subprocess.TimeoutExpired:
                 logging.warning("FFmpeg did not terminate gracefully after 10s, sending SIGKILL.")
                 try:
                     os.killpg(os.getpgid(ffmpeg_process.pid), signal.SIGKILL)
                     logging.info("FFmpeg process group killed.")
                 except Exception as e:
                     logging.error(f"Error sending SIGKILL to ffmpeg process group: {e}")
            except Exception as e:
                logging.error(f"Error stopping ffmpeg process group: {e}")
            finally:
                 ffmpeg_process = None


def check_activity():
    """Runs in a background thread to stop ffmpeg if inactive."""
    global ffmpeg_process
    while not stop_event.is_set():
        with ffmpeg_lock:
            if ffmpeg_process and ffmpeg_process.poll() is None:
                if time.time() - last_request_time > INACTIVITY_TIMEOUT:
                    logging.info("Inactivity detected.")
                    stop_ffmpeg()
            elif ffmpeg_process and ffmpeg_process.poll() is not None:
                 # FFmpeg died on its own
                 logging.warning(f"Detected FFmpeg process died unexpectedly (Exit code: {ffmpeg_process.poll()}). Cleaning up.")
                 stderr_output = ""
                 try:
                     stderr_output = ffmpeg_process.stderr.read().decode('utf-8', errors='ignore')
                 except Exception:
                     pass # Ignore errors reading stderr if process is gone
                 logging.warning(f"FFmpeg stderr: {stderr_output.strip()}")
                 ffmpeg_process = None # Mark as stopped

        # Check every 5 seconds
        stop_event.wait(5)


# --- Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    logging.info("Request for index.html")
    # Treat access to index as activity
    update_last_request_time()
    # Attempt to start ffmpeg if not running when index is accessed
    start_ffmpeg()
    return send_from_directory(HLS_OUTPUT_DIR, 'index.html')

@app.route('/hls.js@latest')
def hls_js():
    """Serves the hls.js library."""
    logging.info("Request for hls.js")
    return send_from_directory(HLS_OUTPUT_DIR, 'hls.js@latest')

@app.route('/<path:filename>')
def hls_files(filename):
    """Serves HLS manifest and segments."""
    if filename == STREAM_FILE or filename.endswith(".ts"):
        logging.info(f"Request for HLS file: {filename}")
        update_last_request_time()
        if not start_ffmpeg(): # Start ffmpeg if not running
             if ffmpeg_process is None: # Check if start_ffmpeg failed because INPUT_URL missing
                 return "Error: Stream input URL not configured.", 500
             # else: ffmpeg is running or starting, proceed to serve

        # Wait a moment for the file to appear if ffmpeg just started
        file_path = os.path.join(HLS_OUTPUT_DIR, filename)
        max_wait = 5 # seconds
        wait_interval = 0.5
        waited = 0
        while not os.path.exists(file_path) and waited < max_wait:
            logging.warning(f"File {filename} not found, waiting...")
            time.sleep(wait_interval)
            waited += wait_interval
            # Check if ffmpeg died while we were waiting
            with ffmpeg_lock:
                if ffmpeg_process is None or ffmpeg_process.poll() is not None:
                    logging.error("FFmpeg process is not running while waiting for file.")
                    abort(503) # Service Unavailable


        if not os.path.exists(file_path):
             logging.error(f"File {filename} not found after waiting.")
             abort(404)

        return send_from_directory(HLS_OUTPUT_DIR, filename)
    else:
        logging.warning(f"Denying request for non-HLS/index file: {filename}")
        abort(404)

def update_last_request_time():
    """Updates the timestamp of the last HLS request."""
    global last_request_time
    last_request_time = time.time()

# --- Main Execution ---
if __name__ == '__main__':
    # Ensure INPUT_URL is available
    if not INPUT_URL:
        logging.error("FATAL: INPUT_URL environment variable not set.")
        exit(1)

    logging.info(f"Input URL configured: {INPUT_URL}")
    logging.info(f"HLS Output Dir: {HLS_OUTPUT_DIR}")
    logging.info(f"Inactivity Timeout: {INACTIVITY_TIMEOUT}s")

    # Start the background activity checker thread
    checker_thread = threading.Thread(target=check_activity, daemon=True)
    checker_thread.start()

    # Start Flask server
    logging.info(f"Starting Flask server on port {SERVER_PORT}")
    try:
        app.run(host='0.0.0.0', port=SERVER_PORT, threaded=True)
    finally:
        logging.info("Flask server shutting down.")
        stop_event.set() # Signal checker thread to stop
        stop_ffmpeg() # Ensure ffmpeg is stopped on exit
        checker_thread.join(timeout=2) # Wait briefly for checker thread
        logging.info("Cleanup complete.")
