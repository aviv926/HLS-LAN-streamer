#!/bin/sh
# Exit immediately if a command exits with a non-zero status.
set -e

# --- Configuration ---
# Get variables from environment or use defaults
SERVER_PORT=${SERVER_PORT:-8007}
HLS_TIME=${HLS_TIME:-4}
HLS_LIST_SIZE=${HLS_LIST_SIZE:-5}
HLS_FLAGS=${HLS_FLAGS:-delete_segments}
HLS_OUTPUT_DIR="/hls-web" # Internal directory where HLS files and web content are stored
OUTPUT_M3U8="$HLS_OUTPUT_DIR/stream.m3u8"

# --- Determine Input URL ---
INPUT_URL=""
if [ -n "$FFMPEG_INPUT_URL" ]; then
    INPUT_URL="$FFMPEG_INPUT_URL"
    echo "Using direct FFMPEG_INPUT_URL: $INPUT_URL"
elif [ -n "$YT_DLP_URL" ]; then
    echo "Attempting to get stream URL from yt-dlp for: $YT_DLP_URL"
    # Capture output and exit code separately for better error handling
    # Use --no-warnings to reduce log noise if desired
    YT_DLP_OUTPUT=$(yt-dlp --get-url "$YT_DLP_URL" 2>&1)
    YT_DLP_EXIT_CODE=$?

    if [ $YT_DLP_EXIT_CODE -eq 0 ] && [ -n "$YT_DLP_OUTPUT" ]; then
        # Assuming the last line of output is the URL
        INPUT_URL=$(echo "$YT_DLP_OUTPUT" | tail -n 1)
        echo "Successfully got URL from yt-dlp: $INPUT_URL"
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
    echo "Error: You must set either FFMPEG_INPUT_URL or YT_DLP_URL in the .env file." >&2
    exit 1
fi

# --- Prepare HLS directory ---
echo "Ensuring HLS output directory exists: $HLS_OUTPUT_DIR"
mkdir -p "$HLS_OUTPUT_DIR"
echo "Cleaning up old HLS segments (if any) from $HLS_OUTPUT_DIR..."
rm -f "$HLS_OUTPUT_DIR"/stream*.m3u8 "$HLS_OUTPUT_DIR"/*.ts

# --- Start FFmpeg in the background ---
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

# --- Graceful shutdown ---
# Function to kill FFmpeg when the script receives a signal (e.g., from docker stop)
cleanup() {
    echo "Received stop signal. Stopping FFmpeg (PID $FFMPEG_PID)..."
    # Send SIGTERM to FFmpeg process group
    kill -TERM -$FFMPEG_PID 2>/dev/null # Kill the process group (including potential child processes)
    wait $FFMPEG_PID 2>/dev/null # Wait for FFmpeg to finish cleaning up segments etc.
    echo "FFmpeg stopped."
    exit 0 # Exit cleanly
}

# Trap SIGTERM and SIGINT signals and run the cleanup function
trap cleanup TERM INT

# --- Start Python HTTP Server ---
echo "Starting Python HTTP server on port $SERVER_PORT, serving files from $HLS_OUTPUT_DIR..."
cd "$HLS_OUTPUT_DIR"
python3 -m http.server "$SERVER_PORT" --bind 0.0.0.0 &

# Capture Python server PID
SERVER_PID=$!
echo "HTTP server started with PID $SERVER_PID"

# --- Keep container running ---
# Wait for either FFmpeg or the HTTP server to exit
# This ensures the script (and container) stays running as long as FFmpeg is active.
# If FFmpeg fails, the script will exit, and the container will stop (based on restart policy).
wait $FFMPEG_PID
FFMPEG_EXIT_CODE=$?
echo "FFmpeg process (PID $FFMPEG_PID) exited with code $FFMPEG_EXIT_CODE."

# If FFmpeg exited, we should also stop the server and exit
echo "Stopping HTTP server (PID $SERVER_PID)..."
kill -TERM $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
echo "HTTP server stopped."

# Exit with FFmpeg's exit code to signal potential problems
exit $FFMPEG_EXIT_CODE
