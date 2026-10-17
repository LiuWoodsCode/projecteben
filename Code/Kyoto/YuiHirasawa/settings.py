"""Persistent settings shared by the Yui shell and its applications."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


CONFIG_PATH = Path.home() / ".config" / "yui" / "config.json"
DEFAULT_CONFIG: dict[str, Any] = {"wallpaper_enabled": True, "shell": "desktop"}
SHELL_OPTIONS = ("desktop", "mobile")


def load_config() -> dict[str, Any]:
    """Load the Yui configuration, creating it with defaults when absent."""
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_CONFIG.copy()

    value = data.get("wallpaper_enabled") if isinstance(data, dict) else None
    shell = data.get("shell") if isinstance(data, dict) else None
    return {
        "wallpaper_enabled": value
        if isinstance(value, bool)
        else DEFAULT_CONFIG["wallpaper_enabled"],
        "shell": shell if shell in SHELL_OPTIONS else DEFAULT_CONFIG["shell"],
    }


def save_config(config: Mapping[str, Any]) -> None:
    """Validate and atomically save the complete Yui configuration."""
    wallpaper_enabled_value = config.get(
        "wallpaper_enabled", DEFAULT_CONFIG["wallpaper_enabled"]
    )
    if not isinstance(wallpaper_enabled_value, bool):
        raise ValueError("wallpaper_enabled must be a boolean")
    shell = config.get("shell", DEFAULT_CONFIG["shell"])
    if shell not in SHELL_OPTIONS:
        raise ValueError("shell must be 'desktop' or 'mobile'")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=CONFIG_PATH.parent,
            prefix="config.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(
                {"wallpaper_enabled": wallpaper_enabled_value, "shell": shell},
                temporary,
                indent=2,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(CONFIG_PATH)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def wallpaper_enabled() -> bool:
    return load_config()["wallpaper_enabled"]


def set_wallpaper_enabled(enabled: bool) -> None:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    config = load_config()
    config["wallpaper_enabled"] = enabled
    save_config(config)


def set_shell(shell: str) -> None:
    """Choose the shell to launch next time Yui starts."""
    if shell not in SHELL_OPTIONS:
        raise ValueError("shell must be 'desktop' or 'mobile'")
    config = load_config()
    config["shell"] = shell
    save_config(config)
