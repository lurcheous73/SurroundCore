import hashlib
import hmac
import os
import re
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException, Query, Request
from . import recordings

router = APIRouter(prefix="/api/v1/controlmac", tags=["ControlMac"])
UPLOAD_ROOT = Path(os.getenv("SURROUNDCORE_DATA", "/data")) / "controlmac-imports"


def _require_token(authorization: str | None):
    expected = os.getenv("SURROUNDCORE_TOKEN", "")
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, "Invalid SurroundCore token")


def _safe(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._ -]+", "_", value or "").strip(" .")
    return value[:180] or fallback


@router.put("/upload")
async def upload(
    request: Request,
    artist: str = Query("Unknown Artist"), album: str = Query("Unknown Album"),
    filename: str = Query("audio.flac"), edition: str = Query("Standard edition"),
    media_type: str = Query("file"), source_format: str | None = Query(None),
    release_year: int | None = Query(None), remaster_year: int | None = Query(None),
    source_id: str | None = Query(None), source_serial: str | None = Query(None),
    provenance: str = Query("controlmac_import"), authorization: str | None = Header(None),
    x_content_sha256: str | None = Header(None, alias="X-Content-SHA256"),
):
    _require_token(authorization)
    expected = (x_content_sha256 or "").lower().strip()
    if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise HTTPException(400, "Invalid X-Content-SHA256")
    folder = UPLOAD_ROOT / _safe(artist, "Unknown Artist") / _safe(album, "Unknown Album") / _safe(edition, "Standard edition")
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / _safe(Path(filename).name, "audio.flac")
    tmp = dst.with_name(dst.name + ".part")
    digest = hashlib.sha256(); size = 0
    try:
        with tmp.open("wb") as out:
            async for chunk in request.stream():
                if not chunk: continue
                digest.update(chunk); size += len(chunk); out.write(chunk)
        actual = digest.hexdigest()
        if expected and not hmac.compare_digest(expected, actual):
            tmp.unlink(missing_ok=True); raise HTTPException(422, "Uploaded checksum does not match source")
        already = dst.is_file() and hashlib.sha256(dst.read_bytes()).hexdigest() == actual
        if already: tmp.unlink(missing_ok=True)
        else: tmp.replace(dst)
        meta = {"source_format": source_format, "release_year": release_year, "remaster_year": remaster_year}
        media = recordings.import_recording(str(dst), media_type, dst.stem, provenance, False, artist, album, edition, source_id, source_serial, actual)
        return {"ok": True, "sha256": actual, "already_present": already, "bytes": size, "item": media or {"path": str(dst)}, "provenance": meta}
    except HTTPException: raise
    except Exception as exc:
        tmp.unlink(missing_ok=True); raise HTTPException(500, f"ControlMac upload failed: {exc}")
