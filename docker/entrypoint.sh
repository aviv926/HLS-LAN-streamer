#!/bin/bash
# set -x # Uncomment for debugging

# ==================================================
# Entrypoint script for HLS Live Streamer
# ==================================================

# --- Configuration ---
# Source the .env file if it exists
if [ -f .env ]; then
    echo "Sourcing .env file..."
    set -a # Export all variables
    source .env
    set +a # Stop exporting variables
fi

# Set default values if not defined in .env
SERVER_PORT=${SERVER_PORT:-8007}
HLS_TIME=${HLS_TIME:-4}
HLS_LIST_SIZE=${HLS_LIST_SIZE:-5}
HLS_FLAGS=${HLS_FLAGS:-delete_segments}
HLS_OUTPUT_DIR=${HLS_OUTPUT_DIR:-/hls-web}

# --- Check for required variables ---
if [[ -z "$YT_DLP_URL" && -z "$FFMPEG_INPUT_URL" ]]; then
    echo "Error: You must set either FFMPEG_INPUT_URL or YT_DLP_URL in the .env file." >&2
    exit 1
fi

# --- Get the stream URL using yt-dlp if YT_DLP_URL is set ---
if [ -n "$YT_DLP_URL" ]; then
    echo "Getting stream URL using yt-dlp..."
    YT_DLP_OUTPUT=$(yt-dlp -g "$YT_DLP_URL")
    YT_DLP_EXIT_CODE=$?

    if [ $YT_DLP_EXIT_CODE -eq 0 ]; then
        INPUT_URL="$YT_DLP_OUTPUT"
        echo "Stream URL: $INPUT_URL"
    else
        echo "--------------------------------------------------" >&2
        echo "Error: Failed to get URL using yt-dlp." >&2
        echo "yt-dlp Output:" >&2
        echo "$YT_DLP_OUTPUT" >&2
        echo "yt-dlp Exit Code: $YT_DLP_EXIT_CODE" >&2
        echo "--------------------------------------------------" >&2
        exit 1
    fi
else
    echo "Using direct FFmpeg input URL from FFMPEG_INPUT_URL."
    INPUT_URL="$FFMPEG_INPUT_URL"
fi

# --- Prepare HLS directory ---
echo "Ensuring HLS output directory exists: $HLS_OUTPUT_DIR"
mkdir -p "$HLS_OUTPUT_DIR"
echo "Cleaning up old HLS segments (if any) from $HLS_OUTPUT_DIR..."
rm -f "$HLS_OUTPUT_DIR"/stream*.m3u8 "$HLS_OUTPUT_DIR"/*.ts

# Function to start FFmpeg
start_ffmpeg() {
    echo "Starting FFmpeg..."
    echo "Command: ffmpeg -i \"$INPUT_URL\" -c copy -f hls -hls_time $HLS_TIME -hls_list_size $HLS_LIST_SIZE -hls_flags $HLS_FLAGS $OUTPUT_M3U8"
    ffmpeg -i "$INPUT_URL" \
           -c copy \
           -f hls \
           -hls_time "$HLS_TIME" \
           -hls_list_size "$HLS_LIST_SIZE" \
           -hls_flags "$HLS_FLAGS" \
           "$OUTPUT_M3U8" &

    # Capture FFmpeg PID
    FFMPEG_PID=$!
    echo "FFmpeg started in background with PID $FFMPEG_PID"
}

# --- Check for active connections before starting FFmpeg ---
check_connections() {
  # Use ss to check for established connections on the server port
  CONNECTION_COUNT=$(ss -o state established '( dport = :'$SERVER_PORT' )' | wc -l)

  # Subtract 1 to exclude the header line from ss output
  CONNECTION_COUNT=$((CONNECTION_COUNT - 1))

  if [ "$CONNECTION_COUNT" -gt 0 ]; then
    echo "Active connection(s) detected. Starting FFmpeg."
    start_ffmpeg
    return 0
  else
    echo "No active connections. FFmpeg will not start."
    return 1
  fi
}

# --- Initial check and periodic checks ---
# Perform an initial check for connections
check_connections

# Check for connections periodically in the background
while true; do
  sleep 25 # Check every 60 seconds
  check_connections
  # If FFmpeg is not running and connections are detected, start it
  if [ -z "$FFMPEG_PID" ] || ! ps -p "$FFMPEG_PID" > /dev/null; then
      if check_connections; then
          start_ffmpeg
      fi
  fi
done &

# --- Graceful shutdown ---
cleanup() {
    echo "Received stop signal. Stopping FFmpeg (PID $FFMPEG_PID)..."
    kill -TERM -$FFMPEG_PID 2>/dev/null
    wait $FFMPEG_PID 2>/dev/null
    echo "FFmpeg stopped."
    exit 0
}

trap cleanup TERM INT

# --- Start Python HTTP Server ---
echo "Starting Python HTTP server on port $SERVER_PORT, serving files from $HLS_OUTPUT_DIR..."
cd "$HLS_OUTPUT_DIR"
python3 -m http.server "$SERVER_PORT" --bind 0.0.0.0 &

# Capture Python server PID
SERVER_PID=$!
echo "HTTP server started with PID $SERVER_PID"

# --- Keep container running ---
wait $SERVER_PID
SERVER_EXIT_CODE=$?
echo "HTTP server process (PID $SERVER_PID) exited with code $SERVER_EXIT_CODE."

echo "Stopping FFmpeg (PID $FFMPEG_PID)..."
kill -TERM -$FFMPEG_PID 2>/dev/null
wait $FFMPEG_PID 2>/dev/null
echo "FFmpeg stopped."

exit $SERVER_EXIT_CODE