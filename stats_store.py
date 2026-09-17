#!/usr/bin/env python3
import json
import os
from pathlib import Path
from datetime import datetime, timezone

APP_NAME = "Pokebot3DS-CFW"
COMPONENT = "Gen3"

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def appdata_dir():
    base = os.environ.get("APPDATA")
    if base:
        root = Path(base)
    else:
        root = Path.home() / "AppData" / "Roaming"
    path = root / APP_NAME / COMPONENT
    path.mkdir(parents=True, exist_ok=True)
    return path

def stats_path():
    return appdata_dir() / "stats.json"

DEFAULT_DATA = {
    "schema_version": 1,
    "lifetime": {
        "encounters": 0,
        "shinies": 0,
        "duplicates": 0,
        "sessions": 0,
        "rng_repeats_avoided": 0,
    },
    "phase_records": {
        "pending_reset": False,
        "encounters": 0,
        "lowest_sv": None,
        "highest_sv": None,
        "highest_iv_sum": None,
        "lowest_iv_sum": None,
    },
    "recent_shinies": [],
    "last_session": None,
    "active_session": None,
}

class StatsStore:
    def __init__(self):
        self.path = stats_path()
        self.data = self._load()
        # If the prior process died mid-hunt, keep the snapshot rather than
        # silently discarding it.
        if self.data.get("active_session"):
            interrupted = dict(self.data["active_session"])
            interrupted["ended_at"] = utc_now()
            interrupted["status"] = "Interrupted"
            self.data["last_session"] = interrupted
            self.data["active_session"] = None
            self._save()

    def _load(self):
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_DATA))
        try:
            with self.path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception:
            # Preserve a corrupt file for diagnosis and start clean.
            try:
                backup = self.path.with_name(
                    f"stats_corrupt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                )
                self.path.replace(backup)
            except Exception:
                pass
            return json.loads(json.dumps(DEFAULT_DATA))

        data = json.loads(json.dumps(DEFAULT_DATA))
        data.update({k: v for k, v in loaded.items() if k in data})
        lifetime = dict(DEFAULT_DATA["lifetime"])
        lifetime.update(loaded.get("lifetime") or {})
        data["lifetime"] = lifetime
        phase = dict(DEFAULT_DATA["phase_records"])
        phase.update(loaded.get("phase_records") or {})
        data["phase_records"] = phase
        recent_shinies = loaded.get("recent_shinies") or []
        if isinstance(recent_shinies, list):
            data["recent_shinies"] = recent_shinies[-100:]
        return data

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = json.dumps(self.data, indent=2, sort_keys=True)
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def lifetime(self):
        return dict(self.data["lifetime"])

    def recent_shinies(self):
        return json.loads(json.dumps(self.data.get("recent_shinies") or []))

    def clear_recent_shinies(self):
        self.data["recent_shinies"] = []
        self._save()

    def phase_records(self):
        return json.loads(json.dumps(self.data.get("phase_records") or DEFAULT_DATA["phase_records"]))

    def _blank_phase_records(self):
        return json.loads(json.dumps(DEFAULT_DATA["phase_records"]))

    def prepare_phase_for_hunt(self):
        phase = self.data.get("phase_records") or self._blank_phase_records()
        if phase.get("pending_reset"):
            self.data["phase_records"] = self._blank_phase_records()
            self._save()
        return self.phase_records()

    def _record_snapshot(self, mon, value):
        return {
            "value": int(value),
            "species": mon.get("species"),
            "pid": f"{int(mon.get('pid', 0)):08X}",
            "encounter_no": int(mon.get("encounter_no", 0)),
        }

    def update_phase_records(self, mon):
        phase = self.data.get("phase_records") or self._blank_phase_records()
        ivs = mon.get("ivs") or {}
        iv_sum = sum(int(ivs.get(k, 0)) for k in ("hp", "atk", "def", "spa", "spd", "spe"))
        sv = int(mon.get("sv", 0))

        phase["encounters"] = int(phase.get("encounters", 0)) + 1

        low_sv = phase.get("lowest_sv")
        if low_sv is None or sv < int(low_sv.get("value", 65536)):
            phase["lowest_sv"] = self._record_snapshot(mon, sv)

        high_sv = phase.get("highest_sv")
        if high_sv is None or sv > int(high_sv.get("value", -1)):
            phase["highest_sv"] = self._record_snapshot(mon, sv)

        high_iv = phase.get("highest_iv_sum")
        if high_iv is None or iv_sum > int(high_iv.get("value", -1)):
            phase["highest_iv_sum"] = self._record_snapshot(mon, iv_sum)

        low_iv = phase.get("lowest_iv_sum")
        if low_iv is None or iv_sum < int(low_iv.get("value", 999)):
            phase["lowest_iv_sum"] = self._record_snapshot(mon, iv_sum)

        # Keep the completed phase visible after the shiny is found. The next
        # hunt calls prepare_phase_for_hunt(), which clears it.
        if mon.get("shiny"):
            phase["pending_reset"] = True

        self.data["phase_records"] = phase
        self._save()
        return self.phase_records()

    def begin_session(self, settings):
        lifetime = self.data["lifetime"]
        lifetime["sessions"] = int(lifetime.get("sessions", 0)) + 1
        self.data["active_session"] = {
            "started_at": utc_now(),
            "ended_at": None,
            "status": "Running",
            "settings": dict(settings),
            "attempts": 0,
            "encounters": 0,
            "unique_pids": 0,
            "duplicates": 0,
            "shinies": 0,
            "rate_per_hour": 0.0,
            "unique_rate_per_hour": 0.0,
            "elapsed_seconds": 0.0,
            "cumulative_shiny_chance_percent": 0.0,
            "rng_repeats_avoided": 0,
            "rng_history_size": 0,
            "effective_rolls": 0,
            "last_rtc": None,
            "last_encounter": None,
        }
        self._save()

    def update_session_stats(self, stats):
        session = self.data.get("active_session")
        if not session:
            return
        session.update({
            "attempts": int(stats.get("attempts", session.get("attempts", 0))),
            "encounters": int(stats.get("encounters", session.get("encounters", 0))),
            "unique_pids": int(stats.get("unique", session.get("unique_pids", 0))),
            "duplicates": int(stats.get("duplicates", session.get("duplicates", 0))),
            "shinies": int(stats.get("shinies", session.get("shinies", 0))),
            "rate_per_hour": float(stats.get("rate", session.get("rate_per_hour", 0.0))),
            "unique_rate_per_hour": float(stats.get("unique_rate", session.get("unique_rate_per_hour", 0.0))),
            "elapsed_seconds": float(stats.get("elapsed", session.get("elapsed_seconds", 0.0))),
            "cumulative_shiny_chance_percent": float(
                stats.get("cumulative", session.get("cumulative_shiny_chance_percent", 0.0))
            ),
            "rng_repeats_avoided": int(
                stats.get("rng_repeats_avoided", session.get("rng_repeats_avoided", 0))
            ),
            "rng_history_size": int(
                stats.get("rng_history_size", session.get("rng_history_size", 0))
            ),
            "effective_rolls": int(
                stats.get("effective_rolls", session.get("effective_rolls", 0))
            ),
        })
        self._save()

    def record_rtc(self, rtc):
        session = self.data.get("active_session")
        if not session:
            return
        session["last_rtc"] = {
            "timestamp": rtc.get("timestamp"),
            "mode": rtc.get("mode"),
            "seed": int(rtc.get("seed", 0)),
            "resets_on_seed": int(rtc.get("resets_on_seed", 0)),
            "error_status": int(rtc.get("error_status", 0)),
            "probe_result": int(rtc.get("probe_result", 0)),
            "status": int(rtc.get("status", 0)),
            "raw_hex": rtc.get("raw_hex"),
        }
        self._save()

    def add_rng_repeat_avoided(self, amount=1):
        amount = int(amount)
        if amount <= 0:
            return
        lifetime = self.data["lifetime"]
        lifetime["rng_repeats_avoided"] = int(
            lifetime.get("rng_repeats_avoided", 0)
        ) + amount
        session = self.data.get("active_session")
        if session:
            session["rng_repeats_avoided"] = int(
                session.get("rng_repeats_avoided", 0)
            ) + amount
        self._save()

    def record_encounter(self, mon):
        session = self.data.get("active_session")
        if not session:
            return

        # Lifetime counters are updated exactly once per successful PK3 event.
        lifetime = self.data["lifetime"]
        lifetime["encounters"] = int(lifetime.get("encounters", 0)) + 1
        if mon.get("duplicate"):
            lifetime["duplicates"] = int(lifetime.get("duplicates", 0)) + 1
        if mon.get("shiny"):
            lifetime["shinies"] = int(lifetime.get("shinies", 0)) + 1

        ivs = mon.get("ivs") or {}
        snapshot = {
            "encounter_no": int(mon.get("encounter_no", 0)),
            "attempt": int(mon.get("attempt", 0)),
            "species": mon.get("species"),
            "species_id": int(mon.get("species_id", 0) or 0),
            "starter": mon.get("species"),
            "chosen": mon.get("chosen"),
            "pid": f"{int(mon.get('pid', 0)):08X}",
            "nature": mon.get("nature"),
            "gender": mon.get("gender"),
            "ability": mon.get("ability"),
            "ability_slot": int(mon.get("ability_slot", 0) or 0),
            "held_item": mon.get("held_item"),
            "held_item_id": int(mon.get("held_item_id", 0) or 0),
            "ivs": {
                "hp": int(ivs.get("hp", 0)),
                "atk": int(ivs.get("atk", 0)),
                "def": int(ivs.get("def", 0)),
                "spa": int(ivs.get("spa", 0)),
                "spd": int(ivs.get("spd", 0)),
                "spe": int(ivs.get("spe", 0)),
            },
            "hidden_power": mon.get("hidden_power"),
            "hidden_power_power": int(mon.get("hidden_power_power", 0)),
            "sv": int(mon.get("sv", 0)),
            "shiny": bool(mon.get("shiny")),
            "anti_shiny": bool(int(mon.get("sv", 0) or 0) >= 65528),
            "duplicate": bool(mon.get("duplicate")),
            "repeated_generation": bool(mon.get("repeated_generation")),
            "encounter_percent": mon.get("encounter_percent"),
            "wurmple_evolution": mon.get("wurmple_evolution"),
            "hunt_state": mon.get("hunt_state"),
            "method": mon.get("method"),
            "encounter_method": mon.get("encounter_method"),
            "game": mon.get("game"),
            "game_code": mon.get("game_code"),
            "revision": mon.get("revision"),
            "map_group": mon.get("map_group"),
            "map_num": mon.get("map_num"),
            "map_name": mon.get("map_name"),
            "rtc_seed": mon.get("rtc_seed"),
            "confirm_rng": mon.get("confirm_rng"),
            "recorded_at": utc_now(),
        }
        session["last_encounter"] = snapshot
        if snapshot["shiny"]:
            recent = self.data.get("recent_shinies") or []
            recent.append(snapshot)
            self.data["recent_shinies"] = recent[-100:]
        self._save()

    def finish_session(self, status):
        session = self.data.get("active_session")
        if not session:
            return
        session["ended_at"] = utc_now()
        session["status"] = str(status)
        self.data["last_session"] = session
        self.data["active_session"] = None
        self._save()

    def reset_lifetime(self):
        self.data["lifetime"] = dict(DEFAULT_DATA["lifetime"])
        self._save()
