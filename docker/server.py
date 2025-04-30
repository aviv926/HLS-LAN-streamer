#!/usr/bin/env python3
import os
import subprocess
import threading
import time
import logging
import signal
# Change Lock to RLock here
from threading import RLock
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
# Use RLock instead of Lock
ffmpeg_lock = RLock()
stop_event = threading.Event()

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Flask App ---
app = Flask(__name__)

# --- FFmpeg Management ---
# stop_ffmpeg function remains the same as the previous version
def stop_ffmpeg():
    global ffmpeg_process
    with ffmpeg_lock: # This will now work correctly with RLock
        if ffmpeg_process: # Check if the object exists first
            pid = ffmpeg_process.pid
            pgid = 0
            try:
                # Try to get pgid. If process is gone, ProcessLookupError occurs.
                pgid = os.getpgid(pid)
            except ProcessLookupError:
                logging.warning(f"Process {pid} already gone before trying to get PGID. Clearing variable.")
                ffmpeg_process = None # Already dead, just clear the variable
                return # Nothing more to do

            if ffmpeg_process.poll() is None: # Check if it's actually running *after* getting pgid
                logging.info(f"Attempting to stop ffmpeg process group (PID: {pid}, PGID: {pgid})...")
                try:
                    # Send SIGTERM to the entire process group
                    os.killpg(pgid, signal.SIGTERM)
                    ffmpeg_process.wait(timeout=10) # Wait for graceful shutdown
                    logging.info(f"FFmpeg process group (PID: {pid}) terminated gracefully.")
                except ProcessLookupError:
                    # This could happen if it died between poll() and killpg()
                    logging.warning(f"FFmpeg process group (PID: {pid}) already gone during termination attempt.")
                except subprocess.TimeoutExpired:
                    logging.warning(f"FFmpeg (PID: {pid}) did not terminate gracefully after 10s, sending SIGKILL to PGID {pgid}.")
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                        # Give a brief moment for kill to take effect
                        time.sleep(0.1)
                        logging.info(f"FFmpeg process group (PID: {pid}) killed.")
                    except ProcessLookupError:
                         logging.warning(f"FFmpeg process group (PID: {pid}) already gone when sending SIGKILL.")
                    except Exception as e:
                        logging.error(f"Error sending SIGKILL to ffmpeg process group (PID: {pid}, PGID: {pgid}): {e}")
                except Exception as e:
                    logging.error(f"Error stopping ffmpeg process group (PID: {pid}, PGID: {pgid}): {e}")
                finally:
                    # Ensure variable is cleared even if errors occurred during kill
                    logging.info(f"Setting ffmpeg_process to None after termination attempt (PID: {pid}).")
                    ffmpeg_process = None
            else:
                # Process object existed but was already dead when poll() was checked
                logging.warning(f"FFmpeg process (PID: {pid}) was already terminated (exit code {ffmpeg_process.poll()}) before stop command could run kill.")
                ffmpeg_process = None # Clear the variable
        else:
            # ffmpeg_process was already None
            logging.info("Stop ffmpeg called, but process was already None.")

# start_ffmpeg function remains the same as the previous version
def start_ffmpeg():
    global ffmpeg_process
    with ffmpeg_lock: # This will now work correctly with RLock
        # Check if the process object exists AND is running
        if ffmpeg_process and ffmpeg_process.poll() is None:
             logging.info("Start ffmpeg called, but FFmpeg process object exists and poll() is None (already running).")
             return True # Indicate already running

        # If process is None or not running (poll() is not None), proceed to start
        logging.info("Start ffmpeg called: Process is None or poll() is not None. Attempting to start.")

        if not INPUT_URL:
            logging.error("INPUT_URL is not set. Cannot start ffmpeg.")
            return False # Failed to start

        # Clean up old segments before starting
        try:
            cleaned_count = 0
            for f in os.listdir(HLS_OUTPUT_DIR):
                if f.endswith(".ts") or f == STREAM_FILE:
                    # Check if file exists before removing, might have been deleted by ffmpeg itself
                    file_path = os.path.join(HLS_OUTPUT_DIR, f)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        cleaned_count += 1
            if cleaned_count > 0:
                logging.info(f"Cleaned {cleaned_count} old HLS file(s).")
            else:
                logging.info("No old HLS files found to clean.")
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
        try:
            # Use preexec_fn=os.setsid to create a process group
            # Use text=True for easier stderr reading if needed later, ignore errors
            ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
                text=True,
                errors='ignore'
             )
            # Check if process started successfully
            time.sleep(0.1) # Give a tiny moment for process to potentially fail immediately
            if ffmpeg_process.poll() is not None:
                 # It died immediately
                 stderr_output = ffmpeg_process.stderr.read()
                 logging.error(f"FFmpeg process failed immediately after start (Exit code: {ffmpeg_process.poll()}).")
                 if stderr_output:
                      logging.error(f"FFmpeg stderr: {stderr_output.strip()}")
                 ffmpeg_process = None # Ensure it's None
                 return False # Failed to start

            # Process started and seems okay for now
            logging.info(f"FFmpeg started with PID: {ffmpeg_process.pid}, PGID: {os.getpgid(ffmpeg_process.pid)}")
            time.sleep(2) # Small delay to allow ffmpeg to create initial files
            return True # Started successfully
        except Exception as e:
             logging.error(f"Failed to execute Popen for ffmpeg: {e}", exc_info=True)
             ffmpeg_process = None # Ensure it's None if Popen failed
             return False # Failed to start


# --- Activity Checker ---
# check_activity function remains the same as the previous version
def check_activity():
    """Runs in a background thread to stop ffmpeg if inactive."""
    global ffmpeg_process # Ensure global is used
    while not stop_event.is_set():
        with ffmpeg_lock: # Acquire lock to check/stop (RLock allows re-acquisition)
            current_process = ffmpeg_process # Work with a local reference inside the lock
            if current_process:
                process_poll = current_process.poll()
                if process_poll is None: # Check if running
                    # Check inactivity only if ffmpeg is running
                    if time.time() - last_request_time > INACTIVITY_TIMEOUT:
                        logging.info(f"Inactivity detected (last request: {time.time() - last_request_time:.1f}s ago). Stopping ffmpeg.")
                        stop_ffmpeg() # stop_ffmpeg acquires RLock again, which is now allowed
                    # else: still active, do nothing
                else:
                    # FFmpeg died on its own
                    logging.warning(f"Detected FFmpeg process (PID: {current_process.pid}) died unexpectedly (Exit code: {process_poll}). Cleaning up variable.")
                    stderr_output = ""
                    try:
                        # Read remaining stderr if possible
                        stderr_output = current_process.stderr.read()
                    except Exception:
                        pass # Ignore errors reading stderr if process is gone
                    if stderr_output:
                        logging.warning(f"FFmpeg stderr: {stderr_output.strip()}")
                    ffmpeg_process = None # Mark as stopped by setting global variable
            # else: ffmpeg_process is None, nothing to check/stop

        # Check every 5 seconds (outside the lock)
        stop_event.wait(5)
    logging.info("Activity checker thread finished.")


# --- Routes ---
# Routes remain the same as the previous version
@app.route('/')
def index():
    """Serves the main HTML page."""
    logging.info("Request for index.html")
    # Treat access to index as activity
    update_last_request_time()
    # Attempt to start ffmpeg if not running when index is accessed
    if not start_ffmpeg():
         # Optional: Maybe return an error page if ffmpeg fails to start?
         logging.error("ffmpeg failed to start when accessing index.html")
         # For now, just serve index.html anyway, client-side might show error
         pass
    return send_from_directory(HLS_OUTPUT_DIR, 'index.html')

@app.route('/hls.js@latest')
def hls_js():
    """Serves the hls.js library."""
    logging.info("Request for hls.js")
    # Don't treat hls.js request as activity for ffmpeg restart,
    # only index.html and actual stream files should trigger it.
    # update_last_request_time() # Removed
    return send_from_directory(HLS_OUTPUT_DIR, 'hls.js@latest')

@app.route('/<path:filename>')
def hls_files(filename):
    """Serves HLS manifest and segments."""
    if filename == STREAM_FILE or filename.endswith(".ts"):
        logging.info(f"Request for HLS file: {filename}")
        update_last_request_time() # Update activity time for HLS files
        if not start_ffmpeg(): # Ensure ffmpeg is running
             # If start_ffmpeg returns false, it means it either failed to start
             # or INPUT_URL wasn't set. Check logs for details.
             logging.error(f"ffmpeg not running or failed to start when requesting {filename}.")
             abort(503) # Service Unavailable seems appropriate

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
             abort(404) # Not Found

        # Serve the file
        response = send_from_directory(HLS_OUTPUT_DIR, filename)
        # Add headers to prevent caching of manifest and segments
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    elif filename == 'favicon.ico':
        # Browsers often request this, return 404 cleanly
        abort(404)
    else:
        logging.warning(f"Denying request for non-HLS/index/favicon file: {filename}")
        abort(404)

def update_last_request_time():
    """Updates the timestamp of the last relevant activity."""
    global last_request_time
    last_request_time = time.time()
    logging.debug(f"Updated last_request_time to {last_request_time}")


# --- Main Execution ---
# Main execution block remains the same as the previous version
if __name__ == '__main__':
    # Ensure INPUT_URL is available
    if not INPUT_URL:
        logging.error("FATAL: INPUT_URL environment variable not set.")
        exit(1)

    logging.info(f"Input URL configured: {INPUT_URL}")
    logging.info(f"HLS Output Dir: {HLS_OUTPUT_DIR}")
    logging.info(f"Inactivity Timeout: {INACTIVITY_TIMEOUT}s")
    logging.info(f"Server Port: {SERVER_PORT}")

    # Perform initial cleanup
    try:
        initial_cleaned_count = 0
        if os.path.exists(HLS_OUTPUT_DIR):
            for f in os.listdir(HLS_OUTPUT_DIR):
                if f.endswith(".ts") or f == STREAM_FILE:
                    file_path = os.path.join(HLS_OUTPUT_DIR, f)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        initial_cleaned_count += 1
        if initial_cleaned_count > 0:
             logging.info(f"Performed initial cleanup of {initial_cleaned_count} HLS files.")
    except Exception as e:
        logging.warning(f"Error during initial cleanup: {e}")


    # Start the background activity checker thread
    checker_thread = threading.Thread(target=check_activity, daemon=True)
    checker_thread.start()
    logging.info("Activity checker thread started.")

    # Start Flask server
    logging.info(f"Starting Flask server on 0.0.0.0:{SERVER_PORT}")
    try:
        # Use waitress or gunicorn in production instead of app.run
        app.run(host='0.0.0.0', port=SERVER_PORT, threaded=True)
    finally:
        logging.info("Flask server shutting down.")
        stop_event.set() # Signal checker thread to stop
        logging.info("Stopping ffmpeg process if running...")
        stop_ffmpeg() # Ensure ffmpeg is stopped on exit
        logging.info("Waiting for activity checker thread to finish...")
        checker_thread.join(timeout=2) # Wait briefly for checker thread
        logging.info("Cleanup complete. Exiting.")