#!/usr/bin/env python3
"""Pinned 40Cakes/pokebot-gen3 Pokémon sprite asset helper.

Upstream source (pinned for repeatability):
  40Cakes/pokebot-gen3 @ 5dd898f830775d448b06db6f5cd65b930540f146
  modules/web/static/sprites/pokemon/{normal,shiny,anti-shiny}

There are 413 PNGs in each variant directory.  Sprites are UI-only: hunting
logic never depends on a network request.  Missing files can be populated into
``assets/pokemon`` with the included SYNC_40CAKES_SPRITES.bat or automatically
in the background while the app is open.
"""
from pathlib import Path
import string
import urllib.parse
import urllib.request

COMMIT = "5dd898f830775d448b06db6f5cd65b930540f146"
BASE = (
    f"https://raw.githubusercontent.com/40Cakes/pokebot-gen3/{COMMIT}/"
    "modules/web/static/sprites/pokemon"
)
VARIANTS = ("normal", "shiny", "anti-shiny")
EXPECTED_PER_VARIANT = 413
ROOT = Path(__file__).resolve().parents[2] / "assets" / "pokemon"


def safe_name(value: str) -> str:
    """Match 40Cakes' make_string_safe_for_file_name()."""
    result = ""
    allowed = f"-_.()' {string.ascii_letters}{string.digits}"
    for char in str(value):
        if char in allowed:
            result += char
        elif char == "♂":
            result += "_m"
        elif char == "♀":
            result += "_f"
        elif char == "!":
            result += "em"
        elif char == "?":
            result += "qm"
        else:
            result += "_"
    return result


def filename(species):
    # 40Cakes uses a deterministic Unown sprite for generic Unown.
    if str(species) == "Unown":
        return "Unown (P).png"
    return safe_name(str(species)) + ".png"


def path_for(species, variant="normal"):
    variant = variant if variant in VARIANTS else "normal"
    return ROOT / variant / filename(species)


def asset_counts():
    return {
        variant: len(list((ROOT / variant).glob("*.png")))
        if (ROOT / variant).exists() else 0
        for variant in VARIANTS
    }


def pack_complete():
    counts = asset_counts()
    return all(counts[v] >= EXPECTED_PER_VARIANT for v in VARIANTS)


def ensure_one(species, variant="normal", timeout=4):
    path = path_for(species, variant)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{variant}/{urllib.parse.quote(filename(species))}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Pokebot3DS-CFW"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
        if data.startswith(b"\x89PNG"):
            temp = path.with_suffix(".tmp")
            temp.write_bytes(data)
            temp.replace(path)
    except Exception:
        pass
    return path


def ensure_variants(species):
    return [ensure_one(species, variant) for variant in VARIANTS]


def sync_all(progress=None, timeout=8):
    """Populate all 413 files for all three variants from pinned upstream."""
    import json

    api = (
        "https://api.github.com/repos/40Cakes/pokebot-gen3/contents/"
        "modules/web/static/sprites/pokemon/normal?ref=" + COMMIT
    )
    request = urllib.request.Request(api, headers={"User-Agent": "Pokebot3DS-CFW"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        files = json.loads(response.read().decode("utf-8"))
    names = [
        entry["name"] for entry in files
        if entry.get("type") == "file" and entry.get("name", "").lower().endswith(".png")
    ]
    total = len(names) * len(VARIANTS)
    done = 0
    for variant in VARIANTS:
        (ROOT / variant).mkdir(parents=True, exist_ok=True)
        for name in names:
            path = ROOT / variant / name
            if not path.exists():
                url = f"{BASE}/{variant}/{urllib.parse.quote(name)}"
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": "Pokebot3DS-CFW"})
                    with urllib.request.urlopen(request, timeout=timeout) as response:
                        data = response.read()
                    if data.startswith(b"\x89PNG"):
                        temp = path.with_suffix(".tmp")
                        temp.write_bytes(data)
                        temp.replace(path)
                except Exception:
                    pass
            done += 1
            if progress:
                try:
                    progress(done, total, name, variant)
                except Exception:
                    pass
    return done, total


def background_sync_all(progress=None):
    import threading
    thread = threading.Thread(
        target=lambda: _safe_sync(progress), name="RS Sprite Sync", daemon=True
    )
    thread.start()
    return thread


def _safe_sync(progress=None):
    try:
        if pack_complete():
            total = EXPECTED_PER_VARIANT * len(VARIANTS)
            return (total, total)
        return sync_all(progress=progress)
    except Exception:
        return (0, 0)
