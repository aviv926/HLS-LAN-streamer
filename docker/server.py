#!/usr/bin/env python3
import os
import sys
import subprocess
import threading
import signal
from http.server import SimpleHTTPRequestHandler, HTTPServer

# Globals for ffmpeg process and client tracking
ffmpeg_started = False
ffmpeg_process = None
active_clients = 0
active_clients_lock = threading.Lock()
idle_timer = None
# Idle timeout in seconds before stopping ffmpeg when no clients are active
IDLE_TIMEOUT = 10

def start_ffmpeg():
    global ffmpeg_started, ffmpeg_process
    if ffmpeg_started:
        return
    # Determine HLS output directory and ensure it exists
    hls_output_dir = os.getenv("HLS_OUTPUT_DIR", "/hls-web")
    if not os.path.exists(hls_output_dir):
        os.makedirs(hls_output_dir)
    # Clean up old HLS segments (if any)
    for f in os.listdir(hls_output_dir):
        if f.startswith("stream") and (f.endswith(".m3u8") or f.endswith(".ts")):
            try:
                os.remove(os.path.join(hls_output_dir, f))
            except Exception as e:
                print(f"Warning: could not remove file {f}: {e}")

    # Determine input URL either from FFMPEG_INPUT_URL or via yt-dlp from YT_DLP_URL.
    ffmpeg_input_url = os.getenv("FFMPEG_INPUT_URL", "").strip()
    yt_dlp_url = os.getenv("YT_DLP_URL", "").strip()

    if ffmpeg_input_url:
        input_url = ffmpeg_input_url
    elif yt_dlp_url:
        print("Fetching stream URL with yt-dlp...")
        try:
            res = subprocess.run(["yt-dlp", "-f", "best", "-g", yt_dlp_url],
                                 capture_output=True, text=True, check=True)
            input_url = res.stdout.splitlines()[0].strip()
        except subprocess.CalledProcessError as e:
            print("Error: Failed to get URL using yt-dlp.", file=sys.stderr)
            print("yt-dlp Output:", e.stdout, file=sys.stderr)
            print("yt-dlp Error:", e.stderr, file=sys.stderr)
            sys.exit(1)
    else:
        print("Error: You must set either FFMPEG_INPUT_URL or YT_DLP_URL in the .env file.", file=sys.stderr)
        sys.exit(1)

    # Get HLS settings with defaults
    hls_time = os.getenv("HLS_TIME", "4")
    hls_list_size = os.getenv("HLS_LIST_SIZE", "5")
    hls_flags = os.getenv("HLS_FLAGS", "delete_segments")
    output_m3u8 = os.path.join(hls_output_dir, "stream.m3u8")

    ffmpeg_cmd = [
        "ffmpeg",
        "-i", input_url,
        "-c", "copy",
        "-f", "hls",
        "-hls_time", hls_time,
        "-hls_list_size", hls_list_size,
        "-hls_flags", hls_flags,
        output_m3u8
    ]
    print("Starting FFmpeg with command:")
    print(" ".join(ffmpeg_cmd))
    ffmpeg_process = subprocess.Popen(ffmpeg_cmd)
    ffmpeg_started = True
    print(f"FFmpeg started with PID {ffmpeg_process.pid}")

def stop_ffmpeg():
    global ffmpeg_started, ffmpeg_process, idle_timer
    if ffmpeg_started and ffmpeg_process:
        print("Idle timeout reached. Terminating ffmpeg.")
        try:
            ffmpeg_process.terminate()
            ffmpeg_process.wait()
            print("FFmpeg terminated due to idle timeout.")
        except Exception as e:
            print(f"Error while terminating ffmpeg: {e}")
        ffmpeg_started = False
    if idle_timer:
        idle_timer.cancel()
        idle_timer = None

def reset_idle_timer():
    global idle_timer
    if idle_timer:
        idle_timer.cancel()
    idle_timer = threading.Timer(IDLE_TIMEOUT, check_idle)
    idle_timer.daemon = True
    idle_timer.start()

def check_idle():
    with active_clients_lock:
        if active_clients <= 0:
            stop_ffmpeg()

class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        global active_clients, ffmpeg_started
        with active_clients_lock:
            active_clients += 1
            # Cancel the idle timer if a new request comes in
            if idle_timer:
                idle_timer.cancel()

        # On the initial request for the index (or "/index.html"), start ffmpeg if not started.
        if not ffmpeg_started and (self.path == "/" or self.path.startswith("/index.html")):
            threading.Thread(target=start_ffmpeg, daemon=True).start()

        super().do_GET()

    def finish(self):
        try:
            super().finish()
        finally:
            global active_clients
            with active_clients_lock:
                active_clients -= 1
                if active_clients <= 0:
                    # Start idle timer when no active client remains.
                    reset_idle_timer()

def run_server():
    server_port = int(os.getenv("SERVER_PORT", "8007"))
    hls_output_dir = os.getenv("HLS_OUTPUT_DIR", "/hls-web")
    os.chdir(hls_output_dir)
    httpd = HTTPServer(("", server_port), CustomHTTPRequestHandler)
    print(f"Starting custom HTTP server on port {server_port}, serving files from {hls_output_dir}")

    def shutdown_handler(signum, frame):
        global ffmpeg_process
        print(f"Received signal {signum}, shutting down...")
        if ffmpeg_process:
            try:
                ffmpeg_process.terminate()
                ffmpeg_process.wait()
                print("FFmpeg process terminated.")
            except Exception as e:
                print(f"Error terminating ffmpeg process: {e}")
        httpd.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        shutdown_handler(signal.SIGINT, None)

if __name__ == "__main__":
    run_server()