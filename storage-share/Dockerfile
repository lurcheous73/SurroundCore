FROM debian:13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    nfs-ganesha nfs-ganesha-vfs ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
