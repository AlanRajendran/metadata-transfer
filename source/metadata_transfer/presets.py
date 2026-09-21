"""User settings and channel-name presets in %APPDATA%\\Metadata Transfer (shared by all versions)."""

from __future__ import annotations

import json
import os
from typing import Any

from . import APP_NAME
from .model import Channel


def settings_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _file() -> str:
    return os.path.join(settings_dir(), "settings.json")


def load() -> dict[str, Any]:
    try:
        with open(_file(), encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict[str, Any]) -> None:
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, _file())


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def put(key: str, value: Any) -> None:
    data = load()
    data[key] = value
    save(data)


def key_str(ch: Channel) -> str:
    ex, em, det, mod, *rest = ch.settings_key
    em_s = f"{em[0]}-{em[1]}" if em else "-"
    key = f"{ex}|{em_s}|{det}|{mod}"
    extra = "|".join(str(r) for r in rest if r)
    return key + ("|" + extra if extra else "")


def preset_names() -> list[str]:
    return sorted(load().get("presets", {}))


def save_preset(name: str, channels: list[Channel]) -> None:
    data = load()
    presets = data.setdefault("presets", {})
    presets[name] = {key_str(ch): ch.name for ch in channels}
    save(data)


def delete_preset(name: str) -> None:
    data = load()
    data.get("presets", {}).pop(name, None)
    save(data)


def names_from_preset(name: str, channels: list[Channel]) -> list[str]:
    """Channel names for `channels` using the preset; channels not in the preset keep their name."""
    mapping = load().get("presets", {}).get(name, {})
    return [mapping.get(key_str(ch), ch.name) for ch in channels]
