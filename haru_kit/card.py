"""Character cards and voice clips for the API server, client, and framework adapters.

Matches upstream ``haru.prompt.render_card`` without importing ``torch``, and reads
characters and voices from the shared ``haru-kit`` library folder.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import paths

CARD_FIELDS = ("persona", "name", "gender", "backchannel", "other_name", "relationship", "situation")
DEFAULT_REGISTRY = "https://yungalgo.github.io/haru-characters/"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VOICE_EXTS = (".wav", ".flac", ".ogg", ".opus", ".mp3", ".m4a", ".aac", ".aiff", ".caf")
_MAX_PACK_BYTES = 25 * 1024 * 1024
_MAX_UNPACKED_BYTES = 100 * 1024 * 1024


@lru_cache(maxsize=1)
def card_templates_data() -> dict[str, Any]:
    path = paths.package_dir() / "assets" / "card_templates.json"
    return json.loads(path.read_text(encoding="utf-8"))


def card_templates() -> dict[str, dict[str, Any]]:
    return card_templates_data()["templates"]


def supported_languages() -> tuple[str, ...]:
    return tuple(item["code"] for item in card_templates_data()["languages"])


@lru_cache(maxsize=1)
def default_character_data() -> dict[str, Any]:
    path = paths.package_dir() / "assets" / "default_character.json"
    return json.loads(path.read_text(encoding="utf-8"))


def check_caller_path() -> Path:
    return paths.package_dir() / "assets" / "check_caller.flac"


def check_voice_path() -> Path:
    return paths.package_dir() / "assets" / "check_voice.flac"


def render_card(
    card: Mapping[str, Any],
    language: str = "en",
    template: Mapping[str, Any] | None = None,
) -> str:
    """Render the 7 card fields into the text prompt Haru-4B expects."""
    if template is None:
        templates = card_templates()
        template = templates.get(language) or templates["en"]
    lines = [str(template["lead"])]
    for key in CARD_FIELDS:
        value = str(card.get(key) or "").strip()
        if key in ("gender", "backchannel"):
            value = str(template[key].get(value, ""))
        if value:
            lines += [str(template["headers"][key]), value]
    return "\n".join(lines)


def _is_safe_id(value: str) -> bool:
    return bool(SAFE_ID.fullmatch(value)) and ".." not in value


def _contained(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def list_characters(library_dir: Path | None = None) -> list[dict[str, Any]]:
    root = (library_dir or paths.app_library_dir()) / "characters"
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for item in sorted(root.glob("*.character.json")):
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cid = str(data.get("id") or "")
        if _is_safe_id(cid) and isinstance(data.get("versions"), dict):
            out.append(data)
    return out


def load_character(character_id: str, library_dir: Path | None = None) -> dict[str, Any] | None:
    if not _is_safe_id(character_id):
        return None
    chars_dir = (library_dir or paths.app_library_dir()) / "characters"
    path = chars_dir / f"{character_id}.character.json"
    if not path.is_file() or not _contained(chars_dir, path):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") == character_id else None


def find_voice_file(voice_id: str, library_dir: Path | None = None) -> Path | None:
    if not _is_safe_id(voice_id):
        return None
    voices_dir = (library_dir or paths.app_library_dir()) / "voices"
    if not voices_dir.is_dir():
        return None
    meta_path = voices_dir / f"{voice_id}.voice.json"
    if meta_path.is_file() and _contained(voices_dir, meta_path):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            file_name = str(meta.get("file") or "")
            if file_name and "/" not in file_name and "\\" not in file_name and ".." not in file_name:
                candidate = voices_dir / file_name
                if candidate.is_file() and _contained(voices_dir, candidate):
                    return candidate
        except (OSError, ValueError):
            pass
    for ext in VOICE_EXTS:
        candidate = voices_dir / f"{voice_id}{ext}"
        if candidate.is_file() and _contained(voices_dir, candidate):
            return candidate
    return None


def first_voice_for_language(language: str, library_dir: Path | None = None) -> Path | None:
    voices_dir = (library_dir or paths.app_library_dir()) / "voices"
    if not voices_dir.is_dir():
        return None
    fallback: Path | None = None
    for meta_path in sorted(voices_dir.glob("*.voice.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        vid = str(meta.get("id") or meta_path.name.removesuffix(".voice.json"))
        audio = find_voice_file(vid, library_dir)
        if audio is None:
            continue
        if meta.get("language") == language:
            return audio
        if fallback is None:
            fallback = audio
    return fallback


def _extract_registry_pack(pack_bytes: bytes, entry_id: str, library_dir: Path) -> bool:
    with zipfile.ZipFile(io.BytesIO(pack_bytes)) as zf:
        infos = zf.infolist()
        if len(infos) > 64:
            return False
        total = 0
        files: dict[str, bytes] = {}
        for info in infos:
            name = info.filename
            if name.endswith("/"):
                continue
            if name.startswith("/") or "\\" in name or ".." in Path(name).parts:
                return False
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:  # symlink
                return False
            total += info.file_size
            if total > _MAX_UNPACKED_BYTES:
                return False
            data = zf.read(info)
            if len(data) != info.file_size:
                return False
            files[name] = data

    try:
        manifest = json.loads(files.get("haru.json", b"").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    if manifest.get("characters") != [entry_id]:
        return False

    char_key = f"characters/{entry_id}.character.json"
    if char_key not in files:
        return False
    try:
        char_obj = json.loads(files[char_key].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    if char_obj.get("id") != entry_id:
        return False

    for sub in ("characters", "voices", "images"):
        (library_dir / sub).mkdir(parents=True, exist_ok=True)

    for name, raw in files.items():
        parts = Path(name).parts
        if len(parts) != 2 or parts[0] not in ("characters", "voices", "images"):
            continue
        if not SAFE_ID.fullmatch(Path(parts[1]).stem.removesuffix(".character").removesuffix(".voice")):
            continue
        dest = library_dir / parts[0] / parts[1]
        if _contained(library_dir / parts[0], dest):
            dest.write_bytes(raw)
    return True


def seed_library_if_empty(library_dir: Path | None = None, registry_url: str | None = None) -> bool:
    """Populate an empty library from the character registry (same source as the Mac and web apps)."""
    target = library_dir or paths.app_library_dir()
    if list_characters(target):
        return True
    base = registry_url if registry_url is not None else os.environ.get("HARU_KIT_REGISTRY", DEFAULT_REGISTRY)
    base = base.strip()
    if not base or not base.startswith(("https://", "http://")):
        return False
    if not base.endswith("/"):
        base += "/"
    try:
        with urllib.request.urlopen(base + "index.json", timeout=15) as resp:
            index = json.load(resp)
    except Exception:  # noqa: BLE001 - offline or unreachable; caller falls back cleanly
        return False
    entries = index.get("entries") if isinstance(index, dict) else None
    if not isinstance(entries, list):
        return False
    added = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("id") or "")
        haru_rel = str(entry.get("haru") or "")
        size = entry.get("size")
        sha256 = str(entry.get("sha256") or "").lower()
        if (
            not _is_safe_id(cid)
            or not haru_rel
            or not isinstance(size, int)
            or not (0 < size <= _MAX_PACK_BYTES)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            continue
        pack_url = urllib.parse.urljoin(base + "index.json", haru_rel)
        if not pack_url.startswith(("https://", "http://")):
            continue
        if base.startswith("https://") and not pack_url.startswith("https://"):
            continue
        try:
            with urllib.request.urlopen(pack_url, timeout=30) as resp:
                pack = resp.read(_MAX_PACK_BYTES + 1)
        except Exception:  # noqa: BLE001
            continue
        if len(pack) != size or hashlib.sha256(pack).hexdigest() != sha256:
            continue
        if _extract_registry_pack(pack, cid, target):
            added = True
    return added


def _character_version(data: Mapping[str, Any], language: str) -> tuple[str, dict[str, Any]]:
    versions = data.get("versions") or {}
    if language in versions and isinstance(versions[language], dict):
        return language, versions[language]
    if "en" in versions and isinstance(versions["en"], dict):
        return "en", versions["en"]
    for lang, ver in versions.items():
        if isinstance(ver, dict):
            return str(lang), ver
    raise ValueError(f"character {data.get('id')!r} has no language versions")


def resolve_card_and_voice(
    card: str | Path | Mapping[str, Any] | None = None,
    voice: str | Path | bytes | Any | None = None,
    *,
    language: str = "en",
    library_dir: Path | None = None,
    seed_registry: bool = True,
) -> tuple[str, Path | bytes | Any]:
    """Resolve ``(card, voice)`` into ``(system_prompt_text, voice_source)``.

    - ``card``: ``None`` (default ``"haru"``), a library character ID, a ``dict`` of the 7 card
      fields, a path to a ``.character.json`` or ``.txt`` file, or raw rendered card text.
    - ``voice``: ``None`` (the character's voice in the library, or fallback voice), a library
      voice ID, a file path, ``bytes``, or a float32 ``numpy.ndarray``.
    """
    lib = library_dir or paths.app_library_dir()
    character_voice_id: str | None = None
    prompt = ""

    if card is None:
        if seed_registry:
            seed_library_if_empty(lib)
        char_data = load_character("haru", lib) or default_character_data()
        lang_used, ver = _character_version(char_data, language)
        prompt = render_card(ver.get("card") or {}, lang_used)
        character_voice_id = str(ver.get("voice") or "") or None
    elif isinstance(card, Mapping):
        if "versions" in card and isinstance(card["versions"], Mapping):
            lang_used, ver = _character_version(card, language)
            prompt = render_card(ver.get("card") or {}, lang_used)
            character_voice_id = str(ver.get("voice") or "") or None
        else:
            prompt = render_card(card, language)
    elif isinstance(card, Path):
        if card.suffix == ".json":
            loaded = json.loads(card.read_text(encoding="utf-8"))
            return resolve_card_and_voice(
                loaded, voice, language=language, library_dir=lib, seed_registry=seed_registry
            )
        prompt = card.read_text(encoding="utf-8").strip()
    elif isinstance(card, str):
        raw = card.strip()
        candidate_path = Path(raw)
        if "\n" not in raw and len(raw) < 260 and candidate_path.is_file():
            return resolve_card_and_voice(
                candidate_path, voice, language=language, library_dir=lib, seed_registry=seed_registry
            )
        if "\n" not in raw and _is_safe_id(raw):
            if seed_registry and not list_characters(lib):
                seed_library_if_empty(lib)
            char_data = load_character(raw, lib)
            if char_data is None and raw == "haru":
                char_data = default_character_data()
            if char_data is not None:
                lang_used, ver = _character_version(char_data, language)
                prompt = render_card(ver.get("card") or {}, lang_used)
                character_voice_id = str(ver.get("voice") or "") or None
            else:
                prompt = raw
        else:
            prompt = raw
    else:
        raise TypeError(f"unsupported card type: {type(card).__name__}")

    # Resolve voice
    if voice is None:
        if seed_registry and not (lib / "voices").is_dir():
            seed_library_if_empty(lib)
        resolved_voice: Path | None = None
        if character_voice_id:
            resolved_voice = find_voice_file(character_voice_id, lib)
        if resolved_voice is None:
            resolved_voice = first_voice_for_language(language, lib)
        if resolved_voice is None:
            resolved_voice = check_voice_path()
        return prompt, resolved_voice

    if isinstance(voice, (bytes, bytearray)):
        return prompt, bytes(voice)
    if isinstance(voice, Path):
        if not voice.is_file():
            raise FileNotFoundError(f"voice file not found: {voice}")
        return prompt, voice
    if isinstance(voice, str):
        vpath = Path(voice)
        if vpath.is_file():
            return prompt, vpath
        if seed_registry and not (lib / "voices").is_dir():
            seed_library_if_empty(lib)
        found = find_voice_file(voice, lib)
        if found is not None:
            return prompt, found
        raise FileNotFoundError(f"voice {voice!r} was not found as a file path or in {lib / 'voices'}")

    # e.g. numpy.ndarray passed directly by Python caller
    return prompt, voice
