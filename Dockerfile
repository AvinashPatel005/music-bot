FROM python:3.11-slim

# Install system dependencies: ffmpeg for audio transcoding, libsodium & libopus for Discord voice
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    libopus0 \
    libsodium23 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose default port
ENV PORT=8000
EXPOSE 8000

# Start unified runner (FastAPI web server + Discord bot)
CMD ["python", "run_all.py"]
