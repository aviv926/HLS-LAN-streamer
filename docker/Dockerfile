# Use a slim Python base image
FROM python:3.13-slim

# Set environment variables to prevent interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Install FFmpeg, curl (to download hls.js), and clean up
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl iproute2 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install yt-dlp using pip
RUN pip install --no-cache-dir yt-dlp

# Set the working directory inside the container
WORKDIR /app

# Create the directory to serve web content and HLS stream
RUN mkdir /hls-web

# Download hls.js library and save it with the specific name needed by the index.html
RUN curl -o /hls-web/hls.js@latest -L https://cdn.jsdelivr.net/npm/hls.js@latest

# Copy the HTML file into the web serving directory
COPY index.html /hls-web/index.html

# Copy the entrypoint script
COPY entrypoint.sh /app/entrypoint.sh

# Make the entrypoint script executable
RUN chmod +x /app/entrypoint.sh

# Expose the default server port (can be overridden by .env)
EXPOSE 8007

# Run the entrypoint script when the container starts
ENTRYPOINT ["/app/entrypoint.sh"]