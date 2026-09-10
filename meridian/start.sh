#!/bin/sh
set -eu
VENDOR=/vendor
for f in SooloosApp.dll SooloosBase.dll ClientBase.dll SooloosMessages.dll Messaging.dll; do
  [ -f "$VENDOR/$f" ] || { echo "Missing vendor assembly: $f" >&2; exit 2; }
done
export MONO_PATH="$VENDOR"
mcs -out:/app/BridgeTool.exe \
  -r:$VENDOR/SooloosApp.dll -r:$VENDOR/SooloosBase.dll \
  -r:$VENDOR/ClientBase.dll -r:$VENDOR/SooloosMessages.dll \
  -r:$VENDOR/Messaging.dll /app/BridgeTool.cs
exec uvicorn server:app --host 0.0.0.0 --port ${SURROUNDCORE_MERIDIAN_PORT:-8091}
