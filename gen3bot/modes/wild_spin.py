#!/usr/bin/env python3
"""PokéBot Gen3-style Spin mode for continuous Ruby/Sapphire encounters."""
import math
import threading
import time

from PySide6.QtCore import QThread, Signal

from gen3bot.core.bridge import Bridge
from gen3bot.games.rs import ADDR, battle_active, detect_profile
from gen3bot.modes.wild_engine import WildEncounterEngine
from gen3bot.data.rs_world import RSWorldDatabase, wurmple_evolution
from gen3bot.data.hunt_policy import HuntPolicy
from gen3bot.pokemon.pk3 import apply_species_metadata
from gen3bot.ui.shiny_sound import play_shiny_sound

ODDS = 8192
SPIN_DIRECTIONS = ("UP", "RIGHT", "DOWN", "LEFT")


class WildSpinWorker(QThread):
    connected = Signal(dict)
    encounter = Signal(dict)
    outcome = Signal(dict)
    stats = Signal(dict)
    log = Signal(str)
    stopped = Signal(str)
    error = Signal(str)

    def __init__(self, ip, fast_forward=True, shiny_sound=True):
        super().__init__()
        self.ip = ip
        self.fast_forward = bool(fast_forward)
        self.shiny_sound = bool(shiny_sound)
        self._stop_requested = threading.Event()
        self.bridge = None
        self.engine = None
        self.profile = None
        self.started_at = None
        self.encounters = 0
        self.effective_rolls = 0
        self.duplicates = 0
        self.shinies = 0
        self.spin_inputs = 0
        self.map_state = None
        self.world = None
        self.hunt_policy = HuntPolicy()

    def request_stop(self):
        self._stop_requested.set()

    def should_stop(self):
        return self._stop_requested.is_set()

    def safe_fast_off(self):
        if self.engine is not None:
            try:
                self.engine.set_fast(False, force=True)
                return
            except Exception:
                pass
        if self.bridge is not None:
            try:
                self.bridge.fast(False)
            except Exception:
                pass

    def emit_stats(self):
        if self.started_at is None:
            return
        elapsed = max(0.001, time.monotonic() - self.started_at)
        rate = self.encounters / elapsed * 3600.0
        effective_rate = self.effective_rolls / elapsed * 3600.0
        cumulative = (1.0 - ((ODDS - 1) / ODDS) ** self.effective_rolls) * 100.0
        eta_hours = (
            max(0, ODDS - self.effective_rolls) / effective_rate
            if effective_rate > 0 else math.inf
        )
        self.stats.emit({
            "encounters": self.encounters,
            "effective_rolls": self.effective_rolls,
            "duplicates": self.duplicates,
            "shinies": self.shinies,
            "spin_inputs": self.spin_inputs,
            "rate": rate,
            "effective_rate": effective_rate,
            "elapsed": elapsed,
            "cumulative": cumulative,
            "eta_hours": eta_hours,
        })

    def run(self):
        try:
            self.bridge = Bridge(self.ip)
            pong = self.bridge.cmd("PING")
            self.profile = detect_profile(self.bridge)

            party_count = self.bridge.read(ADDR["gPlayerPartyCount"], 1)[0]
            if party_count < 1:
                raise RuntimeError(
                    "Wild encounters require at least one Pokémon in the party. "
                    "Finish the starter sequence/save first."
                )

            self.engine = WildEncounterEngine(
                self.bridge,
                self.fast_forward,
                telemetry=self.log.emit,
            )
            self.world = RSWorldDatabase(self.bridge, self.profile)

            # A worker restart while the previous battle is still active must
            # never turn the same PID into a fresh encounter count.
            self.engine.ensure_clean_start()
            self.map_state = self.engine.remember_start_position()
            if self.map_state.get("x") is None or self.map_state.get("y") is None:
                raise RuntimeError(
                    "Could not resolve the player's overworld tile. Start Spin while "
                    "standing still in the overworld."
                )

            # Verify the requested fast-forward override once, then gate it OFF
            # for all stationary direction inputs.  It is only re-enabled by the
            # battle engine during passive, input-free transitions.
            fast_test_status = None
            if self.fast_forward:
                self.engine.set_fast(True, force=True)
                time.sleep(0.10)
                fast_test_status = self.bridge.cmd("STATUS")
                if "FAST=1" not in fast_test_status:
                    raise RuntimeError(
                        "Fast-forward did not remain enabled. Install the v0p21 bridge CIA "
                        "or disable fast-forward."
                    )
                self.engine.set_fast(False, force=True)
            else:
                self.engine.set_fast(False, force=True)

            # Report the actual post-gating state in support bundles.
            status = self.bridge.cmd("STATUS")
            if "FAST=0" not in status:
                raise RuntimeError(
                    "Could not return mGBA to FAST=0 before stationary Spin input."
                )

            self.connected.emit({
                "pong": pong,
                "status": status,
                "fast_override_test_status": fast_test_status,
                "fast_forward_requested": self.fast_forward,
                "fast_forward_input_gated": True,
                **self.profile,
                **self.map_state,
            })
            self.started_at = time.monotonic()
            self.log.emit(
                "Wild Spin started. Stationary direction input is locked to FAST=0; "
                "fast-forward is used only for passive battle transitions."
            )

            direction_index = 0
            while not self.should_stop():
                if battle_active(self.bridge):
                    # Battle transition is now passive: if requested, acceleration
                    # is safe until the live action menu is detected.  The engine
                    # returns to FAST=0 before any menu input.
                    if self.fast_forward:
                        self.engine.set_fast(True)
                    time.sleep(0.020 if self.fast_forward else 0.075)
                    mon, _flags = self.engine.wait_for_enemy(self.should_stop)
                    self.encounters += 1
                    duplicate, _fingerprint = self.engine.classify_generation(mon)
                    if duplicate:
                        self.duplicates += 1
                    else:
                        self.effective_rolls += 1

                    mon = dict(mon)
                    # Enrich PK3 data from the running R/S ROM so every wild
                    # species is known without a hand-maintained species list.
                    try:
                        meta = self.world.species_meta(mon["species_id"])
                        mon = apply_species_metadata(mon, meta, self.world.item_name(mon.get("held_item_id", 0)))
                    except Exception as enrich_error:
                        self.log.emit(f"WORLD_META_FALLBACK {enrich_error}")
                    mon["encounter_percent"] = self.world.species_percent(
                        self.map_state["map_group"], self.map_state["map_num"],
                        mon["species_id"], "land"
                    )
                    mon["wurmple_evolution"] = wurmple_evolution(mon["pid"], mon["species_id"])
                    mon["hunt_state"] = self.hunt_policy.state(
                        self.profile["code"], self.map_state["map_group"],
                        self.map_state["map_num"], "land", mon["species_id"]
                    )
                    mon.update({
                        "encounter_no": self.encounters,
                        "attempt": self.encounters,
                        "duplicate": duplicate,
                        "repeated_generation": duplicate,
                        "effective_roll_no": self.effective_rolls,
                        "method": "Spin",
                        "encounter_method": "land",
                        "game": self.profile["name"],
                        "game_code": self.profile["code"],
                        "revision": self.profile["revision"],
                        "version": self.profile["version"],
                        "map_group": self.map_state["map_group"],
                        "map_num": self.map_state["map_num"],
                        "map_name": self.map_state.get("map_name"),
                    })
                    self.encounter.emit(mon)

                    if mon["shiny"]:
                        self.shinies += 1
                        self.outcome.emit({
                            "encounter_no": self.encounters,
                            "result": "Held — Shiny",
                            "outcome": "held_shiny",
                        })
                        self.safe_fast_off()
                        self.emit_stats()
                        if self.shiny_sound:
                            play_shiny_sound()
                        self.stopped.emit(f"SHINY {mon['species']} FOUND — battle held")
                        return

                    self.emit_stats()
                    self.engine.run_away(self.should_stop)
                    self.engine.wait_for_overworld(self.should_stop)
                    # Only call the encounter "Ran Away" once battle-active has
                    # cleared and the overworld return has been RAM-confirmed.
                    self.outcome.emit({
                        "encounter_no": self.encounters,
                        "result": "Ran Away",
                        "outcome": "ran_away",
                    })
                    continue

                direction = SPIN_DIRECTIONS[direction_index]
                direction_index = (direction_index + 1) % len(SPIN_DIRECTIONS)
                turned = self.engine.stationary_turn(direction, self.should_stop)
                if turned:
                    self.spin_inputs += 1

                if self.spin_inputs and self.spin_inputs % 64 == 0:
                    self.engine.check_no_drift()
                    self.emit_stats()

                # stationary_turn owns the minimum safe pacing/settle window.
                time.sleep(0.002)

            self.safe_fast_off()
            self.stopped.emit("Wild Spin stopped by user.")

        except Exception as exc:
            self.safe_fast_off()
            self.error.emit(str(exc))
