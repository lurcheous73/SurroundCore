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
- Bandcamp: provider slot uses the official-account/Subsonic style integration; authentication/stream adapter still to be connected.
- HDtracks: both purchased-library and streaming provider slots are reserved; PCM/FLAC, MQA and AIRIA capability negotiation is modelled, but partner authentication/codec modules are not bundled.
- Spotify: intended as a native/Connect-style Core provider rather than a ControlMac decoder.
- TIDAL and Qobuz: partner/API adapters pending.
- Apple Music and Audible: native-authorised handoff/provider adapters pending.
- Podcasts/RSS: native Core provider planned.

Provider-specific SDKs, keys and licensed codecs are not faked or embedded. A provider reports `implemented: false` until a real adapter is installed.

## Core API

- `GET /api/v1/streaming`
- `POST /api/v1/streaming/settings`
- `POST /api/v1/streaming/radio`
- `DELETE /api/v1/streaming/radio/{id}`
- `POST /api/v1/streaming/play`
- `POST /api/v1/playback/url`
- `POST /api/v1/playback/{endpoint}/stop`
- `GET /api/v1/playback/{endpoint}/status`
