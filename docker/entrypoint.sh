#!/bin/bash
set -e

# --- Prepare HLS output directory ---
# The HLS_OUTPUT_DIR environment variable should be set (default is /hls-web)
echo "Ensuring HLS output directory exists: $HLS_OUTPUT_DIR"
mkdir -p "$HLS_OUTPUT_DIR"

# (Optional) Clean up old HLS segments (if any)
echo "Cleaning up old HLS segments (if any) from $HLS_OUTPUT_DIR..."
rm -f "$HLS_OUTPUT_DIR"/stream*.m3u8 "$HLS_OUTPUT_DIR"/*.ts

# --- Start the custom HTTP server which will start ffmpeg on demand ---
echo "Starting custom HTTP server..."
exec python3 server.py