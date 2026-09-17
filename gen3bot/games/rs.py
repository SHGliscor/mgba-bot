#!/usr/bin/env python3
"""Shared Pokémon Ruby/Sapphire English v1.0-v1.1 runtime profile.

The relevant wild-battle RAM symbols are identical across AXVE/AXPE revisions
00 and 01. Starter ROM callback differences remain isolated inside the frozen
starter module and are not duplicated here.
"""
from ruby_starter_hunter import (
    ADDR as STARTER_ADDR,
    detect_supported_game,
    player_state as frozen_player_state,
)

ADDR = dict(STARTER_ADDR)
ADDR.update({
    "gBattleTypeFlags": 0x020239F8,
    "gBattleBufferA": 0x02023A60,
    "gBattleBufferB": 0x02024260,
    "gActiveBattler": 0x02024A60,
    "gBattleControllerExecFlags": 0x02024A64,
    "gBattlersCount": 0x02024A68,
    "gBattleOutcome": 0x02024D26,
    "gActionSelectionCursor": 0x02024E60,
    "gBattlerInMenuId": 0x02024E6C,
    "gBattleMainFunc": 0x030042D4,
    "gMapHeader": 0x0202E828,
})

# gBattleTypeFlags bits from Ruby/Sapphire battle.h.
BATTLE_TYPE_DOUBLE = 1 << 0
BATTLE_TYPE_TRAINER = 1 << 3
BATTLE_TYPE_FIRST_BATTLE = 1 << 4
BATTLE_TYPE_SAFARI = 1 << 7

# battle_controllers.h / battle.h constants used for RAM-authoritative menu
# validation.  These are stable in Ruby/Sapphire English v1.0-v1.1.
CONTROLLER_PRINTSTRING = 16
CONTROLLER_PRINTSTRINGPLAYERONLY = 17
CONTROLLER_CHOOSEACTION = 18
CONTROLLER_TWORETURNVALUES = 33
B_ACTION_RUN = 3
BATTLE_BUFFER_SIZE = 0x200

from gen3bot.data.rs_world import map_name as world_map_name


def u16(data):
    return int.from_bytes(data, "little")


def u32(data):
    return int.from_bytes(data, "little")


def detect_profile(bridge):
    profile = detect_supported_game(bridge)
    profile = dict(profile)
    profile["profile_id"] = f"{profile['code']}-rev{profile['revision']:02X}"
    return profile


def read_u16(bridge, address):
    return u16(bridge.read(address, 2))


def read_u32(bridge, address):
    return u32(bridge.read(address, 4))


def map_name(map_group, map_num):
    return world_map_name(map_group, map_num)


def player_state(bridge):
    state = dict(frozen_player_state(bridge))
    state["map_name"] = map_name(state["map_group"], state["map_num"])
    return state


def battle_type_flags(bridge):
    # gBattleTypeFlags is u16 in Ruby/Sapphire.  Reading 4 bytes also reads the
    # alignment padding / following symbol and can fabricate battle-type bits.
    return read_u16(bridge, ADDR["gBattleTypeFlags"])


def battlers_count(bridge):
    return bridge.read(ADDR["gBattlersCount"], 1)[0]


MAIN_IN_BATTLE_OFFSET = 0x43D
MAIN_IN_BATTLE_MASK = 0x02


def battle_active(bridge):
    """Return Ruby/Sapphire's authoritative gMain.inBattle bit.

    gBattleTypeFlags is configuration for the most recent/current battle and is
    not an overworld/battle lifetime flag; it can remain non-zero after returning
    to the field.  gMain.inBattle is explicitly set TRUE entering battle and
    FALSE on battle exit, so it is the correct authority for wild-mode state.
    """
    value = bridge.read(ADDR["gMain"] + MAIN_IN_BATTLE_OFFSET, 1)[0]
    return bool(value & MAIN_IN_BATTLE_MASK)


def battle_controller_exec_flags(bridge):
    return read_u32(bridge, ADDR["gBattleControllerExecFlags"])


def action_cursor(bridge, battler=0):
    battler = int(battler)
    if battler < 0 or battler > 3:
        battler = 0
    cursor = bridge.read(ADDR["gActionSelectionCursor"] + battler, 1)[0] & 0x03
    return battler, cursor



def live_controller_commands(bridge):
    """Return controller commands for every battler whose exec bit is live.

    Battle text can be owned by the opponent-side controller (battler 1/3),
    while the action menu is normally owned by the player side (0/2).  Wild
    intro advancement must therefore inspect all four controller slots rather
    than only player-side menu candidates.
    """
    exec_flags = battle_controller_exec_flags(bridge)
    states = []
    for battler in range(4):
        bit = 1 << battler
        if not (exec_flags & bit):
            continue
        command = bridge.read(
            ADDR["gBattleBufferA"] + battler * BATTLE_BUFFER_SIZE, 1
        )[0]
        states.append({
            "battler": battler,
            "command": command,
            "exec_flags": exec_flags,
        })
    return states


def battle_text_controller_state(bridge):
    """Return a live PRINTSTRING controller state, if one exists."""
    for state in live_controller_commands(bridge):
        if state["command"] in (CONTROLLER_PRINTSTRING, CONTROLLER_PRINTSTRINGPLAYERONLY):
            return state
    return None

def action_menu_candidate_state(bridge):
    """Return the current player-controller candidate state, or ``None``.

    This is deliberately broader than :func:`choose_action_state`: it requires
    a live player controller-exec bit and returns the current action cursor even
    if ``gBattleBufferA[0]`` is not reporting CONTROLLER_CHOOSEACTION.

    Some retail/mGBA runs observed in support bundles have a visible,
    interactive Fight/Bag/Pokémon/Run menu while the expected command byte does
    not read back as 18.  The wild engine never treats this broader candidate as
    proof by itself; it must first prove interactivity by moving the action
    cursor with a harmless D-pad pulse.  A is never sent on candidate state
    alone.
    """
    exec_flags = battle_controller_exec_flags(bridge)
    fallback = None
    for battler in (0, 2):
        bit = 1 << battler
        if not (exec_flags & bit):
            continue
        command = bridge.read(
            ADDR["gBattleBufferA"] + battler * BATTLE_BUFFER_SIZE, 1
        )[0]
        _battler, cursor = action_cursor(bridge, battler)
        state = {
            "battler": battler,
            "cursor": cursor,
            "command": command,
            "exec_flags": exec_flags,
            "strict": command == CONTROLLER_CHOOSEACTION,
        }
        if state["strict"]:
            return state
        if fallback is None:
            fallback = state
    return fallback


def choose_action_state(bridge):
    """Return the strict live player command-menu state, or ``None``.

    The cursor byte by itself is not proof that the Fight/Bag/Pokémon/Run menu
    is active: it persists between controller commands.  The preferred path is
    still the player's controller exec bit plus CONTROLLER_CHOOSEACTION (18).
    When retail/mGBA does not expose that command byte reliably, the wild engine
    can fall back to :func:`action_menu_candidate_state` and prove the menu by
    observing a predicted RAM cursor change after a harmless D-pad pulse.
    """
    state = action_menu_candidate_state(bridge)
    if state is not None and state.get("strict"):
        return state
    return None


def action_reply(bridge, battler=0):
    """Read the player's most recent 4-byte controller->engine reply."""
    battler = int(battler)
    if battler < 0 or battler > 3:
        battler = 0
    raw = bridge.read(
        ADDR["gBattleBufferB"] + battler * BATTLE_BUFFER_SIZE, 4
    )
    return {
        "raw": raw,
        "command": raw[0],
        "action": raw[1],
        "value": int.from_bytes(raw[2:4], "little"),
    }


def run_action_reply_seen(bridge, battler=0):
    reply = action_reply(bridge, battler)
    return (
        reply["command"] == CONTROLLER_TWORETURNVALUES
        and reply["action"] == B_ACTION_RUN
    )
