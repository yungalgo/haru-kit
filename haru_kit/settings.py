"""settings.json: the last choice (way, front end, port, precision)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import paths


@dataclass
class Settings:
    way: str | None = None  # "mac", "nvidia", "modal"
    front: str | None = None  # Mac only: "app" or "browser"
    port: int = 8998
    quantize: str = "8"  # Mac only: "8" or "none"
    app_source: str | None = None  # SHA-256 of the Mac app source that the installed app was built from
    modal_region: str = "auto"  # Modal: "auto" (from the time zone), "any", "us", "eu", "ap" or a narrow region

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or paths.settings_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or paths.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
