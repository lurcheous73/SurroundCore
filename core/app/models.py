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
    duration: float

    def dict(self):
        return asdict(self)
