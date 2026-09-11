# SurroundCore node model

SurroundCore is always managed through the Primary Core interface/API. Users do not directly browse, mount or administer internal storage transports.

## Roles

**Primary Core** owns the canonical catalogue, users, queues, metadata/provider settings, presentation/formatting, storage policy, playback orchestration and update policy.

**Extended Storage** is a lightweight managed node that contributes capacity to a Primary Core. Its internal file transport may be NFS, but that is implementation plumbing only. The user sees it as an Extended Storage node in SurroundCore Control.

**Render / OAAT node** provides playback/render capability. It reports codecs, PCM formats, channel layouts, latency, hardware and health to the Primary. It does not own the canonical catalogue.

**OAAT endpoint** is an audio endpoint using Open Advanced Audio Transport. OAAT is independent of discovery and any future secure remote carrier.
## Data and playback path

Original media bytes live on storage selected through the Primary interface. The Primary may read them from its own disks or from Extended Storage over an internal read-only transport such as NFS.

Playback remains Core-controlled. If the destination supports the original encoded stream, SurroundCore may pass it unchanged. If PCM is required, decoding must preserve the source sample rate, bit depth and channel layout unless the user explicitly allows conversion. OAAT carries native/PCM audio to capable render nodes with timing and capability negotiation.

## Updates

Updates are explicit and Primary-controlled. The Nodes & Updates surface must allow selection of the Primary, individual Extended Storage/render nodes, any owned OAAT endpoint, or all compatible nodes.

Each node reports role, identity, current build, health and last update result. The Primary owns the desired build and formatting/policy. Nodes do not independently change UI or policy.

No automatic cron updates. Where platform support exists, create a snapshot/rollback point before applying an update and verify health after restart.