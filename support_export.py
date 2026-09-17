#!/usr/bin/env python3
import json
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ruby_starter_hunter import Bridge, ADDR, player_state, main_callback2
from gen3bot.pokemon.pk3 import decode_mon, apply_species_metadata
from gen3bot.data.rs_world import RSWorldDatabase, wurmple_evolution
from gen3bot.games.rs import (
    battle_active, battle_type_flags, battlers_count,
    battle_controller_exec_flags, choose_action_state,
    action_menu_candidate_state,
)
from rtc_tools import read_rtc_snapshot, G_RNG_VALUE
from stats_store import appdata_dir


def _safe_read(bridge, address, length):
    try:
        return bridge.read(address, length).hex().upper()
    except Exception as e:
        return {"error": str(e)}


def _safe_mon(bridge, address, world=None):
    try:
        raw = bridge.read(address, 100)
        # Empty party slots can contain trailing runtime bytes; do not present
        # an all-zero PK3 header as a real shiny Species 0.
        if raw[:80] == b"\0" * 80:
            mon = None
        else:
            mon = decode_mon(raw)
            if mon and world is not None:
                try:
                    mon = apply_species_metadata(mon, world.species_meta(mon["species_id"]), world.item_name(mon.get("held_item_id", 0)))
                    mon["wurmple_evolution"] = wurmple_evolution(mon["pid"], mon["species_id"])
                except Exception as e:
                    mon["world_metadata_error"] = str(e)
        return {
            "raw_hex": raw.hex().upper(),
            "decoded": mon,
        }
    except Exception as e:
        return {"error": str(e)}


def capture_bridge_snapshot(ip):
    out = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "target": f"{ip}:4953",
    }
    try:
        b = Bridge(ip, timeout=0.45)
        out["ping"] = b.cmd("PING", retries=2)
        out["game"] = b.cmd("GAME", retries=2)
        out["status"] = b.cmd("STATUS", retries=2)
        out["rom_revision"] = b.read(ADDR["revision"], 1)[0]
        world = None
        try:
            from gen3bot.games.rs import detect_profile
            world = RSWorldDatabase(b, detect_profile(b))
        except Exception as e:
            out["world_database_error"] = str(e)

        try:
            out["player_state"] = player_state(b)
        except Exception as e:
            out["player_state_error"] = str(e)

        try:
            out["callback2"] = f"0x{main_callback2(b):08X}"
        except Exception as e:
            out["callback2_error"] = str(e)

        try:
            out["rtc"] = read_rtc_snapshot(b)
        except Exception as e:
            out["rtc_error"] = str(e)

        try:
            rng = int.from_bytes(b.read(G_RNG_VALUE, 4), "little")
            out["gRngValue"] = f"0x{rng:08X}"
        except Exception as e:
            out["gRngValue_error"] = str(e)

        try:
            out["party_count"] = b.read(ADDR["gPlayerPartyCount"], 1)[0]
        except Exception as e:
            out["party_count_error"] = str(e)

        try:
            out["battle_state"] = {
                "active": battle_active(b),
                "gMain_inBattle_byte": f"0x{b.read(ADDR['gMain'] + 0x43D, 1)[0]:02X}",
                "type_flags": f"0x{battle_type_flags(b):04X}",
                "battlers_count": battlers_count(b),
                "controller_exec_flags": f"0x{battle_controller_exec_flags(b):08X}",
                "choose_action": choose_action_state(b),
                "action_menu_candidate": action_menu_candidate_state(b),
            }
        except Exception as e:
            out["battle_state_error"] = str(e)

        out["player_slot1"] = _safe_mon(b, ADDR["gPlayerParty"], world)
        out["enemy_slot1"] = _safe_mon(b, ADDR["gEnemyParty"], world)
        out["raw_blocks"] = {
            "gMain_64": _safe_read(b, ADDR["gMain"], 64),
            "gMain_tail_4": _safe_read(b, ADDR["gMain"] + 0x43C, 4),
            "gTasks_640": _safe_read(b, ADDR["gTasks"], 40 * 16),
            "gPlayerAvatar_36": _safe_read(b, ADDR["gPlayerAvatar"], 0x24),
            "gSaveBlock1_head_32": _safe_read(b, ADDR["gSaveBlock1"], 32),
            "gSaveBlock2_head_32": _safe_read(b, ADDR["gSaveBlock2"], 32),
        }
        try:
            out["starter_rng_gate"] = b.starter_result()
        except Exception as e:
            out["starter_rng_gate"] = {"error": repr(e)}

        out["bridge_client_diagnostics"] = {
            "ignored_stale_replies": b.ignored_replies,
            "last_ignored_reply": b.last_ignored_reply,
        }
    except Exception as e:
        out["bridge_error"] = str(e)
    return out


def export_support_bundle(ip, ui_settings, events, app_version, source_dir):
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")

    home = Path(os.environ.get("USERPROFILE") or Path.home())
    output_dir = home / "Desktop"
    if not output_dir.exists():
        output_dir = Path.cwd()

    final_zip = output_dir / f"Pokebot3DS-CFW_support_{stamp}.zip"

    with tempfile.TemporaryDirectory(prefix="pokebot3ds_support_") as tmp:
        root = Path(tmp) / f"Pokebot3DS-CFW_support_{stamp}"
        root.mkdir(parents=True, exist_ok=True)

        environment = {
            "app_version": app_version,
            "exported_at_local": now.isoformat(),
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        }
        (root / "environment.json").write_text(
            json.dumps(environment, indent=2, default=str), encoding="utf-8"
        )
        (root / "ui_settings.json").write_text(
            json.dumps(ui_settings, indent=2, default=str), encoding="utf-8"
        )
        (root / "session_events.json").write_text(
            json.dumps(list(events), indent=2, default=str), encoding="utf-8"
        )

        snapshot = capture_bridge_snapshot(ip)
        (root / "bridge_snapshot.json").write_text(
            json.dumps(snapshot, indent=2, default=str), encoding="utf-8"
        )

        appdata = appdata_dir()
        for name in ("stats.json", "rng_history.json", "rs_hunt_policy.json"):
            source = appdata / name
            if source.exists():
                shutil.copy2(source, root / name)
        for source in appdata.glob("rs_world_*.json"):
            if source.is_file():
                shutil.copy2(source, root / source.name)

        source_dir = Path(source_dir)
        for name in ("UI_README.txt", "README.txt", "PACKAGE_SHA256SUMS.txt"):
            source = source_dir / name
            if source.exists():
                shutil.copy2(source, root / name)

        with zipfile.ZipFile(final_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for file in root.rglob("*"):
                if file.is_file():
                    z.write(file, arcname=f"{root.name}/{file.relative_to(root)}")

    return final_zip
