# Social Media Downloader API — production image (Render / Railway ready)
FROM python:3.12-slim

# ffmpeg = MP3/WAV/OPUS audio conversion support
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render & Railway inject $PORT automatically
ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    MAX_DURATION_SECONDS=3600

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
