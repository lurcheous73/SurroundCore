from dataclasses import dataclass, asdict
from typing import List, Optional

@dataclass
class Endpoint:
    id: str
    name: str
    kind: str
    channels: int
    channel_map: List[str]
    address: Optional[str] = None
    capabilities: Optional[dict] = None

    def dict(self):
        return asdict(self)

@dataclass
class Edition:
    path: str
    codec: str
    channels: int
    channel_layout: str
    sample_rate: int
    bit_depth: Optional[int]
    bitrate: Optional[int] = None
    duration: float = 0.0
    metadata: Optional[dict] = None

    def dict(self):
        return asdict(self)
