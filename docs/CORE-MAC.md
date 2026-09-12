# Core-Mac

Core-Mac is the macOS evolution of SurroundCore.

## Sources of truth

Core-Mac has two authoritative engineering sources:

1. `lurcheous73/SurroundCore`
   - playback/session engine
   - providers and library behaviour
   - endpoint discovery
   - groups/zones
   - storage and ingest behaviour
   - quality rules

2. `lurcheous73/brimstone-surroundcore-control` (ControlMacV2)
   - feature/function baseline
   - native Apple UI and control behaviour
   - device configuration
   - Meridian/Sooloos interoperability
   - Library / Now Playing / Devices / Configuration / Remote UX
   - Apple-native networking and local discovery patterns

Older ControlMac trees may be used as donor/reference implementations for proven protocol behaviour, but they are not the primary feature-development source.

## Target

Core-Mac is a native macOS application supporting both:

- Apple silicon: `arm64`
- Intel Mac: `x86_64`

The distributed application must be a single Universal 2 app wherever all bundled native components can be made universal. Apple-silicon Macs must not require Rosetta.

Minimum target: macOS 13 unless a feature proves a lower compatible deployment target is safe.

## Architectural rule

Do not port the Linux host. Port the SurroundCore service behaviour.

Core-Mac consists of:

- Native Swift 6 / SwiftUI application shell derived from ControlMacV2 behaviour.
- Shared native control/library/networking layer.
- Embedded SurroundCore service during transition, bound to loopback only.
- Native macOS platform adapters replacing Linux host functions.
- Bundled media helpers where licensing permits, built for both architectures.

Long-term, individual Python service areas may be migrated into the native shared core when that materially improves reliability or packaging; behavioural compatibility takes priority over rewrite-for-rewrite's-sake.

## Platform substitutions

| SurroundCore/Linux concept | Core-Mac implementation |
| --- | --- |
| systemd service | app lifecycle / launch agent only where genuinely required |
| Docker networking | native process + loopback IPC/network service |
| ALSA devices | CoreAudio / AVFoundation |
| `/dev/*` enumeration | IOKit / AVFoundation / Disk Arbitration as appropriate |
| Linux mount commands | FileManager, Disk Arbitration, SMB/NFS URLs or approved helper |
| Avahi/mDNS assumptions | Network.framework / Bonjour / NWBrowser |
| Linux secret files | Keychain |
| `/DATA` paths | app support + user-selected security-scoped locations |
| udev hotplug | Disk Arbitration / IOKit notifications |
| XDG/config dirs | `~/Library/Application Support/Core-Mac` |

## Audio integrity

The existing rule remains absolute: audio is never silently normalised, resampled, downmixed or otherwise altered.

Core-Mac must preserve source sample rate, bit depth and channel count whenever the selected endpoint supports them. Any conversion/fallback must be explicit and visible.

## Feature carry-forward

The Core-Mac feature target is the union of current SurroundCore behaviour and ControlMacV2 features/functions, including where supported:

- local and network library management
- playback sessions and independent rooms
- grouped/synchronised rooms
- Meridian/Sooloos discovery and control
- Sonos
- AirPlay
- Chromecast
- Bluetooth / local CoreAudio outputs
- OAAT/Core-Mac endpoints
- Plex as library input
- SMB/CIFS, NFS, USB and cloud storage configuration
- simple and advanced cloud configuration
- provider bridges and streaming-provider UI
- Internet radio and podcasts
- MiniDisc ingest/control modules
- optical disc ingest / authoring on macOS where enabled
- quality/capability negotiation with no silent downmix
- Library, Now Playing, Devices, Configuration and Remote UI
- customisable Meridian-style remote actions/macros

## Packaging

Core-Mac must not require Homebrew, MacPorts, Docker, Rosetta, Mono, Wine, Electron, Qt or a separately installed Python runtime for normal use.

Native dependencies included in the app must contain both `arm64` and `x86_64` slices, or be architecture-neutral resources/scripts.

The release pipeline must verify every Mach-O executable/library inside the app before packaging.

## Development branch

Initial macOS universalisation work lives on:

`core-mac-universal`

The working Linux SurroundCore `main` remains protected from platform-port churn until Core-Mac reaches feature parity and passes native Mac testing.
