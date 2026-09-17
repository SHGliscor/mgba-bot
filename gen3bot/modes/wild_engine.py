#!/usr/bin/env python3
"""Shared continuous wild-encounter battle engine for Ruby/Sapphire.

Trigger modes such as Spin, Bunny Hop, Sweet Scent and Fishing should feed this
engine rather than duplicating battle detection, PK3 validation, duplicate
classification and run-away navigation.
"""
import time

from gen3bot.games.rs import (
    ADDR,
    B_ACTION_RUN,
    CONTROLLER_PRINTSTRING,
    CONTROLLER_PRINTSTRINGPLAYERONLY,
    BATTLE_TYPE_TRAINER,
    action_menu_candidate_state,
    action_reply,
    battle_active,
    battle_controller_exec_flags,
    battle_text_controller_state,
    battle_type_flags,
    battlers_count,
    choose_action_state,
    player_state,
)
from gen3bot.pokemon.pk3 import decode_mon, generation_fingerprint


class WildEncounterEngine:
    def __init__(self, bridge, fast_forward=True, telemetry=None):
        self.bridge = bridge
        self.fast_forward = bool(fast_forward)
        self.telemetry = telemetry
        self.fingerprints = set()
        self.start_position = None
        self.start_object_event_id = None
        self._fast_state = None

    @property
    def poll_sleep(self):
        # Menu/state polling is intentionally conservative even when the user
        # requested fast-forward.  Inputs are always issued at FAST=0.
        return 0.010 if self.fast_forward else 0.020

    def log(self, message):
        if self.telemetry is not None:
            try:
                self.telemetry(str(message))
            except Exception:
                pass

    def set_fast(self, enabled, force=False):
        enabled = bool(enabled)
        if not force and self._fast_state is enabled:
            return
        self.bridge.fast(enabled)
        self._fast_state = enabled

    def remember_start_position(self):
        state = player_state(self.bridge)
        if state.get("x") is not None and state.get("y") is not None:
            self.start_position = (
                state["map_group"], state["map_num"], state["x"], state["y"]
            )
            self.start_object_event_id = state.get("object_event_id")
        return state

    def _quick_position(self):
        """Read one ObjectEvent for low-overhead stationary-spin validation."""
        oid = self.start_object_event_id
        if oid is None or not (0 <= int(oid) < 16):
            return None
        raw = self.bridge.read(ADDR["gObjectEvents"] + int(oid) * 0x24, 0x1A)
        flags = int.from_bytes(raw[0:4], "little")
        if not (flags & 1):
            return None
        x = int.from_bytes(raw[0x10:0x12], "little") - 7
        y = int.from_bytes(raw[0x12:0x14], "little") - 7
        facing_code = int.from_bytes(raw[0x18:0x1A], "little") & 0xF
        facing = {1: "DOWN", 2: "UP", 3: "LEFT", 4: "RIGHT"}.get(facing_code)
        return x, y, facing

    def classify_generation(self, mon):
        fingerprint = generation_fingerprint(mon)
        duplicate = fingerprint in self.fingerprints
        if not duplicate:
            self.fingerprints.add(fingerprint)
        return duplicate, fingerprint

    def ensure_clean_start(self):
        """Refuse to count a battle that existed before this worker started."""
        if not battle_active(self.bridge):
            return
        flags = battle_type_flags(self.bridge)
        self.set_fast(False, force=True)
        detail = ""
        try:
            raw = self.bridge.read(ADDR["gEnemyParty"], 100)
            mon = decode_mon(raw)
            if mon is not None:
                shiny = " SHINY" if mon.get("shiny") else ""
                detail = (
                    f" Existing opponent: {mon.get('species')} PID "
                    f"{int(mon.get('pid', 0)):08X}{shiny}."
                )
        except Exception:
            pass
        raise RuntimeError(
            "Existing battle detected before Wild Spin started. Safety stop: "
            "this battle was NOT counted as a new encounter. Resolve or leave "
            "the current battle manually, return to the overworld, then start "
            f"the hunt again.{detail}"
        )

    def wait_for_enemy(self, stop_requested=lambda: False, timeout=6.0):
        """Wait for a checksum-valid, stable enemy PK3 after battle startup."""
        deadline = time.monotonic() + timeout
        stable_key = None
        stable_reads = 0
        last_flags = 0
        last_count = 0

        while time.monotonic() < deadline and not stop_requested():
            active = battle_active(self.bridge)
            flags = battle_type_flags(self.bridge)
            count = battlers_count(self.bridge)
            last_flags, last_count = flags, count

            if not active:
                time.sleep(self.poll_sleep)
                continue

            if flags & BATTLE_TYPE_TRAINER:
                raise RuntimeError(
                    "Trainer battle detected while a wild mode was active. "
                    "Safety stop: trainer battles are never automated by the wild engine."
                )

            # A normal single wild battle has two battlers. Requiring the battle
            # structures to be initialised avoids accepting stale gEnemyParty data
            # from the previous battle during the transition frame.
            if active and count >= 2:
                raw = self.bridge.read(ADDR["gEnemyParty"], 100)
                mon = decode_mon(raw)
                if mon is not None:
                    key = (mon["species_id"], mon["pid"], mon["checksum"])
                    if key == stable_key:
                        stable_reads += 1
                    else:
                        stable_key = key
                        stable_reads = 1
                    if stable_reads >= 2:
                        return mon, flags

            time.sleep(self.poll_sleep)

        raise RuntimeError(
            "Battle began but no stable checksum-valid enemy PK3 appeared "
            f"(flags=0x{last_flags:04X}, battlers={last_count})."
        )

    def _menu_state(self, battler=None, relaxed=False):
        """Return a current action-menu state.

        Strict mode requires the expected CONTROLLER_CHOOSEACTION command byte.
        Relaxed mode is only used after the menu has been *proven interactive*
        by an observed, predicted gActionSelectionCursor change caused by one
        harmless D-pad pulse.  A is never sent from an unproven candidate.
        """
        state = choose_action_state(self.bridge)
        if state is None and relaxed:
            state = action_menu_candidate_state(self.bridge)
        if state is None:
            return None
        if battler is not None and state.get("battler") != battler:
            return None
        return state

    def _prove_action_menu_candidate(self, candidate, stop_requested, timeout=0.45):
        """Prove a broad player-controller candidate is the 2x2 action menu.

        D-pad is harmless during battle text/animations.  The proof succeeds
        only if the action cursor moves to the exact value predicted by the
        Fight/Bag/Pokémon/Run 2x2 layout while the same player controller bit is
        live.  This lets retail/mGBA builds proceed even when gBattleBufferA[0]
        does not expose command 18 reliably, without ever gambling an A press.
        """
        if candidate is None:
            return None
        battler = int(candidate.get("battler", 0))
        cursor = int(candidate.get("cursor", 0)) & 0x03
        command = int(candidate.get("command", 0)) & 0xFF

        # Toggle only the horizontal bit.  This always predicts a different
        # action cursor value and never advances text like A/B could.
        if cursor & 1:
            key = "LEFT"
            wanted = cursor ^ 1
        else:
            key = "RIGHT"
            wanted = cursor ^ 1

        self.log(
            "BATTLE_MENU_PROBE_SENT "
            f"battler={battler} command={command} cursor={cursor} "
            f"key={key} wanted={wanted}"
        )
        self.bridge.key1(key)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not stop_requested():
            if not battle_active(self.bridge):
                return None

            # If the strict command becomes visible at any point, use it.
            strict = choose_action_state(self.bridge)
            if strict is not None and strict.get("battler") == battler:
                strict = dict(strict)
                strict["verification"] = "strict"
                self.log(
                    "BATTLE_MENU_READY "
                    f"battler={battler} cursor={strict['cursor']} "
                    f"command={strict['command']} verification=strict "
                    f"exec=0x{strict['exec_flags']:08X}"
                )
                return strict

            current = action_menu_candidate_state(self.bridge)
            if current is not None and current.get("battler") == battler:
                got = int(current.get("cursor", 0)) & 0x03
                if got == wanted:
                    current = dict(current)
                    current["verification"] = "cursor_probe"
                    self.log(
                        "BATTLE_MENU_PROBE_CONFIRMED "
                        f"battler={battler} command={current['command']} "
                        f"before={cursor} after={got}"
                    )
                    self.log(
                        "BATTLE_MENU_READY "
                        f"battler={battler} cursor={got} "
                        f"command={current['command']} verification=cursor_probe "
                        f"exec=0x{current['exec_flags']:08X}"
                    )
                    return current
            time.sleep(self.poll_sleep)

        self.log(
            "BATTLE_MENU_PROBE_NO_CHANGE "
            f"battler={battler} command={command} cursor={cursor}"
        )
        return None

    def _wait_for_action_menu(self, stop_requested, deadline):
        """Wait for and prove the live Fight/Bag/Pokémon/Run menu.

        Preferred authority remains CONTROLLER_CHOOSEACTION (18).  If that
        command byte is not readable on a retail/mGBA run, a safe D-pad cursor
        proof is used as a fallback.  Pre-menu battle text is detected directly
        from live PRINTSTRING controller commands across all four battlers, so
        "Wild <species> appeared!" can be advanced even when the text belongs
        to the opponent-side controller.  Once the action menu is interactive,
        A is never pressed until RUN is RAM-confirmed.
        """
        passive_fast = self.fast_forward
        if passive_fast:
            self.set_fast(True)

        last_exec = 0
        last_candidate = None
        next_probe = 0.0
        intro_a_presses = 0
        next_intro_a = 0.0

        while time.monotonic() < deadline and not stop_requested():
            active = battle_active(self.bridge)
            flags = battle_type_flags(self.bridge)
            if not active:
                self.set_fast(False)
                return None
            if flags & BATTLE_TYPE_TRAINER:
                self.set_fast(False)
                raise RuntimeError("Trainer battle appeared during run-away sequence.")

            state = choose_action_state(self.bridge)
            if state is not None:
                # Inputs are never sent under mGBA fast-forward.  Freeze back to
                # normal speed, then re-read the authoritative menu command.
                self.set_fast(False)
                time.sleep(0.025)
                state = choose_action_state(self.bridge)
                if state is not None:
                    state = dict(state)
                    state["verification"] = "strict"
                    self.log(
                        "BATTLE_MENU_READY "
                        f"battler={state['battler']} cursor={state['cursor']} "
                        f"command={state['command']} verification=strict "
                        f"exec=0x{state['exec_flags']:08X}"
                    )
                    return state

            try:
                last_exec = battle_controller_exec_flags(self.bridge)
                last_candidate = action_menu_candidate_state(self.bridge)
            except Exception:
                last_candidate = None

            # Ruby/Sapphire can leave the opening "Wild <species> appeared!"
            # text on an opponent-side controller (battler 1/3).  HF6/HF7 only
            # looked for player-side action-menu candidates (0/2), so in that
            # state no probe ran and no A could ever be sent.  PRINTSTRING (16)
            # and PRINTSTRINGPLAYERONLY (17) are direct controller authority
            # that battle text is live.  Re-read after returning to FAST=0 and
            # send A only while that exact text command is still active.
            now = time.monotonic()
            if intro_a_presses < 6 and now >= next_intro_a:
                try:
                    text_state = battle_text_controller_state(self.bridge)
                except Exception:
                    text_state = None
                if text_state is not None:
                    self.set_fast(False)
                    time.sleep(0.025)

                    # The command may have completed while fast-forward was
                    # being disabled.  Never let a late A fall through into a
                    # newly-created Fight/Bag/Pokémon/Run menu.
                    strict_now = choose_action_state(self.bridge)
                    try:
                        text_now = battle_text_controller_state(self.bridge)
                    except Exception:
                        text_now = None
                    if strict_now is not None:
                        strict_now = dict(strict_now)
                        strict_now["verification"] = "strict"
                        self.log(
                            "BATTLE_MENU_READY "
                            f"battler={strict_now['battler']} cursor={strict_now['cursor']} "
                            f"command={strict_now['command']} verification=strict "
                            f"exec=0x{strict_now['exec_flags']:08X}"
                        )
                        return strict_now
                    if (
                        text_now is not None
                        and text_now.get("battler") == text_state.get("battler")
                        and text_now.get("command") == text_state.get("command")
                    ):
                        intro_a_presses += 1
                        self.bridge.key1("A")
                        self.log(
                            "WILD_TEXT_A_SENT "
                            f"press={intro_a_presses} battler={text_now['battler']} "
                            f"command={text_now['command']} reason=live_printstring"
                        )
                        # Give the text printer time to consume A and hand the
                        # battle controller back before probing for the menu.
                        time.sleep(0.12)
                        next_intro_a = time.monotonic() + 0.18
                        next_probe = time.monotonic() + 0.10
                        if passive_fast and battle_active(self.bridge):
                            self.set_fast(True)
                        time.sleep(self.poll_sleep)
                        continue

            # A player controller bit is live but the expected command byte is
            # not.  Probe only with D-pad and only at FAST=0.  If this is still
            # intro/text, the action cursor will not make the predicted change.
            now = time.monotonic()
            if last_candidate is not None and now >= next_probe:
                self.set_fast(False)
                time.sleep(0.025)
                candidate = action_menu_candidate_state(self.bridge)
                proved = self._prove_action_menu_candidate(
                    candidate, stop_requested, timeout=0.45
                )
                if proved is not None:
                    return proved

                # A non-reactive D-pad probe proves that the 2x2 action menu is
                # not interactive *at this moment*.  On Ruby/Sapphire the wild
                # intro can then be sitting on "Wild <species> appeared!" and
                # waiting for A before the Fight/Bag/Pokémon/Run menu is created.
                # Send A only after that negative menu proof, never blindly.
                # Re-run the proof before every additional A so a newly-opened
                # action menu can never receive an accidental confirmation.
                now = time.monotonic()
                if intro_a_presses < 3 and now >= next_intro_a:
                    strict_now = choose_action_state(self.bridge)
                    if strict_now is not None:
                        self.set_fast(False)
                        strict_now = dict(strict_now)
                        strict_now["verification"] = "strict"
                        self.log(
                            "BATTLE_MENU_READY "
                            f"battler={strict_now['battler']} cursor={strict_now['cursor']} "
                            f"command={strict_now['command']} verification=strict "
                            f"exec=0x{strict_now['exec_flags']:08X}"
                        )
                        return strict_now

                    self.set_fast(False)
                    intro_a_presses += 1
                    command = None if candidate is None else candidate.get("command")
                    battler = None if candidate is None else candidate.get("battler")
                    self.bridge.key1("A")
                    self.log(
                        "WILD_INTRO_A_SENT "
                        f"press={intro_a_presses} battler={battler} command={command} "
                        "reason=action_menu_probe_no_change"
                    )
                    # Let Ruby consume the text advance before probing again.
                    time.sleep(0.12)
                    next_intro_a = time.monotonic() + 0.30

                next_probe = time.monotonic() + 0.18
                if passive_fast and battle_active(self.bridge):
                    self.set_fast(True)

            time.sleep(self.poll_sleep)

        self.set_fast(False)
        detail = ""
        if last_candidate is not None:
            detail = (
                f" candidate=b{last_candidate.get('battler')}"
                f"/cursor{last_candidate.get('cursor')}"
                f"/cmd{last_candidate.get('command')}"
            )
        raise RuntimeError(
            "Timed out waiting for the live Fight/Bag/Pokémon/Run command menu "
            f"(controller_exec=0x{last_exec:08X}{detail})."
        )

    def _wait_for_cursor(
        self, battler, wanted, stop_requested, timeout=0.70, relaxed=False
    ):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline and not stop_requested():
            state = self._menu_state(battler=battler, relaxed=relaxed)
            if state is None:
                time.sleep(self.poll_sleep)
                continue
            last = state
            if state["cursor"] == wanted:
                return state
            time.sleep(self.poll_sleep)
        return last


    def _navigate_run_cursor(self, state, stop_requested):
        battler = state["battler"]
        relaxed = state.get("verification") == "cursor_probe"
        self.log(
            "RUN_CURSOR_BEFORE "
            f"battler={battler} cursor={state['cursor']} "
            f"verification={state.get('verification', 'unknown')}"
        )

        # Closed-loop navigation.  Strict command-byte authority is preferred.
        # If the menu was proven by cursor-probe, continue with the same
        # controller bit + predicted action-cursor RAM authority.
        for _ in range(6):
            if stop_requested():
                return None
            state = self._menu_state(battler=battler, relaxed=relaxed)
            if state is None:
                time.sleep(self.poll_sleep)
                continue
            cursor = state["cursor"]
            if cursor == B_ACTION_RUN:
                state = dict(state)
                state["verification"] = "cursor_probe" if relaxed else "strict"
                self.log(
                    f"RUN_CONFIRMED battler={battler} cursor=3 "
                    f"verification={state['verification']}"
                )
                return state

            if cursor == 0:
                key, wanted, tag = "DOWN", 2, "RUN_DOWN_RESULT"
            elif cursor == 1:
                key, wanted, tag = "DOWN", 3, "RUN_DOWN_RESULT"
            elif cursor == 2:
                key, wanted, tag = "RIGHT", 3, "RUN_RIGHT_RESULT"
            else:
                raise RuntimeError(f"Invalid battle action cursor value: {cursor}")

            self.bridge.key1(key)
            updated = self._wait_for_cursor(
                battler, wanted, stop_requested, timeout=0.70, relaxed=relaxed
            )
            got = None if updated is None else updated.get("cursor")
            self.log(f"{tag} key={key} wanted={wanted} got={got}")
            if updated is not None and got == wanted:
                state = updated
                continue

            # One bounded retry is preferable to blindly flooding directions.
            current = self._menu_state(battler=battler, relaxed=relaxed)
            if current is not None and current.get("cursor") == cursor:
                self.bridge.key1(key)
                updated = self._wait_for_cursor(
                    battler, wanted, stop_requested, timeout=0.70, relaxed=relaxed
                )
                got = None if updated is None else updated.get("cursor")
                self.log(f"{tag}_RETRY key={key} wanted={wanted} got={got}")
                if updated is not None and got == wanted:
                    state = updated
                    continue

        current = self._menu_state(battler=battler, relaxed=relaxed)
        cursor = None if current is None else current.get("cursor")
        raise RuntimeError(
            f"Could not RAM-verify the RUN cursor (last cursor={cursor})."
        )

    def _confirm_run_submission(
        self, battler, stop_requested, timeout=0.90, previous_reply_raw=None
    ):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not stop_requested():
            active = battle_active(self.bridge)
            if not active:
                return True, "battle ended"

            candidate = action_menu_candidate_state(self.bridge)
            menu_still_live = (
                candidate is not None and candidate.get("battler") == battler
            )

            try:
                reply = action_reply(self.bridge, battler)
                raw = reply["raw"]
                changed = previous_reply_raw is None or raw != previous_reply_raw
                if (
                    changed
                    and reply["command"] == 33
                    and reply["action"] == B_ACTION_RUN
                ):
                    return True, "new gBattleBufferB action=RUN"
            except Exception:
                pass

            # The player controller bit clearing is direct proof that the menu
            # handler consumed A.  Unlike the old strict-only check, this also
            # works when command 18 is not readable.
            if not menu_still_live:
                return True, "player action controller completed"

            time.sleep(self.poll_sleep)

        return False, "RUN submission did not leave the live player controller"


    def run_away(self, stop_requested=lambda: False, timeout=15.0):
        """RAM-verified RUN selection, retrying if escape fails.

        No B presses are used.  The engine waits for Ruby/Sapphire's live
        CONTROLLER_CHOOSEACTION command, navigates its cursor closed-loop at
        FAST=0, confirms the RUN action was submitted, then waits for either an
        overworld exit or another live command menu (failed escape -> retry).
        """
        deadline = time.monotonic() + timeout
        attempts = 0

        while time.monotonic() < deadline and not stop_requested():
            state = self._wait_for_action_menu(stop_requested, deadline)
            if state is None:
                return

            state = self._navigate_run_cursor(state, stop_requested)
            if state is None:
                return
            battler = state["battler"]
            relaxed = state.get("verification") == "cursor_probe"

            # Re-validate the same proven menu path and exact cursor immediately
            # before A.  Candidate state alone is never enough; relaxed mode is
            # permitted only because _wait_for_action_menu already proved this
            # menu by a predicted action-cursor change.
            final = self._menu_state(battler=battler, relaxed=relaxed)
            if final is None or final["cursor"] != 3:
                raise RuntimeError(
                    "RUN cursor lost before confirmation; no A press was sent."
                )

            try:
                previous_reply_raw = action_reply(self.bridge, battler)["raw"]
            except Exception:
                previous_reply_raw = None

            attempts += 1
            self.bridge.key1("A")
            self.log(
                f"RUN_A_SENT attempt={attempts} battler={battler} "
                f"verification={'cursor_probe' if relaxed else 'strict'}"
            )
            accepted, why = self._confirm_run_submission(
                battler,
                stop_requested,
                timeout=0.90,
                previous_reply_raw=previous_reply_raw,
            )
            self.log(f"RUN_SUBMISSION accepted={int(bool(accepted))} reason={why}")
            if not accepted:
                raise RuntimeError(
                    "RUN was highlighted but Ruby/Sapphire did not accept the A press."
                )

            # Once input has been consumed, fast-forward is safe again while the
            # escape result / enemy turn resolves.  If escape fails, the next
            # menu detection immediately returns to FAST=0 before more input.
            if self.fast_forward:
                self.set_fast(True)

            while time.monotonic() < deadline and not stop_requested():
                active = battle_active(self.bridge)
                flags = battle_type_flags(self.bridge)
                if not active:
                    self.set_fast(False)
                    self.log(f"OVERWORLD_RECOVERED run_attempts={attempts}")
                    return
                if flags & BATTLE_TYPE_TRAINER:
                    self.set_fast(False)
                    raise RuntimeError("Trainer battle appeared during run-away sequence.")

                # Any live player-controller candidate is enough to return
                # to the outer menu wait.  The outer loop will then prove the
                # action menu strictly or by safe cursor probe before any input
                # beyond D-pad is sent.
                menu = action_menu_candidate_state(self.bridge)
                if menu is not None:
                    self.set_fast(False)
                    self.log(
                        "RUN_RETRY_CONTROLLER_READY "
                        f"attempt={attempts + 1} cursor={menu['cursor']} "
                        f"command={menu['command']}"
                    )
                    break
                time.sleep(self.poll_sleep)

        self.set_fast(False)
        diag = self._battle_diag()
        raise RuntimeError(
            "Timed out running from wild battle after RAM-verified RUN navigation. "
            + diag
        )

    def _battle_diag(self):
        parts = []
        try:
            parts.append(f"active={int(battle_active(self.bridge))}")
            parts.append(f"flags=0x{battle_type_flags(self.bridge):04X}")
        except Exception:
            pass
        try:
            parts.append(
                f"controller_exec=0x{battle_controller_exec_flags(self.bridge):08X}"
            )
        except Exception:
            pass
        try:
            state = choose_action_state(self.bridge)
            candidate = action_menu_candidate_state(self.bridge)
            if state is not None:
                parts.append(
                    f"menu=strict:b{state['battler']}/cursor{state['cursor']}/cmd{state['command']}"
                )
            elif candidate is not None:
                parts.append(
                    f"menu=candidate:b{candidate['battler']}/cursor{candidate['cursor']}"
                    f"/cmd{candidate['command']}"
                )
            else:
                parts.append("menu=none")
        except Exception:
            pass
        try:
            reply = action_reply(self.bridge, 0)
            parts.append(
                "reply=" + reply["raw"].hex().upper()
            )
        except Exception:
            pass
        return " ".join(parts)

    def wait_for_overworld(self, stop_requested=lambda: False, timeout=5.0):
        deadline = time.monotonic() + timeout
        clear_reads = 0
        self.set_fast(False)
        while time.monotonic() < deadline and not stop_requested():
            if not battle_active(self.bridge):
                clear_reads += 1
                if clear_reads >= 3:
                    self.check_no_drift()
                    return
            else:
                clear_reads = 0
            time.sleep(self.poll_sleep)
        raise RuntimeError("Battle ended but overworld did not settle before timeout.")

    def _safe_spin_direction(self, requested=None):
        """Return a direction guaranteed not to match the live facing.

        In Ruby/Sapphire, pressing the direction the player is already facing
        can advance one tile.  Stationary Spin must therefore choose a turn
        relative to the *current* ObjectEvent facing instead of assuming a
        fixed UP/RIGHT/DOWN/LEFT sequence is safe after restarts or ignored
        inputs.  Clockwise rotation gives a deterministic next direction.
        """
        quick = self._quick_position()
        if quick is None:
            return None, None
        x, y, facing = quick
        if self.start_position is not None and (x, y) != self.start_position[2:4]:
            raise RuntimeError(
                "Stationary Spin drift safety stop: player moved from "
                f"{self.start_position} to "
                f"({self.start_position[0]}, {self.start_position[1]}, {x}, {y})."
            )

        clockwise = {
            "UP": "RIGHT",
            "RIGHT": "DOWN",
            "DOWN": "LEFT",
            "LEFT": "UP",
        }
        if facing not in clockwise:
            return None, facing
        wanted = str(requested).upper() if requested is not None else None
        if wanted in clockwise.values() and wanted != facing:
            return wanted, facing
        return clockwise[facing], facing

    def _post_turn_settle(self, stop_requested, duration=0.10):
        """Allow the field object to finish its turn before another KEY1.

        Support capture 20:45:08 showed a second direction sent ~50 ms after
        the previous turn could be ignored even though facing RAM had already
        changed.  A short no-input settle window prevents command flooding while
        keeping X/Y and battle state under continuous RAM supervision.
        """
        deadline = time.monotonic() + max(0.0, float(duration))
        while time.monotonic() < deadline and not stop_requested():
            if battle_active(self.bridge):
                return True
            self.check_no_drift()
            time.sleep(self.poll_sleep)
        return not stop_requested()

    def stationary_turn(self, direction=None, stop_requested=lambda: False):
        """Perform one RAM-safe stationary facing change.

        HF4 derives the final direction from the *live* ObjectEvent facing so a
        restart while already facing UP can never send UP and accidentally walk
        one tile.  Exactly one direction pulse is sent at FAST=0.  If the game
        ignores that pulse, the method reports it and returns without treating
        it as a fatal safety error; the next loop iteration will recompute a
        safe direction from live RAM.
        """
        requested = None if direction is None else str(direction).upper()
        if requested is not None and requested not in ("UP", "RIGHT", "DOWN", "LEFT"):
            raise ValueError(f"Invalid spin direction: {requested}")

        self.set_fast(False)
        if stop_requested():
            return False

        direction, before_facing = self._safe_spin_direction(requested)
        if direction is None:
            if battle_active(self.bridge):
                return True
            raise RuntimeError(
                "Stationary Spin could not resolve the player's live facing. "
                "No direction input was sent."
            )

        if requested is not None and requested != direction:
            self.log(
                f"SPIN_DIRECTION_GUARD requested={requested} "
                f"live_facing={before_facing} using={direction}"
            )

        self.bridge.key1(direction)
        self.log(
            f"SPIN_TURN_SENT direction={direction} previous_facing={before_facing}"
        )

        deadline = time.monotonic() + 0.60
        last_facing = before_facing
        polls = 0
        while time.monotonic() < deadline and not stop_requested():
            quick = self._quick_position()
            polls += 1

            if quick is not None and self.start_position is not None:
                x, y, facing = quick
                last_facing = facing
                if (x, y) != self.start_position[2:4]:
                    raise RuntimeError(
                        "Stationary Spin drift safety stop: player moved from "
                        f"{self.start_position} to "
                        f"({self.start_position[0]}, {self.start_position[1]}, {x}, {y})."
                    )

                if facing == direction:
                    self.log(
                        f"SPIN_TURN_CONFIRMED direction={direction} polls={polls}"
                    )
                    self._post_turn_settle(stop_requested, duration=0.10)
                    return True

                if battle_active(self.bridge):
                    self.log(
                        f"SPIN_BATTLE_STARTED direction={direction} "
                        f"last_facing={facing} polls={polls}"
                    )
                    return True
            else:
                if battle_active(self.bridge):
                    self.log(
                        f"SPIN_BATTLE_STARTED direction={direction} "
                        f"last_facing=unavailable polls={polls}"
                    )
                    return True
                self.check_no_drift()

            time.sleep(self.poll_sleep)

        if stop_requested():
            return False

        # An ignored turn is not itself unsafe.  The invariant is position: if
        # X/Y is unchanged and no battle is active, simply let the caller loop
        # and recompute another safe turn from the current facing.
        self.check_no_drift()
        if battle_active(self.bridge):
            return True
        self.log(
            f"SPIN_TURN_IGNORED direction={direction} "
            f"last_facing={last_facing} polls={polls}"
        )
        self._post_turn_settle(stop_requested, duration=0.10)
        return False

    def check_no_drift(self):
        if self.start_position is None:
            return

        quick = self._quick_position()
        if quick is not None:
            x, y, _facing = quick
            current = (
                self.start_position[0], self.start_position[1], x, y
            )
        else:
            state = player_state(self.bridge)
            current = (
                state.get("map_group"), state.get("map_num"),
                state.get("x"), state.get("y")
            )

        if None not in current[2:] and current != self.start_position:
            raise RuntimeError(
                "Stationary Spin drift safety stop: player moved from "
                f"{self.start_position} to {current}."
            )
