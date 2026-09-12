from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable
import json
import os
import threading


OPTIONAL_CAPABILITIES = (
    "apple-mobile",
    "android",
    "minidisc",
    "sonos",
    "bluos",
    "linn-openhome",
    "naim",
    "heos",
    "musiccast",
    "plex",
    "jellyfin",
    "emby",
    "lyrion",
    "kodi",
    # Reserved/dormant so RAAT/Roon can be switched on later without redesign.
    "roon",
)

# These remain live regardless of the optional UI/discovery selections.
ALWAYS_ON_CAPABILITIES = (
    "oaat",
    "coreend",
    "corestore",
    "local-audio",
    "usb-audio",
    "hdmi-audio",
    "airplay-raop-foundation",
    "google-cast-foundation",
    "upnp-dlna-foundation",
    "smb3",
    "nfs",
    "webdav",
    "local-filesystem",
    "usb-storage",
)


@dataclass(frozen=True)
class DeviceCapabilitySnapshot:
    enabled: Dict[str, bool]
    always_on: tuple[str, ...] = ALWAYS_ON_CAPABILITIES

    def allows(self, capability: str) -> bool:
        key = capability.strip().lower()
        if key in self.always_on:
            return True
        return self.enabled.get(key, True)


class DeviceCapabilityProfile:
    """Persistent Core-side capability profile.

    Optional device families may be disabled to avoid pointless discovery traffic and
    to let Control render a Devices page that matches the user's actual kit. Generic
    transports/storage and OAAT remain available at all times.
    """

    def __init__(self, path: str | None = None) -> None:
        configured = path or os.getenv("SURROUNDCORE_DEVICE_PROFILE")
        self.path = Path(configured or "/var/lib/surroundcore/device-capabilities.json")
        self._lock = threading.RLock()
        self._enabled: Dict[str, bool] = {name: (name != "roon") for name in OPTIONAL_CAPABILITIES}
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return
            values = payload.get("enabled", payload)
            if isinstance(values, dict):
                for name in OPTIONAL_CAPABILITIES:
                    if name in values:
                        self._enabled[name] = bool(values[name])

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps({"enabled": self._enabled}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.path)

    def snapshot(self) -> DeviceCapabilitySnapshot:
        with self._lock:
            return DeviceCapabilitySnapshot(enabled=dict(self._enabled))

    def update(self, changes: Dict[str, bool]) -> DeviceCapabilitySnapshot:
        with self._lock:
            for raw_name, value in changes.items():
                name = raw_name.strip().lower()
                if name not in OPTIONAL_CAPABILITIES:
                    raise ValueError(f"Unknown optional device capability: {raw_name}")
                self._enabled[name] = bool(value)
            self.save()
            return self.snapshot()

    def replace_enabled(self, enabled: Iterable[str]) -> DeviceCapabilitySnapshot:
        selected = {item.strip().lower() for item in enabled}
        unknown = selected.difference(OPTIONAL_CAPABILITIES)
        if unknown:
            raise ValueError(f"Unknown optional device capabilities: {sorted(unknown)}")
        with self._lock:
            self._enabled = {name: name in selected for name in OPTIONAL_CAPABILITIES}
            self.save()
            return self.snapshot()

    def allows(self, capability: str) -> bool:
        return self.snapshot().allows(capability)

    def as_dict(self) -> dict:
        snap = self.snapshot()
        return {
            "enabled": snap.enabled,
            "always_on": list(snap.always_on),
            "optional": [name for name in OPTIONAL_CAPABILITIES if name != "roon"],
            "reserved": ["roon"],
        }


device_capabilities = DeviceCapabilityProfile()
