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
- Spotify: official Spotify Soloist configuration/control is implemented (status, now playing, queue, URI play, pause, next/previous, volume and activate/deactivate). The Soloist binary/API key must come from Spotify and are not redistributed.
- Sonos Radio / Favorites: Sonos OAuth/Control API account configuration, households, groups, favorites and favorite playback are implemented. Sonos-served audio remains on Sonos hardware.
- TIDAL: licensed-provider bridge contract is implemented; the actual playback sidecar must use TIDAL's official Player SDK.
- Qobuz: licensed-provider bridge contract is implemented; a Qobuz Connect receiver still requires partner integration.
- HDtracks/AIRIA: licensed-provider bridge contract requests highest-native/lossless-first with AIRIA preferred when an authorised module is present.
- Apple Music and Audible: licensed/native-authorised bridge slots are implemented; official playback modules are still required.
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

Provider APIs in v0.5 development:

- `GET /api/v1/providers/bandcamp`
- `POST|DELETE /api/v1/providers/bandcamp/configure`
- `GET /api/v1/providers/bandcamp/albums`
- `GET /api/v1/providers/bandcamp/albums/{id}`
- `GET /api/v1/providers/bandcamp/stream/{song_id}`
- `POST /api/v1/providers/podcasts`
- `DELETE /api/v1/providers/podcasts/{feed_id}`
- `GET /api/v1/providers/podcasts/{feed_id}/episodes`

- `GET|POST|DELETE /api/v1/providers/spotify...` — Soloist configuration and control
- `GET|POST|DELETE /api/v1/providers/sonos...` — Sonos OAuth, households, groups and favorites
- `GET|POST|DELETE /api/v1/providers/bridge/{provider}...` — licensed TIDAL/Qobuz/HDtracks/Apple/Audible sidecar contract
