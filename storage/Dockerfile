FROM debian:13-slim
RUN sed -i 's/Components: main/Components: main contrib/' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends \
 python3 python3-venv ca-certificates util-linux mount cifs-utils smbclient nfs-common \
 rclone avahi-utils iproute2 zfsutils-linux smartmontools gdisk parted && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY storage_app ./storage_app
ENV PATH="/opt/venv/bin:$PATH"
CMD ["uvicorn","storage_app.main:app","--host","0.0.0.0","--port","8084"]
