FROM debian:13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    mono-devel python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY BridgeTool.cs server.py start.sh ./
RUN chmod +x /app/start.sh
ENV PATH="/opt/venv/bin:$PATH"
CMD ["/app/start.sh"]
