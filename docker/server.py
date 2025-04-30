#!/usr/bin/env python3
import os
import subprocess
import threading
import time
import logging
import signal
from threading import RLock
from flask import Flask, send_from_directory, Response, abort

# --- Configuration ---
HLS_OUTPUT_DIR = "/hls-web"
STREAM_FILE = "stream.m3u8"
INACTIVITY_TIMEOUT = 60
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8007))
INPUT_URL = os.environ.get('INPUT_URL', '')
HLS_TIME = os.environ.get('HLS_TIME', '4')
HLS_LIST_SIZE = os.environ.get('HLS_LIST_SIZE', '5')
HLS_FLAGS = os.environ.get('HLS_FLAGS', 'delete_segments')
OUTPUT_M3U8_PATH = os.path.join(HLS_OUTPUT_DIR, STREAM_FILE)

# --- Global State ---
ffmpeg_process = None
ffmpeg_stderr_thread = None
ffmpeg_stdout_thread = None
last_request_time = 0
ffmpeg_lock = RLock()
stop_event = threading.Event()

# --- Logging ---
# Add a specific logger for ffmpeg output
ffmpeg_logger = logging.getLogger('ffmpeg')
formatter = logging.Formatter('%(asctime)s - FFMPEG - %(levelname)s - %(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
ffmpeg_logger.addHandler(handler)
ffmpeg_logger.setLevel(logging.INFO)
ffmpeg_logger.propagate = False

# Main application logger - SET LEVEL TO DEBUG
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
app_logger = logging.getLogger(__name__) # Use default logger for app messages

# --- Flask App ---
app = Flask(__name__)

# --- FFmpeg Output Logging Thread ---
def log_stream(stream, logger_func):
    """Reads a stream line by line and logs it using the provided logger function."""
    try:
        for line in iter(stream.readline, ''):
            if not line: break
            logger_func(line.strip())
    except Exception as e:
        app_logger.error(f"Error reading ffmpeg stream: {e}", exc_info=False) # Keep concise
    finally:
        try: stream.close()
        except Exception: pass
        app_logger.debug("FFmpeg stream logging thread finished.") # Use Debug


# --- FFmpeg Management ---
# stop_ffmpeg function remains the same
def stop_ffmpeg():
    global ffmpeg_process, ffmpeg_stderr_thread, ffmpeg_stdout_thread
    with ffmpeg_lock:
        if ffmpeg_process:
            pid = ffmpeg_process.pid
            pgid = 0
            try:
                pgid = os.getpgid(pid)
            except ProcessLookupError:
                app_logger.warning(f"Process {pid} already gone before trying to get PGID. Clearing variable.")
                ffmpeg_process = None
                if ffmpeg_stderr_thread and ffmpeg_stderr_thread.is_alive(): ffmpeg_stderr_thread.join(timeout=0.1)
                if ffmpeg_stdout_thread and ffmpeg_stdout_thread.is_alive(): ffmpeg_stdout_thread.join(timeout=0.1)
                ffmpeg_stderr_thread = None
                ffmpeg_stdout_thread = None
                return

            if ffmpeg_process.poll() is None:
                app_logger.info(f"Attempting to stop ffmpeg process group (PID: {pid}, PGID: {pgid})...")
                try:
                    os.killpg(pgid, signal.SIGTERM)
                    ffmpeg_process.wait(timeout=10)
                    app_logger.info(f"FFmpeg process group (PID: {pid}) terminated gracefully.")
                except ProcessLookupError: app_logger.warning(f"FFmpeg process group (PID: {pid}) already gone during termination attempt.")
                except subprocess.TimeoutExpired:
                    app_logger.warning(f"FFmpeg (PID: {pid}) did not terminate gracefully after 10s, sending SIGKILL to PGID {pgid}.")
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                        time.sleep(0.1); app_logger.info(f"FFmpeg process group (PID: {pid}) killed.")
                    except ProcessLookupError: app_logger.warning(f"FFmpeg process group (PID: {pid}) already gone when sending SIGKILL.")
                    except Exception as e: app_logger.error(f"Error sending SIGKILL to ffmpeg process group (PID: {pid}, PGID: {pgid}): {e}")
                except Exception as e: app_logger.error(f"Error stopping ffmpeg process group (PID: {pid}, PGID: {pgid}): {e}")
            else:
                app_logger.warning(f"FFmpeg process (PID: {pid}) was already terminated (exit code {ffmpeg_process.poll()}) before stop command could run kill.")

            app_logger.info("Waiting for ffmpeg logging threads to finish...")
            if ffmpeg_stderr_thread and ffmpeg_stderr_thread.is_alive():
                ffmpeg_stderr_thread.join(timeout=2)
                if ffmpeg_stderr_thread.is_alive(): app_logger.warning("FFmpeg stderr logging thread did not finish.")
            if ffmpeg_stdout_thread and ffmpeg_stdout_thread.is_alive():
                ffmpeg_stdout_thread.join(timeout=1)
                if ffmpeg_stdout_thread.is_alive(): app_logger.warning("FFmpeg stdout logging thread did not finish.")

            app_logger.info(f"Setting ffmpeg_process to None after termination and thread join (PID: {pid}).")
            ffmpeg_process = None
            ffmpeg_stderr_thread = None
            ffmpeg_stdout_thread = None
        else:
            app_logger.info("Stop ffmpeg called, but process was already None.")


# start_ffmpeg function remains the same
def start_ffmpeg():
    global ffmpeg_process, ffmpeg_stderr_thread, ffmpeg_stdout_thread
    with ffmpeg_lock:
        if ffmpeg_process and ffmpeg_process.poll() is None:
             app_logger.debug("Start ffmpeg called, but FFmpeg process object exists and poll() is None (already running).") # Debug level
             return True

        app_logger.info("Start ffmpeg called: Process is None or poll() is not None. Attempting to start.")

        if not INPUT_URL:
            app_logger.error("INPUT_URL is not set. Cannot start ffmpeg.")
            return False

        try:
            cleaned_count = 0
            for f in os.listdir(HLS_OUTPUT_DIR):
                if f.endswith(".ts") or f == STREAM_FILE:
                    file_path = os.path.join(HLS_OUTPUT_DIR, f)
                    if os.path.exists(file_path): os.remove(file_path); cleaned_count += 1
            if cleaned_count > 0: app_logger.info(f"Cleaned {cleaned_count} old HLS file(s).")
            else: app_logger.debug("No old HLS files found to clean.") # Debug level
        except OSError as e: app_logger.warning(f"Error cleaning HLS directory: {e}")

        ffmpeg_cmd = [
            "ffmpeg", "-i", INPUT_URL, "-c", "copy", "-f", "hls",
            "-hls_time", HLS_TIME, "-hls_list_size", HLS_LIST_SIZE,
            "-hls_flags", HLS_FLAGS, OUTPUT_M3U8_PATH
        ]
        app_logger.info(f"Starting ffmpeg command: {' '.join(ffmpeg_cmd)}")
        try:
            ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                preexec_fn=os.setsid, text=True, errors='ignore'
             )
            time.sleep(0.1)
            if ffmpeg_process.poll() is not None:
                 stderr_output = ""; try: stderr_output = ffmpeg_process.stderr.read() except: pass
                 app_logger.error(f"FFmpeg process failed immediately after start (Exit code: {ffmpeg_process.poll()}).")
                 if stderr_output: ffmpeg_logger.error(f"FFmpeg stderr: {stderr_output.strip()}")
                 ffmpeg_process = None; return False

            app_logger.info(f"FFmpeg started with PID: {ffmpeg_process.pid}, PGID: {os.getpgid(ffmpeg_process.pid)}")

            ffmpeg_stderr_thread = threading.Thread(target=log_stream, args=(ffmpeg_process.stderr, ffmpeg_logger.warning), daemon=True)
            ffmpeg_stdout_thread = threading.Thread(target=log_stream, args=(ffmpeg_process.stdout, ffmpeg_logger.info), daemon=True)
            ffmpeg_stderr_thread.start()
            ffmpeg_stdout_thread.start()
            app_logger.debug("Started FFmpeg stdout/stderr logging threads.") # Debug level

            time.sleep(2)
            return True
        except Exception as e:
             app_logger.error(f"Failed to execute Popen for ffmpeg: {e}", exc_info=True)
             ffmpeg_process = None; return False


# --- Activity Checker ---
def check_activity():
    global ffmpeg_process
    while not stop_event.is_set():
        with ffmpeg_lock:
            current_process = ffmpeg_process
            if current_process:
                process_poll = current_process.poll()
                if process_poll is None: # Check if running
                    # --- Add detailed logging here ---
                    now = time.time()
                    elapsed_since_last_req = now - last_request_time
                    # Log this check every time it runs while ffmpeg is active
                    app_logger.debug(f"Activity check: Running. Now={now:.1f}, LastReq={last_request_time:.1f}, Elapsed={elapsed_since_last_req:.1f}s")
                    # ---------------------------------
                    # Check inactivity only if ffmpeg is running
                    if elapsed_since_last_req > INACTIVITY_TIMEOUT:
                        app_logger.info(f"Inactivity detected (last request: {elapsed_since_last_req:.1f}s ago). Stopping ffmpeg.")
                        stop_ffmpeg()
                    # else: still active, do nothing
                else:
                    # FFmpeg died on its own
                    app_logger.warning(f"Detected FFmpeg process (PID: {current_process.pid}) died unexpectedly (Exit code: {process_poll}). Calling stop_ffmpeg to cleanup.")
                    stop_ffmpeg()
            # else: ffmpeg_process is None, no need to check inactivity
            #    app_logger.debug("Activity check: FFmpeg not running.") # Optional: log when not running

        stop_event.wait(5) # Check every 5 seconds
    app_logger.info("Activity checker thread finished.")


# --- Routes ---
@app.route('/')
def index():
    app_logger.info("Request for index.html")
    update_last_request_time()
    if not start_ffmpeg():
         app_logger.error("ffmpeg failed to start when accessing index.html")
    return send_from_directory(HLS_OUTPUT_DIR, 'index.html')

@app.route('/hls.js@latest')
def hls_js():
    app_logger.info("Request for hls.js")
    # No update_last_request_time() here
    return send_from_directory(HLS_OUTPUT_DIR, 'hls.js@latest')

@app.route('/<path:filename>')
def hls_files(filename):
    if filename == STREAM_FILE or filename.endswith(".ts"):
        app_logger.info(f"Request for HLS file: {filename}")
        update_last_request_time() # Update activity time for HLS files
        if not start_ffmpeg():
             app_logger.error(f"ffmpeg not running or failed to start when requesting {filename}.")
             abort(503)

        file_path = os.path.join(HLS_OUTPUT_DIR, filename)
        max_wait = 5; wait_interval = 0.5; waited = 0
        while not os.path.exists(file_path) and waited < max_wait:
            with ffmpeg_lock:
                if ffmpeg_process is None or ffmpeg_process.poll() is not None:
                    app_logger.error(f"FFmpeg process (PID: {ffmpeg_process.pid if ffmpeg_process else 'N/A'}) is not running while waiting for file {filename}.")
                    abort(503)
            app_logger.warning(f"File {filename} not found, waiting {wait_interval}s...")
            time.sleep(wait_interval)
            waited += wait_interval

        if not os.path.exists(file_path):
             app_logger.error(f"File {filename} not found after waiting {waited:.1f}s.")
             with ffmpeg_lock:
                 if ffmpeg_process is None or ffmpeg_process.poll() is not None:
                     app_logger.error(f"FFmpeg process (PID: {ffmpeg_process.pid if ffmpeg_process else 'N/A'}) is not running after failing to find {filename}.")
                     abort(503)
                 else:
                     app_logger.error(f"FFmpeg (PID: {ffmpeg_process.pid}) is running but {filename} still not found. Aborting request.")
                     abort(404)

        response = send_from_directory(HLS_OUTPUT_DIR, filename)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"; response.headers["Expires"] = "0"
        return response
    elif filename == 'favicon.ico': abort(404)
    else: app_logger.warning(f"Denying request for non-HLS/index/favicon file: {filename}"); abort(404)

def update_last_request_time():
    global last_request_time
    now = time.time()
    app_logger.debug(f"Updating last_request_time from {last_request_time:.1f} to {now:.1f}")
    last_request_time = now


# --- Main Execution ---
# main block remains the same
if __name__ == '__main__':
    if not INPUT_URL: app_logger.error("FATAL: INPUT_URL not set."); exit(1)
    app_logger.info(f"Input URL configured: {INPUT_URL}")
    app_logger.info(f"HLS Output Dir: {HLS_OUTPUT_DIR}")
    app_logger.info(f"Inactivity Timeout: {INACTIVITY_TIMEOUT}s")
    app_logger.info(f"Server Port: {SERVER_PORT}")

    try:
        initial_cleaned_count = 0
        if os.path.exists(HLS_OUTPUT_DIR):
            for f in os.listdir(HLS_OUTPUT_DIR):
                if f.endswith(".ts") or f == STREAM_FILE:
                    file_path = os.path.join(HLS_OUTPUT_DIR, f)
                    if os.path.exists(file_path): os.remove(file_path); initial_cleaned_count += 1
        if initial_cleaned_count > 0: app_logger.info(f"Performed initial cleanup of {initial_cleaned_count} HLS files.")
    except Exception as e: app_logger.warning(f"Error during initial cleanup: {e}")

    checker_thread = threading.Thread(target=check_activity, daemon=True)
    checker_thread.start()
    app_logger.info("Activity checker thread started.")

    app_logger.info(f"Starting Flask server on 0.0.0.0:{SERVER_PORT}")
    try:
        app.run(host='0.0.0.0', port=SERVER_PORT, threaded=True)
    finally:
        app_logger.info("Flask server shutting down.")
        stop_event.set()
        app_logger.info("Stopping ffmpeg process if running...")
        stop_ffmpeg()
        app_logger.info("Waiting for activity checker thread to finish...")
        checker_thread.join(timeout=2)
        app_logger.info("Cleanup complete. Exiting.")
