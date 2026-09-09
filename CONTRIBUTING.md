# Contributing to SurroundCore

Thank you for making music hardware live longer.

## Good contributions

- New renderer/transport drivers
- New lossless formats or source probes
- Streaming-provider adapters using authorised/public interfaces
- Better metadata and artwork handling
- Accessibility and controller improvements
- Tests against real hardware, especially older hardware vendors no longer support
- Documentation of quirks, capabilities and interoperability

## Ground rules

- Never silently downsample or downmix. Endpoint conversion must be explicit in the playback plan.
- Never claim a device/format is supported until the relevant path has been tested. Use the project support states.
- Do not commit credentials, service tokens, DRM material or proprietary binaries.
- Keep provider integrations within their published/authorised interfaces and terms.
- Prefer small drivers behind stable contracts over vendor-specific logic spread through the Core.
- Add regression tests with bug fixes.

## Development

Supported server platform is Debian 13. Work in a feature branch, run the test suite, and open a pull request describing hardware/software used for verification.

See `docs/PHILOSOPHY.md` and the transport/provider documentation before adding a new integration.
