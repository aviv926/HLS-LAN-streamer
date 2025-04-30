# LAN HLS Streamer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A simple Dockerized solution to stream video content from various online sources (using `yt-dlp`) or direct URLs as an HLS (HTTP Live Streaming) feed, accessible on your Local Area Network (LAN).

This project uses `yt-dlp` to fetch stream URLs, `FFmpeg` to re-stream the content as HLS, and Python's built-in HTTP server to serve a basic HTML player page and the HLS stream files. Configuration is managed easily through a `.env` file.

## Features

*   **Easy Setup:** Runs entirely within Docker containers using Docker Compose.
*   **Flexible Input:**
    *   Supports any URL compatible with `yt-dlp` (YouTube, Twitch, many others).
    *   Supports direct input of existing HLS manifests or other stream URLs compatible with FFmpeg.
*   **HLS Streaming:** Converts the input stream into HLS format (`.m3u8` playlist and `.ts` segments) using `FFmpeg` with `-c copy` for minimal resource usage (no transcoding).
*   **Web Interface:** Serves a simple HTML page with an integrated video player.
*   **Self-Hosted Player:** Includes and serves `hls.js` locally, so the player works offline within the LAN without needing internet access (after initial setup).
*   **Configurable:** All key parameters (source URL, server port, HLS settings) are controlled via a simple `.env` file.
*   **Cross-Platform:** Works on any system that can run Docker and Docker Compose (Windows, macOS, Linux).

## Prerequisites

*   **Docker:** Download and install Docker Desktop (Windows/macOS) or Docker Engine (Linux). [Get Docker](https://docs.docker.com/get-docker/)
*   **Docker Compose:** Usually included with Docker Desktop. For Linux, you might need to install it separately. [Install Docker Compose](https://docs.docker.com/compose/install/)
*   **Git:** To clone the repository.

## Setup and Configuration

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/aviv926/HLS-LAN-streamer.git
    cd HLS-LAN-streamer
    ```

2.  **Create Configuration File:**
    Copy the example environment file to `.env`. This file will hold your specific settings and is ignored by Git.
    ```bash
    # On Linux/macOS/Git Bash on Windows
    cp .env.example .env

    # On Windows Command Prompt
    copy .env.example .env
    ```

3.  **Edit `.env` File:**
    Open the `.env` file with a text editor and configure the following variables:

    *   `SERVER_PORT`: The port number the web server will listen on within the container and be mapped to on your host machine. Clients on your LAN will connect to this port. (Default: `8007`)
    *   **Choose ONE source type:**
        *   `YT_DLP_URL`: Set this to the URL of the video or live stream you want to grab using `yt-dlp` (e.g., a YouTube live stream URL). Leave `FFMPEG_INPUT_URL` blank or commented out.
        *   `FFMPEG_INPUT_URL`: Set this if you *already* have a direct URL to a stream manifest (like `.m3u8`) that FFmpeg can read directly. This will be used *instead* of `YT_DLP_URL`.
    *   **Optional HLS Settings:**
        *   `HLS_TIME`: Duration of each HLS segment in seconds (Default: `4`).
        *   `HLS_LIST_SIZE`: Maximum number of segments listed in the playlist (Default: `5`).
        *   `HLS_FLAGS`: FFmpeg HLS flags (Default: `delete_segments`).

    **Example `.env` using `yt-dlp`:**
    ```dotenv
    SERVER_PORT=8007
    YT_DLP_URL=https://www.youtube.com/watch?v=jfKfPfyJRdk # Example: YouTube Stream
    FFMPEG_INPUT_URL=
    # HLS_TIME=4
    # HLS_LIST_SIZE=5
    # HLS_FLAGS=delete_segments
    ```

    **Example `.env` using a direct URL:**
    ```dotenv
    SERVER_PORT=8007
    YT_DLP_URL=
    FFMPEG_INPUT_URL=https://your-direct-stream-link/playlist.m3u8
    # HLS_TIME=4
    # HLS_LIST_SIZE=5
    # HLS_FLAGS=delete_segments
    ```

## Usage

Make sure you are in the repository directory (`HLS-LAN-streamer`) in your terminal or command prompt.

1.  **Choose Build or Pull:**

    *   **Option A: Pull Pre-built Image (Recommended for users)**
        If the maintainer has pushed the image to a container registry (like GitHub Container Registry or Docker Hub) and configured the `image:` line in `docker-compose.yml`, you can pull it directly:
        ```bash
        docker compose pull
        ```
        *(Note: The provided `docker-compose.yml` is set up to build locally by default. To use a pre-built image, you would comment out the `build:` section and uncomment/edit the `image:` line in `docker-compose.yml`)*

    *   **Option B: Build the Image Locally (Required first time or after code changes)**
        If you want to build the image yourself from the `Dockerfile`:
        ```bash
        docker compose build
        ```

2.  **Start the Service:**
    This command starts the container in the background (`-d`). It will read the `.env` file, start `ffmpeg`, and launch the web server.
    ```bash
    docker compose up -d
    ```

3.  **Access the Stream:**
    *   Find the **IP address** of the computer running Docker (the host machine) on your LAN.
        *   On Windows: Open Command Prompt and type `ipconfig`. Look for the "IPv4 Address" under your active network adapter (e.g., `192.168.1.100`).
        *   On macOS: Go to System Preferences > Network or type `ipconfig getifaddr en0` (or `en1`) in the Terminal.
        *   On Linux: Type `ip addr show` or `hostname -I` in the Terminal.
    *   Open a web browser on **any device** connected to the same LAN.
    *   Navigate to: `http://<HOST_IP_ADDRESS>:<SERVER_PORT>`
        *   (e.g., `http://192.168.1.100:8007` if your host IP is `192.168.1.100` and you used port `8007`)
    *   The page should load, and the HLS stream should start playing automatically after a few seconds (FFmpeg needs time to create the initial segments).

4.  **View Logs (Optional):**
    If you want to see the output from `yt-dlp`, `ffmpeg`, and the Python server:
    ```bash
    docker compose logs -f hls-streamer
    ```
    Press `Ctrl+C` to stop following the logs.

5.  **Stop the Service:**
    To stop the container and the stream:
    ```bash
    docker compose down
    ```
    This will stop and remove the container, but your configuration (`.env`) and the Docker image (if built locally) remain.

## Troubleshooting

*   **Stream Not Playing / Page Not Loading:**
    *   **Firewall:** Check the firewall on the computer running Docker (e.g., Windows Defender Firewall). Make sure incoming connections are allowed for the specified `SERVER_PORT` (e.g., 8007) on your Private/Local network.
    *   **IP Address:** Double-check you are using the correct IP address of the host machine.
    *   **Port:** Ensure you are using the correct `SERVER_PORT` specified in your `.env` file.
    *   **Logs:** Check the container logs (`docker compose logs hls-streamer`) for errors from `yt-dlp` (e.g., invalid URL, geo-restriction) or `ffmpeg` (e.g., connection refused, format errors).
*   **Error: "You must set either FFMPEG_INPUT_URL or YT_DLP_URL..."**
    *   Make sure you have correctly set *one* of these variables in your `.env` file and that the other is either blank or commented out (`#`).
*   **Port Conflict:**
    *   If you get an error message indicating the `SERVER_PORT` is already in use, choose a different port number, update it in your `.env` file, and run `docker compose down && docker compose up -d`.
*   **yt-dlp Errors:**
    *   `yt-dlp` might fail if the URL is incorrect, requires login, is geo-restricted, or if the service changes its API. Try accessing the `YT_DLP_URL` in your browser first. Check the logs for specific `yt-dlp` error messages. Sometimes updating `yt-dlp` might be necessary (requires rebuilding the Docker image).
