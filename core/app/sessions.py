import threading
import time

_lock = threading.RLock()
_sessions = {}


def _queue_item(media):
    meta = media.get("metadata") or {}
    return {
        "media_id": media.get("id"), "duration": float(media.get("duration") or 0.0),
        "title": meta.get("title") or "", "artist": meta.get("artist") or meta.get("album_artist") or "",
        "album": meta.get("album") or "", "codec": media.get("codec") or "",
        "channels": int(media.get("channels") or 0), "channel_layout": media.get("channel_layout") or "",
        "sample_rate": int(media.get("sample_rate") or 0), "bit_depth": int(media.get("bit_depth") or 0),
    }


def _position(s):
    base = float(s.get("position_seconds") or 0.0)
    if s.get("state") != "playing": return base
    return max(0.0, base + time.monotonic() - float(s.get("clock_started") or time.monotonic()))


def _locate(queue, programme_position):
    pos = max(0.0, float(programme_position or 0.0))
    elapsed = 0.0
    for index, item in enumerate(queue):
        duration = float(item.get("duration") or 0.0)
        if duration <= 0 or pos < elapsed + duration or index == len(queue) - 1:
            return index, max(0.0, pos - elapsed)
        elapsed += duration
    return max(0, len(queue)-1), 0.0


def start_library(endpoint_id, media_items, source="library", position_seconds=0.0, plan=None):
    queue = [_queue_item(x) for x in media_items]
    with _lock:
        _sessions[endpoint_id] = {
            "endpoint_id": endpoint_id, "source": source, "state": "playing", "queue": queue,
            "position_seconds": float(position_seconds or 0.0), "clock_started": time.monotonic(),
            "started_at": time.time(), "plan": plan or {},
        }
        return view(endpoint_id)


def start_external(endpoint_id, source, now_playing=None, plan=None):
    item = dict(now_playing or {})
    item.setdefault("duration", 0.0)
    with _lock:
        _sessions[endpoint_id] = {
            "endpoint_id": endpoint_id, "source": source, "state": "playing", "queue": [item],
            "position_seconds": 0.0, "clock_started": time.monotonic(), "started_at": time.time(),
            "plan": plan or {},
        }
        return view(endpoint_id)


def set_state(endpoint_id, state):
    if state not in ("loading", "playing", "paused", "stopped"): raise ValueError("invalid playback state")
    with _lock:
        s = _sessions.get(endpoint_id)
        if not s: return None
        current = _position(s)
        s["position_seconds"] = current
        s["state"] = state
        s["clock_started"] = time.monotonic()
        return view(endpoint_id)


def seek(endpoint_id, programme_seconds):
    with _lock:
        s = _sessions.get(endpoint_id)
        if not s: return None
        s["position_seconds"] = max(0.0, float(programme_seconds or 0.0))
        s["clock_started"] = time.monotonic()
        return view(endpoint_id)


def clear(endpoint_id):
    with _lock:
        s = _sessions.get(endpoint_id)
        if not s: return None
        s["position_seconds"] = _position(s); s["state"] = "stopped"; s["clock_started"] = time.monotonic()
        return view(endpoint_id)


def view(endpoint_id):
    with _lock:
        s = _sessions.get(endpoint_id)
        if not s: return None
        queue = list(s.get("queue") or [])
        programme_pos = _position(s)
        index, local_pos = _locate(queue, programme_pos) if queue else (0, 0.0)
        current = queue[index] if queue else None
        total = sum(float(x.get("duration") or 0.0) for x in queue)
        remaining = max(0.0, total - programme_pos) if total > 0 else None
        state = s.get("state", "stopped")
        return {
            "endpoint_id": endpoint_id, "source": s.get("source"), "state": state,
            "now_playing": current, "queue": queue, "queue_index": index,
            "seek_position": local_pos, "programme_position": programme_pos,
            "queue_items_remaining": max(0, len(queue)-index-1), "queue_time_remaining": remaining,
            "is_previous_allowed": bool(queue and (index > 0 or local_pos > 3)),
            "is_next_allowed": bool(queue and index < len(queue)-1),
            "is_pause_allowed": state == "playing", "is_play_allowed": state in ("paused", "stopped"),
            "is_seek_allowed": bool(current and float(current.get("duration") or 0.0) > 0),
            "plan": s.get("plan") or {}, "started_at": s.get("started_at"),
        }


def list_views():
    with _lock: return [view(k) for k in list(_sessions)]


def control_target(endpoint_id, control):
    current = view(endpoint_id)
    if not current or not current.get("queue"):
        return None
    queue = current["queue"]; index = int(current.get("queue_index") or 0)
    local = float(current.get("seek_position") or 0.0)
    starts=[]; total=0.0
    for item in queue:
        starts.append(total); total += float(item.get("duration") or 0.0)
    if control == "next":
        if index >= len(queue)-1: return None
        return starts[index+1]
    if control == "previous":
        if local > 3.0: return starts[index]
        return starts[max(0,index-1)]
    return None
