# SurroundCore Remote Transport

This service reserves the remote-access boundary without choosing the final carrier yet.

Current contract:
- apps prefer the local Core when reachable;
- remote transport is used only when local Core is unavailable;
- the carrier is deliberately pluggable and currently unconfigured;
- destructive operations are denied for remote sessions by policy;
- pairing/QR/device credentials are reserved for the later app pass.

The service is behind the Docker Compose `remote` profile and therefore does not start in normal development installs.
