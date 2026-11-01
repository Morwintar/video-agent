"""Read-only media inventory and scoped preview/skill-authoring API."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

router = APIRouter()
FOLDERS = (
    ("waiting", "Waiting"),
    ("references", "References"),
    ("finish", "Finish"),
)
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SKILL_DESCRIPTION = 1024
_MAX_SKILL_BODY = 32_000
_MAX_REFERENCE_URL_LENGTH = 2_048
_MAX_REFERENCE_NOTE_LENGTH = 2_000
_MAX_REFERENCES = 200
_MAX_CONTENT_DRAFTS = 1_000
_MAX_CONTENT_STORE_SIZE = 16_000_000
_MAX_VIDEO_MOMENTS = 5_000
_MAX_VIDEO_MOMENTS_PER_SOURCE = 500
_MAX_MOMENTS_STORE_SIZE = 8_000_000
_MAX_MOMENT_SECONDS = 24 * 60 * 60
_MAX_MOMENT_NOTE_LENGTH = 500
_CONTENT_TEXT_LIMITS = {"title": 200, "concept": 5_000, "hook": 2_000, "script": 20_000, "notes": 4_000}
_MAX_RELATIVE_VIDEO_PATH_LENGTH = 1_024
_MAX_RELATIVE_VIDEO_PATH_DEPTH = 16
_MAX_WAITING_SCAN_DEPTH = 16
_MAX_WAITING_SCAN_ENTRIES = 20_000
_REFERENCE_LOCK = threading.Lock()
_CONTENT_LOCK = threading.Lock()
_THUMBNAIL_GENERATION_LOCK = threading.BoundedSemaphore(2)
_MAX_THUMBNAIL_BYTES = 2_000_000
_THUMBNAIL_CACHE_ENTRIES = 128
_STREAM_TICKET_TTL_SECONDS = 900
_MAX_STREAM_TICKETS = 256
_STREAM_TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_STREAM_TICKET_LOCK = threading.Lock()
_STREAM_TICKETS: dict[str, dict[str, Any]] = {}
_REFERENCE_OBSERVATION_ITEMS = (
    ("hook", "Accroche et moment fort dans les premières secondes."),
    ("pacing", "Rythme, durée des plans et transitions."),
    ("framing", "Cadrage vertical sans couper le gameplay ni le HUD."),
    ("captions", "Texte à l’écran; sous-titres seulement si les paroles sont confirmées à l’écoute."),
    ("audio", "Voix, musique et effets sonores; ne pas inventer de paroles."),
    ("payoff", "Montée de tension, payoff et fin du clip."),
)


def _reference_observation_checklist() -> list[dict[str, str]]:
    return [{"id": item_id, "label": label} for item_id, label in _REFERENCE_OBSERVATION_ITEMS]


def _validate_observation_checklist(raw_checklist: Any) -> list[dict[str, str]]:
    if not isinstance(raw_checklist, list) or not 1 <= len(raw_checklist) <= 64:
        raise ValueError("invalid observation checklist")
    validated: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for item in raw_checklist:
        if not isinstance(item, dict):
            raise ValueError("invalid observation checklist item")
        item_id = item.get("id")
        label = item.get("label")
        if (
            not isinstance(item_id, str)
            or not item_id
            or len(item_id) > 64
            or not item_id.isascii()
            or not item_id[0].isalnum()
            or not all(char.isalnum() or char in "_-" for char in item_id)
            or item_id in seen_ids
            or not isinstance(label, str)
            or not label.strip()
            or len(label) > 500
            or any(ord(char) < 32 or ord(char) == 127 for char in label)
        ):
            raise ValueError("invalid observation checklist item")
        seen_ids.add(item_id)
        validated.append({"id": item_id, "label": label.strip()})
    return validated


def _hermes_home() -> Path:
    """Resolve the active Hermes home without hard-coding the plugin's source tree."""
    hermes_home = os.environ.get("HERMES_HOME")
    return Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"


def _workspace_root() -> Path | None:
    """Return the configured absolute mountpoint or this plugin's default workspace."""
    configured = os.environ.get("VIDEO_AGENT_WORKSPACE")
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_absolute() else None
    return _plugin_root() / "workspace"


def _active_workspace_root() -> Path | None:
    """Use the mountpoint only when all three expected media folders are present."""
    candidate = _workspace_root()
    if candidate is None:
        return None
    try:
        if candidate.is_symlink():
            return None
        resolved_root = candidate.resolve(strict=True)
        if not resolved_root.is_dir():
            return None
        for label in ("Waiting", "References", "Finish"):
            directory = resolved_root / label
            if directory.is_symlink() or not directory.is_dir():
                return None
            if directory.resolve(strict=True).parent != resolved_root:
                return None
        return resolved_root
    except OSError:
        return None


def _references_home() -> Path:
    return _hermes_home()


def _validate_reference_url(raw_url: Any) -> dict[str, str]:
    """Normalize and allowlist a public TikTok or YouTube HTTPS video URL."""
    if not isinstance(raw_url, str):
        raise ValueError("Le lien doit être une URL TikTok ou YouTube en HTTPS.")
    url = raw_url.strip()
    if not url or len(url) > _MAX_REFERENCE_URL_LENGTH or any(ord(char) < 32 for char in url) or "\\" in url:
        raise ValueError("Le lien doit être une URL TikTok ou YouTube valide.")

    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold().rstrip(".")
        port = parts.port
    except ValueError as exc:
        raise ValueError("Le lien doit être une URL TikTok ou YouTube valide.") from exc

    if parts.scheme.casefold() != "https" or not host or parts.username or parts.password:
        raise ValueError("Le lien doit être une URL TikTok ou YouTube en HTTPS.")
    if port not in {None, 443} or parts.path in {"", "/"}:
        raise ValueError("Le lien doit pointer vers une vidéo TikTok ou YouTube.")

    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        platform = "YouTube"
    elif host == "tiktok.com" or host.endswith(".tiktok.com"):
        platform = "TikTok"
    else:
        raise ValueError("Seuls les liens YouTube et TikTok sont acceptés.")

    normalized = urlunsplit(("https", host, parts.path, parts.query, parts.fragment))
    return {"url": normalized, "platform": platform}


class _ReferenceConflictError(Exception):
    """Raised when a reference limit or request conflicts with stored state."""


def _state_directory(home: Path, *, create: bool = False) -> Path | None:
    """Resolve durable, profile-local plugin data while refusing symlink escapes."""
    try:
        resolved_home = home.expanduser().resolve(strict=True)
        state_dir = resolved_home / "state"
        if state_dir.is_symlink():
            raise OSError("unsafe state directory")
        if create:
            state_dir.mkdir(mode=0o700, exist_ok=True)
        elif not state_dir.exists():
            return None
        resolved_state = state_dir.resolve(strict=True)
        if resolved_state.parent != resolved_home or not resolved_state.is_dir():
            raise OSError("unsafe state directory")

        plugin_dir = resolved_state / "waiting-studio"
        if plugin_dir.is_symlink():
            raise OSError("unsafe plugin state directory")
        if create:
            plugin_dir.mkdir(mode=0o700, exist_ok=True)
        elif not plugin_dir.exists():
            return None
        resolved_plugin_dir = plugin_dir.resolve(strict=True)
        if resolved_plugin_dir.parent != resolved_state or not resolved_plugin_dir.is_dir():
            raise OSError("unsafe plugin state directory")
        return resolved_plugin_dir
    except OSError:
        raise


def _validate_reference_note(raw_note: Any) -> str:
    if raw_note is None:
        return ""
    if not isinstance(raw_note, str):
        raise ValueError("La note doit être du texte.")
    note = raw_note.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(note) > _MAX_REFERENCE_NOTE_LENGTH or any(
        ord(char) < 32 and char not in {"\n", "\t"} for char in note
    ) or "\x7f" in note:
        raise ValueError("La note doit contenir au plus 2000 caractères et aucun caractère de contrôle.")
    try:
        note.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("La note doit être encodée en UTF-8 valide.") from exc
    return note


def _reference_store_directory(home: Path, *, create: bool = False) -> Path | None:
    """Store reference links with mounted media, or keep the historical state directory."""
    workspace = _active_workspace_root()
    if workspace is None:
        return _state_directory(home, create=create)

    references_dir = workspace / "References"
    if references_dir.is_symlink():
        raise OSError("unsafe references directory")
    resolved_references = references_dir.resolve(strict=True)
    if resolved_references.parent != workspace or not resolved_references.is_dir():
        raise OSError("unsafe references directory")
    return resolved_references


def _load_references(home: Path) -> list[dict[str, Any]]:
    directory = _reference_store_directory(home)
    if directory is None:
        return []
    workspace = _active_workspace_root()
    store = directory / ("links.json" if workspace is not None else "references.json")
    if workspace is not None and not store.exists():
        legacy_directory = _state_directory(home)
        legacy_store = legacy_directory / "references.json" if legacy_directory is not None else None
        if legacy_store is not None and legacy_store.exists():
            store = legacy_store
    if store.is_symlink():
        raise OSError("unsafe reference store")
    if not store.exists():
        return []
    if not store.is_file() or store.stat().st_size > 1_000_000:
        raise OSError("invalid reference store")
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("unsupported store format")
        raw_references = payload.get("references")
        if not isinstance(raw_references, list) or len(raw_references) > _MAX_REFERENCES:
            raise ValueError("invalid reference list")
        references: list[dict[str, Any]] = []
        for item in raw_references:
            if not isinstance(item, dict):
                raise ValueError("invalid reference")
            reference_id = item.get("id")
            parsed_id = uuid.UUID(reference_id) if isinstance(reference_id, str) else None
            validated_url = _validate_reference_url(item.get("url"))
            note = _validate_reference_note(item.get("note", ""))
            observation_checklist = _validate_observation_checklist(
                item.get("observationChecklist", _reference_observation_checklist())
            )
            added_at = item.get("addedAt")
            if (
                parsed_id is None
                or str(parsed_id) != reference_id
                or validated_url["url"] != item.get("url")
                or validated_url["platform"] != item.get("platform")
                or not isinstance(added_at, str)
                or not added_at
                or len(added_at) > 64
            ):
                raise ValueError("invalid reference metadata")
            references.append({
                "id": reference_id,
                "platform": validated_url["platform"],
                "url": validated_url["url"],
                "note": note,
                "addedAt": added_at,
                "observationChecklist": observation_checklist,
            })
        return references
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise OSError("reference store could not be read") from exc


def _save_references(home: Path, references: list[dict[str, Any]]) -> None:
    workspace = _active_workspace_root()
    directory = _reference_store_directory(home, create=True)
    if directory is None:
        raise OSError("reference store is unavailable")
    store = directory / ("links.json" if workspace is not None else "references.json")
    if store.is_symlink():
        raise OSError("unsafe reference store")

    descriptor, temporary_name = tempfile.mkstemp(prefix=".references-", suffix=".tmp", dir=directory)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"version": 1, "references": references}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, store)
        os.chmod(store, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def _list_references(home: Path | None = None) -> list[dict[str, Any]]:
    with _REFERENCE_LOCK:
        return _load_references(home or _references_home())


def _add_reference(home: Path, raw_url: Any, raw_note: Any = "") -> dict[str, Any]:
    validated_url = _validate_reference_url(raw_url)
    note = _validate_reference_note(raw_note)
    with _REFERENCE_LOCK:
        references = _load_references(home)
        if len(references) >= _MAX_REFERENCES:
            raise _ReferenceConflictError("Le registre contient déjà le maximum de références.")
        reference = {
            "id": str(uuid.uuid4()),
            "platform": validated_url["platform"],
            "url": validated_url["url"],
            "note": note,
            "addedAt": datetime.now(timezone.utc).isoformat(),
            "observationChecklist": _reference_observation_checklist(),
        }
        references.append(reference)
        _save_references(home, references)
        return reference


def _delete_reference(home: Path, reference_id: str) -> bool:
    try:
        parsed_id = uuid.UUID(reference_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Identifiant de référence invalide.") from exc
    if str(parsed_id) != reference_id:
        raise ValueError("Identifiant de référence invalide.")

    with _REFERENCE_LOCK:
        references = _load_references(home)
        remaining = [item for item in references if item["id"] != reference_id]
        if len(remaining) == len(references):
            return False
        _save_references(home, remaining)
        return True


def _relative_video_parts(filename: str) -> tuple[str, ...] | None:
    """Validate a relative MP4 path supplied by the inventory or an API caller."""
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > _MAX_RELATIVE_VIDEO_PATH_LENGTH
        or filename.startswith("/")
        or "\\" in filename
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        return None
    parts = tuple(filename.split("/"))
    if not parts or len(parts) > _MAX_RELATIVE_VIDEO_PATH_DEPTH:
        return None
    try:
        if any(not part or part in {".", ".."} or len(part.encode("utf-8")) > 255 for part in parts):
            return None
    except UnicodeEncodeError:
        return None
    if Path(parts[-1]).suffix.casefold() != ".mp4":
        return None
    return parts


def _resolve_regular_relative_video(directory: Path, parts: tuple[str, ...]) -> Path | None:
    """Resolve a relative MP4 path while refusing symlinks in every segment."""
    current = directory
    try:
        for index, part in enumerate(parts):
            candidate = current / part
            if candidate.is_symlink():
                return None
            metadata = os.stat(candidate, follow_symlinks=False)
            is_final = index == len(parts) - 1
            if is_final and not stat.S_ISREG(metadata.st_mode):
                return None
            if not is_final and not stat.S_ISDIR(metadata.st_mode):
                return None
            resolved_candidate = candidate.resolve(strict=True)
            if resolved_candidate.parent != current:
                return None
            current = resolved_candidate
        return current
    except OSError:
        return None


def _resolve_video_in_folder(root: Path, folder_name: str, filename: str) -> Path | None:
    """Resolve one regular MP4 beneath an allowlisted folder without following symlinks."""
    parts = _relative_video_parts(filename)
    if parts is None:
        return None
    try:
        resolved_root = root.resolve(strict=True)
        media_dir = resolved_root / folder_name
        if media_dir.is_symlink():
            return None
        resolved_media_dir = media_dir.resolve(strict=True)
        if resolved_media_dir.parent != resolved_root or not resolved_media_dir.is_dir():
            return None
        return _resolve_regular_relative_video(resolved_media_dir, parts)
    except OSError:
        return None


def _resolve_waiting_video(root: Path, filename: str) -> Path | None:
    """Resolve a regular MP4 anywhere beneath Waiting without following symlinks."""
    return _resolve_video_in_folder(root, "Waiting", filename)


def _resolve_reference_video(root: Path, filename: str) -> Path | None:
    """Resolve a regular MP4 anywhere beneath References without following symlinks."""
    return _resolve_video_in_folder(root, "References", filename)


def _video_file_identity(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat()
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _stream_ticket_key(ticket_id: str) -> str | None:
    if not isinstance(ticket_id, str) or not _STREAM_TICKET_RE.fullmatch(ticket_id):
        return None
    return hashlib.sha256(ticket_id.encode("ascii")).hexdigest()


def _lookup_stream_ticket(ticket_id: str) -> tuple[dict[str, Any] | None, bool]:
    """Return an unexpired ticket and whether a known ticket expired."""
    key = _stream_ticket_key(ticket_id)
    if key is None:
        return None, False
    now = time.monotonic()
    with _STREAM_TICKET_LOCK:
        record = _STREAM_TICKETS.get(key)
        if record is None:
            return None, False
        if record["expiresAt"] <= now:
            _STREAM_TICKETS.pop(key, None)
            return None, True
        return dict(record), False


def _prune_stream_tickets(now: float) -> None:
    expired = [
        key for key, record in _STREAM_TICKETS.items()
        if record["expiresAt"] <= now
    ]
    for key in expired:
        _STREAM_TICKETS.pop(key, None)


def _resolve_stream_ticket_video(record: dict[str, Any]) -> Path | None:
    folder_id = record.get("folderId")
    filename = record.get("filename")
    if not isinstance(folder_id, str) or not isinstance(filename, str):
        return None
    folder_label = dict(FOLDERS).get(folder_id)
    if folder_label is None:
        return None
    media_root = _media_root()
    path = _resolve_video_in_folder(media_root, folder_label, filename)
    if path is None:
        return None
    try:
        if _video_file_identity(path) != record.get("fileIdentity"):
            return None
        compatibility_identity = record.get("compatibilityFileIdentity")
        if folder_id == "waiting" and compatibility_identity is not None:
            compatibility_path = _resolve_hvc1_compat_video(media_root, filename)
            if (
                compatibility_path is not None
                and _video_file_identity(compatibility_path) == compatibility_identity
            ):
                return compatibility_path
        return path
    except OSError:
        return None


def _resolve_hvc1_compat_video(root: Path, filename: str) -> Path | None:
    """Resolve a remuxed hvc1 copy only when its Waiting source still exists."""
    parts = _relative_video_parts(filename)
    if parts is None or _resolve_waiting_video(root, filename) is None:
        return None
    try:
        state_dir = _state_directory(_references_home())
        if state_dir is None:
            return None
        compat_dir = state_dir / "compat"
        if compat_dir.is_symlink() or not compat_dir.exists():
            return None
        resolved_compat = compat_dir.resolve(strict=True)
        if resolved_compat.parent != state_dir.resolve(strict=True) or not resolved_compat.is_dir():
            return None
        return _resolve_regular_relative_video(resolved_compat, parts)
    except OSError:
        return None


def _load_content_drafts(home: Path) -> list[dict[str, Any]]:
    directory = _state_directory(home)
    if directory is None:
        return []
    store = directory / "content.json"
    if store.is_symlink():
        raise OSError("unsafe content store")
    if not store.exists():
        return []
    if not store.is_file() or store.stat().st_size > _MAX_CONTENT_STORE_SIZE:
        raise OSError("invalid content store")

    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
        raw_drafts = payload.get("drafts") if isinstance(payload, dict) and payload.get("version") == 1 else None
        if not isinstance(raw_drafts, list) or len(raw_drafts) > _MAX_CONTENT_DRAFTS:
            raise ValueError("invalid draft list")
        drafts: list[dict[str, Any]] = []
        for item in raw_drafts:
            if not isinstance(item, dict):
                raise ValueError("invalid content draft")
            parsed_id = uuid.UUID(item.get("id", ""))
            filename = item.get("sourceFilename")
            if (
                str(parsed_id) != item.get("id")
                or _relative_video_parts(filename) is None
                or item.get("format") not in {"short", "video"}
                or not isinstance(item.get("createdAt"), str)
            ):
                raise ValueError("invalid content metadata")
            creation_context = item.get("creationContext")
            if not isinstance(creation_context, dict):
                raise ValueError("invalid creation context")
            context_references = creation_context.get("references")
            checklist = creation_context.get("observationChecklist")
            if not isinstance(context_references, list) or not isinstance(checklist, list):
                raise ValueError("invalid creation context")
            validated_references: list[dict[str, Any]] = []
            for reference in context_references:
                if not isinstance(reference, dict):
                    raise ValueError("invalid reference snapshot")
                reference_id = reference.get("id")
                parsed_reference_id = uuid.UUID(reference_id) if isinstance(reference_id, str) else None
                validated_url = _validate_reference_url(reference.get("url"))
                note = _validate_reference_note(reference.get("note", ""))
                reference_checklist = _validate_observation_checklist(reference.get("observationChecklist"))
                if (
                    parsed_reference_id is None
                    or str(parsed_reference_id) != reference_id
                    or reference.get("platform") != validated_url["platform"]
                    or validated_url["url"] != reference.get("url")
                ):
                    raise ValueError("invalid reference snapshot")
                validated_references.append({
                    "id": reference_id,
                    "platform": validated_url["platform"],
                    "url": validated_url["url"],
                    "note": note,
                    "observationChecklist": reference_checklist,
                })
            expected_checklist = _validate_observation_checklist(checklist)
            draft = {
                "id": str(parsed_id),
                "sourceFilename": filename,
                "format": item["format"],
                "title": _validate_content_text(item.get("title"), "title", required=True),
                "concept": _validate_content_text(item.get("concept"), "concept", required=True),
                "hook": _validate_content_text(item.get("hook", ""), "hook"),
                "script": _validate_content_text(item.get("script", ""), "script"),
                "notes": _validate_content_text(item.get("notes", ""), "notes"),
                "createdAt": item["createdAt"],
                "creationContext": {
                    "references": validated_references,
                    "observationChecklist": expected_checklist,
                },
            }
            drafts.append(draft)
        return drafts
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
        raise OSError("content store could not be read") from exc


def _validate_content_text(raw_text: Any, field_name: str, *, required: bool = False) -> str:
    if raw_text is None and not required:
        return ""
    if not isinstance(raw_text, str):
        raise ValueError(f"{field_name} must be text")
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if (required and not text) or len(text) > _CONTENT_TEXT_LIMITS[field_name] or any(
        ord(char) < 32 and char not in {"\n", "\t"} for char in text
    ) or "\x7f" in text:
        raise ValueError(f"{field_name} is empty, too long, or contains control characters")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8") from exc
    return text


def _save_content_drafts(home: Path, drafts: list[dict[str, Any]]) -> None:
    directory = _state_directory(home, create=True)
    if directory is None:
        raise OSError("content store is unavailable")
    store = directory / "content.json"
    if store.is_symlink():
        raise OSError("unsafe content store")
    serialized = json.dumps({"version": 1, "drafts": drafts}, ensure_ascii=False, indent=2) + "\n"
    if len(serialized.encode("utf-8")) > _MAX_CONTENT_STORE_SIZE:
        raise _ContentConflictError("Le registre de contenu est plein.")

    descriptor, temporary_name = tempfile.mkstemp(prefix=".content-", suffix=".tmp", dir=directory)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, store)
        os.chmod(store, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def _list_content_drafts(home: Path | None = None) -> list[dict[str, Any]]:
    with _CONTENT_LOCK:
        return _load_content_drafts(home or _references_home())


def _content_creation_context(home: Path) -> dict[str, Any]:
    references = [
        {
            "id": reference["id"],
            "platform": reference["platform"],
            "url": reference["url"],
            "note": reference["note"],
            "observationChecklist": reference["observationChecklist"],
        }
        for reference in _list_references(home)
    ]
    return {
        "references": references,
        "observationChecklist": _reference_observation_checklist(),
    }


class _ContentConflictError(Exception):
    """Raised when the local content-draft store reaches its configured limit."""


def _create_content_draft(
    media_root: Path,
    home: Path,
    source_filename: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if _resolve_waiting_video(media_root, source_filename) is None:
        raise FileNotFoundError("La rediffusion n’est pas un MP4 régulier de Waiting.")
    if not isinstance(payload, dict):
        raise ValueError("Le contenu doit être un objet JSON.")
    content_format = payload.get("format")
    if content_format not in {"short", "video"}:
        raise ValueError("Le format doit être short ou video.")
    content = {
        field_name: _validate_content_text(payload.get(field_name, ""), field_name, required=field_name in {"title", "concept"})
        for field_name in _CONTENT_TEXT_LIMITS
    }

    with _CONTENT_LOCK:
        drafts = _load_content_drafts(home)
        if len(drafts) >= _MAX_CONTENT_DRAFTS:
            raise _ContentConflictError("Le registre contient déjà le maximum de brouillons.")
        creation_context = _content_creation_context(home)
        draft = {
            "id": str(uuid.uuid4()),
            "sourceFilename": source_filename,
            "format": content_format,
            **content,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "creationContext": creation_context,
        }
        drafts.append(draft)
        _save_content_drafts(home, drafts)
        return draft


def _get_content_workspace(media_root: Path, home: Path, source_filename: str) -> dict[str, Any]:
    if _resolve_waiting_video(media_root, source_filename) is None:
        raise FileNotFoundError("La rediffusion n’est pas un MP4 régulier de Waiting.")
    drafts = _list_content_drafts(home)
    with _CONTENT_LOCK:
        moments = _load_video_moments(home)
    source_stem = Path(source_filename.rsplit("/", 1)[-1]).stem.casefold()
    finish_inventory = _scan_folder(media_root, "finish", "Finish")
    clips = [
        {"name": video["name"], "sizeBytes": video["sizeBytes"]}
        for video in finish_inventory.get("videos", [])
        if video["name"].casefold().startswith(f"{source_stem}_")
    ]
    return {
        "sourceFilename": source_filename,
        "items": [draft for draft in drafts if draft["sourceFilename"] == source_filename],
        "moments": [moment for moment in moments if moment["sourceFilename"] == source_filename],
        "clips": clips,
        "clipsTruncated": finish_inventory.get("truncated", False),
        "references": _list_references(home),
        "observationChecklist": _reference_observation_checklist(),
    }


class _MomentConflictError(Exception):
    """Raised when the local video-moment store reaches its configured limit."""


def _validate_moment_time(raw_time: Any) -> float:
    if isinstance(raw_time, bool) or not isinstance(raw_time, (int, float)):
        raise ValueError("Le timecode doit être un nombre valide.")
    try:
        time_seconds = float(raw_time)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Le timecode doit être un nombre valide.") from exc
    if not math.isfinite(time_seconds) or not 0 <= time_seconds <= _MAX_MOMENT_SECONDS:
        raise ValueError("Le timecode doit être compris entre 0 et 24 heures.")
    return round(time_seconds, 3)


def _load_video_moments(home: Path) -> list[dict[str, Any]]:
    directory = _state_directory(home)
    if directory is None:
        return []
    store = directory / "moments.json"
    if store.is_symlink():
        raise OSError("unsafe moments store")
    if not store.exists():
        return []
    if not store.is_file() or store.stat().st_size > _MAX_MOMENTS_STORE_SIZE:
        raise OSError("invalid moments store")

    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
        raw_moments = payload.get("moments") if isinstance(payload, dict) and payload.get("version") == 1 else None
        if not isinstance(raw_moments, list) or len(raw_moments) > _MAX_VIDEO_MOMENTS:
            raise ValueError("invalid moment list")
        moments: list[dict[str, Any]] = []
        per_source: dict[str, int] = {}
        for item in raw_moments:
            if not isinstance(item, dict):
                raise ValueError("invalid moment")
            moment_id = item.get("id")
            parsed_id = uuid.UUID(moment_id) if isinstance(moment_id, str) else None
            filename = item.get("sourceFilename")
            raw_time = item.get("timeSeconds")
            time_seconds = _validate_moment_time(raw_time)
            if (
                parsed_id is None
                or str(parsed_id) != moment_id
                or _relative_video_parts(filename) is None
                or item.get("targetFormat") not in {"short", "video", "both"}
                or not isinstance(item.get("createdAt"), str)
            ):
                raise ValueError("invalid moment metadata")
            note = _validate_content_text(item.get("note"), "hook", required=True)
            if len(note) > _MAX_MOMENT_NOTE_LENGTH:
                raise ValueError("moment note is too long")
            per_source[filename] = per_source.get(filename, 0) + 1
            if per_source[filename] > _MAX_VIDEO_MOMENTS_PER_SOURCE:
                raise ValueError("too many moments for one source")
            moments.append({
                "id": moment_id,
                "sourceFilename": filename,
                "timeSeconds": time_seconds,
                "note": note,
                "targetFormat": item["targetFormat"],
                "createdAt": item["createdAt"],
            })
        return moments
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
        raise OSError("moments store could not be read") from exc


def _save_video_moments(home: Path, moments: list[dict[str, Any]]) -> None:
    directory = _state_directory(home, create=True)
    if directory is None:
        raise OSError("moments store is unavailable")
    store = directory / "moments.json"
    if store.is_symlink():
        raise OSError("unsafe moments store")
    serialized = json.dumps({"version": 1, "moments": moments}, ensure_ascii=False, indent=2) + chr(10)
    if len(serialized.encode("utf-8")) > _MAX_MOMENTS_STORE_SIZE:
        raise _MomentConflictError("Le registre de moments est plein.")

    descriptor, temporary_name = tempfile.mkstemp(prefix=".moments-", suffix=".tmp", dir=directory)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, store)
        os.chmod(store, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def _create_video_moment(
    media_root: Path,
    home: Path,
    source_filename: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if _resolve_waiting_video(media_root, source_filename) is None:
        raise FileNotFoundError("La rediffusion n’est pas un MP4 régulier de Waiting.")
    if not isinstance(payload, dict):
        raise ValueError("Le moment doit être un objet JSON.")
    time_seconds = _validate_moment_time(payload.get("timeSeconds"))
    note = _validate_content_text(payload.get("note"), "hook", required=True)
    if len(note) > _MAX_MOMENT_NOTE_LENGTH:
        raise ValueError("La note du moment est limitée à 500 caractères.")
    target_format = payload.get("targetFormat")
    if not isinstance(target_format, str) or target_format not in {"short", "video", "both"}:
        raise ValueError("La cible doit être short, video ou both.")

    with _CONTENT_LOCK:
        moments = _load_video_moments(home)
        if len(moments) >= _MAX_VIDEO_MOMENTS:
            raise _MomentConflictError("Le registre contient déjà le maximum de moments.")
        if sum(moment["sourceFilename"] == source_filename for moment in moments) >= _MAX_VIDEO_MOMENTS_PER_SOURCE:
            raise _MomentConflictError("Cette rediffusion contient déjà le maximum de moments.")
        moment = {
            "id": str(uuid.uuid4()),
            "sourceFilename": source_filename,
            "timeSeconds": time_seconds,
            "note": note,
            "targetFormat": target_format,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        moments.append(moment)
        _save_video_moments(home, moments)
        return moment


def _media_root() -> Path:
    """Prefer the unified mountpoint, preserving the existing home path as a fallback."""
    workspace = _active_workspace_root()
    if workspace is not None:
        return workspace
    return _hermes_home() / "inbox" / "stream-test"


def _plugin_root() -> Path:
    """Return this plugin's root, keeping custom skills portable with the plugin."""
    return Path(__file__).resolve().parent.parent


def _scan_folder(root: Path, folder_id: str, label: str) -> dict[str, Any]:
    """List regular MP4s, recursing only within Waiting and never following symlinks."""
    unavailable: dict[str, Any] = {
        "id": folder_id,
        "label": label,
        "available": False,
        "count": 0,
        "videos": [],
    }

    try:
        resolved_root = root.resolve(strict=True)
        directory = resolved_root / label
        if directory.is_symlink():
            return unavailable

        resolved_directory = directory.resolve(strict=True)
        if resolved_directory.parent != resolved_root or not resolved_directory.is_dir():
            return unavailable

        videos: list[dict[str, Any]] = []
        entries_seen = 0
        truncated = False
        recurse = (
            folder_id in {"waiting", "references", "finish"}
            and label in {"Waiting", "References", "Finish"}
        )

        def scan_directory(current: Path, relative_parts: tuple[str, ...], depth: int) -> None:
            nonlocal entries_seen, truncated
            try:
                with os.scandir(current) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name.casefold())
            except OSError:
                return

            for entry in entries:
                if entries_seen >= _MAX_WAITING_SCAN_ENTRIES:
                    truncated = True
                    return
                entries_seen += 1
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if recurse and depth < _MAX_WAITING_SCAN_DEPTH:
                            child = Path(entry.path)
                            resolved_child = child.resolve(strict=True)
                            if resolved_child.parent == current:
                                scan_directory(resolved_child, (*relative_parts, entry.name), depth + 1)
                        continue
                    if Path(entry.name).suffix.casefold() != ".mp4" or not entry.is_file(follow_symlinks=False):
                        continue
                    name = "/".join((*relative_parts, entry.name))
                    if _relative_video_parts(name) is None:
                        continue
                    size_bytes = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    # Entries can disappear or change type while the inventory is read.
                    continue
                videos.append({"name": name, "sizeBytes": size_bytes})

        scan_directory(resolved_directory, (), 0)
        videos.sort(key=lambda video: video["name"].casefold())
        return {
            "id": folder_id,
            "label": label,
            "available": True,
            "count": len(videos),
            "videos": videos,
            "truncated": truncated,
        }
    except OSError:
        # Do not expose filesystem paths or exception details to the browser.
        return unavailable


def _resolve_finish_video(root: Path, filename: str) -> Path | None:
    """Resolve a regular MP4 anywhere beneath Finish without following symlinks."""
    return _resolve_video_in_folder(root, "Finish", filename)


@router.get("/videos")
def list_videos() -> dict[str, Any]:
    """Return the three allowlisted media folders; no paths or media URLs are exposed."""
    root = _media_root()
    return {
        "readOnly": True,
        "folders": [_scan_folder(root, folder_id, label) for folder_id, label in FOLDERS],
    }


@router.get("/videos/finish/{filename:path}")
def get_finished_video(filename: str) -> FileResponse:
    """Stream one allowlisted Finish MP4 with byte-range support for seeking."""
    path = _resolve_finish_video(_media_root(), filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        stat_result=path.stat(),
        content_disposition_type="inline",
    )


@router.get("/videos/references/{filename:path}")
def get_reference_video(filename: str) -> FileResponse:
    """Stream a local reference MP4 with byte-range support for seeking."""
    path = _resolve_reference_video(_media_root(), filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        stat_result=path.stat(),
        content_disposition_type="inline",
    )


@lru_cache(maxsize=_THUMBNAIL_CACHE_ENTRIES)
def _generate_waiting_thumbnail(source_path: str, modified_ns: int, size_bytes: int) -> bytes:
    """Extract one small JPEG on demand; cache a bounded number in process memory."""
    del modified_ns, size_bytes  # Included in the cache key so changed sources invalidate old frames.
    if not _THUMBNAIL_GENERATION_LOCK.acquire(blocking=False):
        raise RuntimeError("thumbnail generation is busy")
    try:
        for seek_time in ("00:00:10", "00:00:00"):
            try:
                result = subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        seek_time,
                        "-i",
                        source_path,
                        "-an",
                        "-sn",
                        "-dn",
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=320:180:force_original_aspect_ratio=decrease",
                        "-f",
                        "image2pipe",
                        "-vcodec",
                        "mjpeg",
                        "-q:v",
                        "5",
                        "pipe:1",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=15,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("thumbnail generation timed out") from exc
            image_bytes = result.stdout
            if (
                result.returncode == 0
                and 4 <= len(image_bytes) <= _MAX_THUMBNAIL_BYTES
                and image_bytes.startswith(b"\xff\xd8")
                and image_bytes.endswith(b"\xff\xd9")
            ):
                return image_bytes
        raise RuntimeError("thumbnail could not be generated")
    finally:
        _THUMBNAIL_GENERATION_LOCK.release()


@router.get("/videos/waiting-thumbnails/{filename:path}")
def get_waiting_thumbnail(filename: str) -> Response:
    """Return one authenticated JPEG preview for a regular MP4 beneath Waiting."""
    source_path = _resolve_waiting_video(_media_root(), filename)
    if source_path is None:
        raise HTTPException(status_code=404, detail="Video not found")
    try:
        metadata = source_path.stat()
        image_bytes = _generate_waiting_thumbnail(
            str(source_path),
            metadata.st_mtime_ns,
            metadata.st_size,
        )
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Thumbnail unavailable") from exc
    return Response(
        content=image_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/videos/waiting/{filename:path}")
def get_waiting_video(filename: str, codec: str = "source") -> FileResponse:
    """Serve a Waiting MP4, optionally preferring its safe hvc1 remux for Safari."""
    media_root = _media_root()
    source_path = _resolve_waiting_video(media_root, filename)
    if source_path is None or codec not in {"source", "auto"}:
        raise HTTPException(status_code=404, detail="Video not found")
    path = source_path
    if codec == "auto":
        path = _resolve_hvc1_compat_video(media_root, filename) or source_path
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=source_path.name,
        stat_result=path.stat(),
        content_disposition_type="inline",
    )


@router.post("/video-tickets", status_code=201)
def create_video_ticket(payload: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
    """Mint a short-lived, single-file playback capability for the Desktop player."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    folder_id = payload.get("folderId")
    folder_label = dict(FOLDERS).get(folder_id) if isinstance(folder_id, str) else None
    filename = payload.get("filename")
    if folder_label is None:
        raise HTTPException(status_code=422, detail="Invalid video folder")
    if not isinstance(filename, str):
        raise HTTPException(status_code=404, detail="Video not found")

    media_root = _media_root()
    video_path = _resolve_video_in_folder(media_root, folder_label, filename)
    if video_path is None:
        raise HTTPException(status_code=404, detail="Video not found")
    try:
        file_identity = _video_file_identity(video_path)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Video not found") from exc

    compatibility_identity = None
    if folder_id == "waiting":
        compatibility_path = _resolve_hvc1_compat_video(media_root, filename)
        if compatibility_path is not None:
            try:
                compatibility_identity = _video_file_identity(compatibility_path)
            except OSError:
                # If the optional remux vanishes, the ticket can still use its source MP4.
                compatibility_identity = None

    ticket_id = secrets.token_urlsafe(32)
    ticket_key = _stream_ticket_key(ticket_id)
    if ticket_key is None:
        raise RuntimeError("generated invalid playback ticket")
    now = time.monotonic()
    with _STREAM_TICKET_LOCK:
        _prune_stream_tickets(now)
        if len(_STREAM_TICKETS) >= _MAX_STREAM_TICKETS:
            raise HTTPException(status_code=503, detail="Playback is temporarily unavailable")
        _STREAM_TICKETS[ticket_key] = {
            "folderId": folder_id,
            "filename": filename,
            "fileIdentity": file_identity,
            "compatibilityFileIdentity": compatibility_identity,
            "expiresAt": now + _STREAM_TICKET_TTL_SECONDS,
        }

    stream_url = request.url_for("video_agent_stream").include_query_params(ticket=ticket_id)
    return {
        "ticketId": ticket_id,
        "streamUrl": str(stream_url),
        "streamPath": f"/videos/stream?{urlencode({'ticket': ticket_id})}",
        "expiresInSeconds": _STREAM_TICKET_TTL_SECONDS,
    }


@router.post("/video-media-source")
def get_video_media_source(payload: dict[str, Any], response: Response) -> dict[str, str]:
    """Resolve an allowlisted MP4 path for Hermes Desktop's authenticated media protocol."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    folder_id = payload.get("folderId")
    folder_label = dict(FOLDERS).get(folder_id) if isinstance(folder_id, str) else None
    filename = payload.get("filename")
    if folder_label is None:
        raise HTTPException(status_code=422, detail="Invalid video folder")
    if not isinstance(filename, str):
        raise HTTPException(status_code=404, detail="Video not found")

    media_root = _media_root()
    media_path = _resolve_video_in_folder(media_root, folder_label, filename)
    if media_path is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if folder_id == "waiting":
        media_path = _resolve_hvc1_compat_video(media_root, filename) or media_path
    return {"mediaPath": str(media_path)}


@router.post("/video-tickets/renew")
def renew_video_ticket(payload: dict[str, Any]) -> dict[str, int]:
    """Extend a ticket only while its original, unchanged video is still allowlisted."""
    ticket_id = payload.get("ticketId")
    if not isinstance(ticket_id, str):
        raise HTTPException(status_code=404, detail="Playback ticket is unavailable")
    ticket_key = _stream_ticket_key(ticket_id)
    if ticket_key is None:
        raise HTTPException(status_code=404, detail="Playback ticket is unavailable")
    record, expired = _lookup_stream_ticket(ticket_id)
    if record is None:
        raise HTTPException(status_code=410 if expired else 404, detail="Playback ticket is unavailable")
    if _resolve_stream_ticket_video(record) is None:
        with _STREAM_TICKET_LOCK:
            _STREAM_TICKETS.pop(ticket_key, None)
        raise HTTPException(status_code=404, detail="Video not found")

    now = time.monotonic()
    with _STREAM_TICKET_LOCK:
        current = _STREAM_TICKETS.get(ticket_key)
        if current is None or current["expiresAt"] <= now:
            _STREAM_TICKETS.pop(ticket_key, None)
            raise HTTPException(status_code=410, detail="Playback ticket is unavailable")
        current["expiresAt"] = now + _STREAM_TICKET_TTL_SECONDS
    return {"expiresInSeconds": _STREAM_TICKET_TTL_SECONDS}


@router.post("/video-tickets/revoke")
def revoke_video_ticket(payload: dict[str, Any]) -> dict[str, bool]:
    """Revoke a Desktop playback capability; expired or repeated revocation is harmless."""
    ticket_id = payload.get("ticketId")
    if not isinstance(ticket_id, str):
        raise HTTPException(status_code=404, detail="Playback ticket is unavailable")
    ticket_key = _stream_ticket_key(ticket_id)
    if ticket_key is None:
        raise HTTPException(status_code=404, detail="Playback ticket is unavailable")
    with _STREAM_TICKET_LOCK:
        revoked = _STREAM_TICKETS.pop(ticket_key, None) is not None
    return {"revoked": revoked}


@router.get("/videos/stream", name="video_agent_stream")
def stream_video_with_ticket(ticket: str) -> FileResponse:
    """Range-stream the exact MP4 bound to a current short-lived ticket."""
    record, expired = _lookup_stream_ticket(ticket)
    if record is None:
        raise HTTPException(status_code=410 if expired else 404, detail="Playback ticket is unavailable")
    video_path = _resolve_stream_ticket_video(record)
    if video_path is None:
        ticket_key = _stream_ticket_key(ticket)
        if ticket_key is not None:
            with _STREAM_TICKET_LOCK:
                _STREAM_TICKETS.pop(ticket_key, None)
        raise HTTPException(status_code=404, detail="Video not found")
    metadata = video_path.stat()
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=video_path.name,
        stat_result=metadata,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _skills_root(plugin_root: Path, *, create: bool = False) -> Path | None:
    """Resolve the plugin-local skills directory and refuse symlink escapes."""
    try:
        resolved_plugin_root = plugin_root.resolve(strict=True)
        if not resolved_plugin_root.is_dir():
            return None
        skills_dir = resolved_plugin_root / "skills"
        if skills_dir.is_symlink():
            return None
        if create:
            skills_dir.mkdir(mode=0o755, exist_ok=True)
        if not skills_dir.exists():
            return None
        resolved_skills_dir = skills_dir.resolve(strict=True)
        if resolved_skills_dir.parent != resolved_plugin_root or not resolved_skills_dir.is_dir():
            return None
        return resolved_skills_dir
    except OSError:
        return None


def _read_skill_metadata(skill_dir: Path) -> dict[str, str] | None:
    """Read the small frontmatter contract used by skills created in this UI."""
    skill_file = skill_dir / "SKILL.md"
    if skill_file.is_symlink() or not skill_file.is_file():
        return None
    try:
        content = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if not content.startswith("---\n"):
        return None
    closing = content.find("\n---\n", 4)
    if closing < 0:
        return None

    fields: dict[str, str] = {}
    for line in content[4:closing].splitlines():
        key, separator, value = line.partition(":")
        if separator and key in {"name", "description"}:
            fields[key] = value.strip()
    try:
        description = json.loads(fields.get("description", ""))
    except (json.JSONDecodeError, TypeError):
        description = fields.get("description", "").strip("\"'")
    name = fields.get("name", "")
    if name != skill_dir.name or not isinstance(description, str) or not description.strip():
        return None
    return {"name": name, "description": description}


def _list_custom_skills(plugin_root: Path | None = None) -> dict[str, Any]:
    """List valid, direct plugin-owned SKILL.md files without returning their paths."""
    skills_dir = _skills_root(plugin_root or _plugin_root())
    if skills_dir is None:
        return {"skills": []}

    skills: list[dict[str, str]] = []
    try:
        with os.scandir(skills_dir) as entries:
            for entry in entries:
                if not _SKILL_NAME_RE.fullmatch(entry.name):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                metadata = _read_skill_metadata(Path(entry.path))
                if metadata is not None:
                    skills.append(metadata)
    except OSError:
        return {"skills": []}
    skills.sort(key=lambda item: item["name"])
    return {"skills": skills}


def _create_custom_skill(
    plugin_root: Path,
    name: str,
    description: str,
    instructions: str,
) -> dict[str, Any]:
    """Create a non-overwriting portable Agent Plugin skill under this plugin's root."""
    if not isinstance(name, str) or len(name) > 64 or not _SKILL_NAME_RE.fullmatch(name):
        raise ValueError("Le nom doit être un identifiant en minuscules, avec des tirets au besoin.")
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description.strip()) > _MAX_SKILL_DESCRIPTION
        or "\n" in description
        or "\r" in description
    ):
        raise ValueError("La description doit contenir de 1 à 1024 caractères sur une seule ligne.")
    normalized_instructions = instructions.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized_instructions or len(normalized_instructions) > _MAX_SKILL_BODY:
        raise ValueError("Les instructions doivent contenir de 1 à 32000 caractères.")
    if "\x00" in normalized_instructions:
        raise ValueError("Les instructions contiennent un caractère non valide.")
    try:
        description.strip().encode("utf-8")
        normalized_instructions.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Le texte doit être encodé en UTF-8 valide.") from exc

    resolved_root = plugin_root.resolve(strict=True)
    skills_dir = _skills_root(resolved_root, create=True)
    if skills_dir is None:
        raise ValueError("Le dossier des skills du plugin n’est pas accessible.")
    skill_dir = skills_dir / name
    if skill_dir.is_symlink() or skill_dir.exists():
        raise FileExistsError("Un skill avec ce nom existe déjà.")

    skill_dir.mkdir(mode=0o755)
    skill_file = skill_dir / "SKILL.md"
    content = (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description.strip(), ensure_ascii=False)}\n"
        "---\n\n"
        f"{normalized_instructions}\n"
    )
    try:
        with skill_file.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except FileExistsError:
        try:
            skill_dir.rmdir()
        except OSError:
            pass
        raise
    except OSError:
        try:
            skill_file.unlink(missing_ok=True)
            skill_dir.rmdir()
        except OSError:
            pass
        raise

    return {
        "skill": {"name": name, "description": description.strip()},
        "requiresGatewayReload": True,
    }


@router.get("/skills")
def list_custom_skills() -> dict[str, Any]:
    """List custom skills bundled under this plugin's own directory."""
    return _list_custom_skills()


@router.post("/skills", status_code=201)
def create_custom_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Create one portable skill; the standard Agent Plugin loader activates it after reload."""
    try:
        return _create_custom_skill(
            _plugin_root(),
            payload.get("name"),
            payload.get("description"),
            payload.get("instructions"),
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Impossible d’enregistrer le skill.") from exc


@router.get("/references")
def list_references() -> dict[str, Any]:
    """Return saved, allowlisted short-form video reference links."""
    try:
        return {"references": _list_references(_references_home())}
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Impossible de lire les références locales.") from exc


@router.post("/references", status_code=201)
def create_reference(payload: dict[str, Any]) -> dict[str, Any]:
    """Save one validated reference link without fetching or downloading it."""
    try:
        reference = _add_reference(
            _references_home(),
            payload.get("url"),
            payload.get("note", ""),
        )
        return {"reference": reference}
    except _ReferenceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Impossible d’enregistrer la référence.") from exc


@router.delete("/references/{reference_id}")
def delete_reference(reference_id: str) -> dict[str, bool]:
    """Remove one saved reference by its opaque ID."""
    try:
        deleted = _delete_reference(_references_home(), reference_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Impossible de modifier les références locales.") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return {"removed": True}


@router.get("/content/{filename:path}")
def get_content_workspace(filename: str) -> dict[str, Any]:
    """List saved content drafts and the current reference guide for one Waiting VOD."""
    try:
        return _get_content_workspace(_media_root(), _references_home(), filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rediffusion introuvable dans Waiting.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Impossible de lire l’espace de contenu local.") from exc


@router.post("/content/{filename:path}/items", status_code=201)
def create_content_draft(filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Create a saved Short/video draft with a snapshot of current reference observations."""
    try:
        item = _create_content_draft(_media_root(), _references_home(), filename, payload)
        return {"item": item}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rediffusion introuvable dans Waiting.") from exc
    except _ContentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Contenu invalide; vérifiez le format et les champs texte.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Impossible d’enregistrer le brouillon local.") from exc


@router.post("/content/{filename:path}/moments", status_code=201)
def create_video_moment(filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Save one timestamped moment associated with a regular MP4 in Waiting."""
    try:
        moment = _create_video_moment(_media_root(), _references_home(), filename, payload)
        return {"moment": moment}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rediffusion introuvable dans Waiting.") from exc
    except _MomentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Moment invalide; vérifiez le timecode, la cible et la note.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Impossible d’enregistrer le moment local.") from exc
