# SurroundCore streaming

Streaming belongs to SurroundCore. ControlMac remains a control surface and does not hold service credentials or decode long-running streams.

## Quality policy

The default policy is `highest_native`:

- request the highest native resolution the provider/account permits;
- prefer lossless delivery when the provider offers it;
- never downsample unless explicitly enabled;
- never downmix unless explicitly enabled;
- preserve legacy/local MQA payloads for pass-through rather than silently processing them;
- prefer AIRIA only when an authorised AIRIA module is actually installed;
- negotiate MHR/MMHR only with an endpoint adapter that genuinely advertises those transports.

The ordinary ALSA endpoint agent advertises PCM only. It never claims MHR or MMHR.

## Web setup

Open `http://CORE:8080/setup/streaming`.

The page stores the SurroundCore token only in browser `sessionStorage`. ControlMac can later open the page with `#token=...`; URL fragments are not sent to the HTTP server and are removed from the address bar after loading.

## Provider status

- Internet Radio: native Core provider; saved HTTP/HTTPS streams and endpoint routing are implemented.
- Bandcamp: Subsonic account configuration, connection test, purchased-album browsing, album detail and Core-proxied stream playback are implemented. Live Bandcamp account verification is still pending.
- HDtracks: both purchased-library and streaming provider slots are reserved; PCM/FLAC, MQA and AIRIA capability negotiation is modelled, but partner authentication/codec modules are not bundled.
- Spotify: official Spotify Soloist integration is the target; Core/endpoint control adapter work is in progress and the Soloist binary must come from Spotify.
- TIDAL: official OAuth/API metadata path is available, but playback must remain inside TIDAL's official Player SDK. Adapter pending.
- Qobuz: Qobuz Connect receiver support requires partner integration; third-party apps cannot control Qobuz Connect directly. Adapter pending.
- Apple Music and Audible: native-authorised handoff/provider adapters pending.
- Podcasts/RSS: feed registration, RSS/Atom episode discovery and endpoint playback are implemented.

Provider-specific SDKs, keys and licensed codecs are not faked or embedded. Bandcamp credentials are held in a separate mode-0600 Core secrets file and are never returned through the API.

## Core API

- `GET /api/v1/streaming`
- `POST /api/v1/streaming/settings`
- `POST /api/v1/streaming/radio`
- `DELETE /api/v1/streaming/radio/{id}`
- `POST /api/v1/streaming/play`
- `POST /api/v1/playback/url`
- `POST /api/v1/playback/{endpoint}/stop`
- `GET /api/v1/playback/{endpoint}/status`

Provider APIs added in v0.4 development:

- `GET /api/v1/providers/bandcamp`
- `POST|DELETE /api/v1/providers/bandcamp/configure`
- `GET /api/v1/providers/bandcamp/albums`
- `GET /api/v1/providers/bandcamp/albums/{id}`
- `GET /api/v1/providers/bandcamp/stream/{song_id}`
- `POST /api/v1/providers/podcasts`
- `DELETE /api/v1/providers/podcasts/{feed_id}`
- `GET /api/v1/providers/podcasts/{feed_id}/episodes`
