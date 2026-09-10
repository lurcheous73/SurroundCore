FROM debian:13-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg flac cd-paranoia cd-discid libcdio-utils lsdvd dvdbackup bchunk \
    util-linux mount eject sg3-utils usbutils udev ca-certificates curl \
    xorriso wodim dvd+rw-tools cdrdao 7zip \
    python3 python3-pip nodejs npm build-essential libusb-1.0-0-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt
COPY ingest_app /app/ingest_app
COPY netmd /opt/surroundcore/netmd
RUN cd /opt/surroundcore/netmd && npm ci --omit=dev
EXPOSE 8082
CMD ["uvicorn", "ingest_app.main:app", "--host", "0.0.0.0", "--port", "8082"]
