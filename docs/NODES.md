# SurroundCore node model

SurroundCore is always managed through the Primary Core interface/API. Users do not directly browse, mount or administer internal storage transports.

## Roles

**Primary Core** owns the canonical catalogue, users, metadata/provider settings, presentation/formatting, storage policy, playback policy, node membership and update policy.

**Replica Core** continuously receives a recoverable copy of Primary state and can keep normal playback/control running when the Primary is offline. A Replica never promotes itself and never becomes Primary automatically.

**Extended Storage** is a lightweight managed node that contributes capacity to the Primary. Its internal transport may be NFS, but that is implementation plumbing only. The user sees one managed storage node in SurroundCore Control.

**Render / OAAT node** provides playback/render capability. It reports codecs, PCM formats, channel layouts, latency, hardware and health to the Primary. It does not own the canonical catalogue.

**OAAT endpoint** uses Open Advanced Audio Transport. OAAT is independent of discovery and any future secure remote carrier.

## Primary failure and recovery

If the Primary goes offline, Replica Cores continue serving from their latest replicated state. There is no promotion, election or split-brain mode.

Normal playback, queue control, reachable storage and cached media continue. If media lives only on storage physically attached to the failed Primary, the Replica reports that Primary-hosted storage as unavailable while keeping other reachable/cached media usable.
A replacement Primary is installed as a Primary and chooses **Recover existing system from Replica**. It authenticates to a selected Replica, restores the replicated system identity/state, then resumes authority. The Replica remains a Replica throughout.

Replicas may journal transient outage activity such as queue position/history, but structural configuration remains Primary-owned so an old/rebuilt Primary cannot create conflicting authority.

## Replica media cache

If a Replica has no dedicated music store, SurroundCore defaults its media cache to a soft maximum of **33% of the usable data volume**. This is a quota, not a fixed partition.

The Primary UI may change that percentage, pin selected media, and inspect cache health. Non-pinned cache uses LRU eviction. Cached original media remains bit-identical to the source.

## Data and playback path

Original media bytes live on storage selected through the Primary interface. The Primary/Replica may read them from Primary disks or Extended Storage over an internal read-only transport such as NFS.

Playback remains Core-controlled. If the destination supports the original encoded stream, SurroundCore may pass it unchanged. If PCM is required, decoding preserves source sample rate, bit depth and channel layout unless the user explicitly allows conversion. OAAT carries native/PCM audio with timing and capability negotiation.

## Updates

Updates are explicit and Primary-controlled. The Nodes & Updates surface allows selection of the Primary, individual Replica/Extended Storage/render nodes, owned OAAT endpoints, or all compatible nodes.

Each node reports role, identity, current build, health and last update result. Nodes do not independently change UI, formatting or policy. No automatic cron updates; create rollback state where supported and verify health after restart.