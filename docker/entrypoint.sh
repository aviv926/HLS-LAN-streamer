#!/bin/sh
# Exit immediately if a command exits with a non-zero status.
set -e

# --- Configuration (Defaults read by Python script now) ---
# SERVER_PORT=${SERVER_PORT:-8007} # Handled by Python
# HLS_TIME=${HLS_TIME:-4}         # Handled by Python
# HLS_LIST_SIZE=${HLS_LIST_SIZE:-5} # Handled by Python
# HLS_FLAGS=${HLS_FLAGS:-delete_segments} # Handled by Python
HLS_OUTPUT_DIR="/hls-web" # Still needed for cleanup here

# --- Determine Input URL ---
INPUT_URL=""
if [ -n "$FFMPEG_INPUT_URL" ]; then
    INPUT_URL="$FFMPEG_INPUT_URL"
    echo "Using direct FFMPEG_INPUT_URL: $INPUT_URL"
elif [ -n "$YT_DLP_URL" ]; then
    echo "Attempting to get stream URL from yt-dlp for: $YT_DLP_URL"
    # Capture output and exit code separately for better error handling
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
    echo "Error: You must set either FFMPEG_INPUT_URL or YT_DLP_URL in the .env file or environment." >&2
    exit 1
fi

# --- Prepare HLS directory (Optional Cleanup) ---
# Python script now handles cleanup before starting ffmpeg,
# but we can do an initial cleanup here too.
echo "Ensuring HLS output directory exists: $HLS_OUTPUT_DIR"
mkdir -p "$HLS_OUTPUT_DIR"
echo "Cleaning up old HLS segments (if any) from $HLS_OUTPUT_DIR..."
rm -f "$HLS_OUTPUT_DIR"/stream*.m3u8 "$HLS_OUTPUT_DIR"/*.ts

# --- Export environment variables for Python script ---
export INPUT_URL
# Other variables like SERVER_PORT, HLS_TIME etc. are read directly
# by the Python script from the environment if they are set.

# --- Start the Python Flask Server ---
echo "Starting Python server..."
exec python3 /app/server.py # Use exec to replace the shell process with Python