# SurroundCore RemoteLink

RemoteLink is the planned secure remote-zone transport for SurroundCore. It is designed to work without static IP addresses, DDNS, inbound port forwarding or exposing the Core web interface to the public Internet.

## Design goals

- Outbound-only connectivity from both Core and remote endpoint.
- One-time-code / QR pairing.
- Per-device public/private key identity.
- Direct peer-to-peer path when NAT traversal succeeds.
- Encrypted relay fallback over TCP/QUIC/HTTPS 443 when direct connectivity is impossible.
- Relay is transport-only and cannot inspect library metadata or audio payloads.
- Local playback never depends on RemoteLink being online.
- Remote endpoint advertises the same capability model as a LAN endpoint.
- Signal path always shows source quality, remote transport and endpoint render format.

## Proposed connection flow

1. Administrator creates a one-time pairing code in the SurroundCore web UI.
2. Remote endpoint enters/scans the code and generates its own key pair.
3. Core and endpoint register ephemeral reachability information with a rendezvous service using outbound HTTPS/QUIC.
4. Peers attempt direct UDP/QUIC traversal.
5. If direct traversal fails, both peers connect outbound to an encrypted relay on port 443.
6. The remote zone appears in the normal SurroundCore Zones UI.

## Audio transport

RemoteLink should negotiate transport separately from endpoint render format.

Preferred modes:

- Lossless FLAC framing for bandwidth-efficient remote lossless playback.
- Native PCM where bandwidth and latency make it sensible.
- Optional Opus compatibility mode for constrained links, only when explicitly enabled.

There must be no silent quality reduction. Example signal path:

`FLAC 24/96 -> RemoteLink FLAC lossless -> remote endpoint -> USB DAC 24/96`

or, when explicitly allowed:

`FLAC 24/96 -> RemoteLink Opus compatibility -> remote endpoint -> DAC 48 kHz`

## Control transport

Control traffic should use authenticated bidirectional QUIC or WebSocket sessions with:

- play / pause / seek / next / previous
- queue updates
- volume / mute
- endpoint capability refresh
- health / latency / packet-loss metrics
- reconnect and session recovery

## Security model

- Mutual device authentication.
- Per-device revocation from the Admin UI.
- No shared global remote-access password.
- Relay never receives plaintext audio or control messages.
- No public exposure of the Core HTTP API is required.

## Open-source deployment

RemoteLink must support a fully self-hosted rendezvous/relay service. A public community relay can be optional, never mandatory.

The design may reuse well-tested open-source components/protocols (WireGuard, ICE/STUN/TURN concepts, QUIC) rather than inventing new cryptography.
