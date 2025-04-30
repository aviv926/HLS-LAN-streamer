#!/bin/bash
set -e

# Ensure HLS output directory is set via .env; if not, a default can be provided.
echo "Ensuring HLS output directory exists: $HLS_OUTPUT_DIR"
mkdir -p "$HLS_OUTPUT_DIR"

# Optionally clean up old HLS segments
echo "Cleaning up old HLS segments from $HLS_OUTPUT_DIR..."
rm -f "$HLS_OUTPUT_DIR"/stream*.m3u8 "$HLS_OUTPUT_DIR"/*.ts

# Start the custom HTTP server
echo "Starting custom HTTP server..."
exec python3 server.py