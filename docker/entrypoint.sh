#!/bin/bash
set -e

echo "Ensuring HLS output directory exists: $HLS_OUTPUT_DIR"
mkdir -p "$HLS_OUTPUT_DIR"

echo "Cleaning up old HLS segments from $HLS_OUTPUT_DIR..."
rm -f "$HLS_OUTPUT_DIR"/stream*.m3u8 "$HLS_OUTPUT_DIR"/*.ts

echo "Starting custom HTTP server..."
exec python3 server.py