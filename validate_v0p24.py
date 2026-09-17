#!/usr/bin/env python3
"""Offline validation for v0p24 RS World/Hunt integration."""
import os
import tempfile
from pathlib import Path

from gen3bot.data.rs_world import (
    RSWorldDatabase, ROM_SYMBOLS, effective_encounter_percent, wurmple_evolution,
)
from gen3bot.data.hunt_policy import HuntPolicy
from gen3bot.data.terrain import TerrainReader, TILE_BIT_ATTRIBUTES
from gen3bot.data.sprite_assets import safe_name, filename, asset_counts
from stats_store import StatsStore
from gen3bot.games.rs import ADDR


class MockBridge:
    def __init__(self):
        self.blocks = []

    def put(self, address, data):
        self.blocks.append((int(address), bytes(data)))

    def read(self, address, length):
        address, length = int(address), int(length)
        for base, data in reversed(self.blocks):
            if address >= base and address + length <= base + len(data):
                start = address - base
                return data[start:start + length]
        return b"\0" * length


def route103_test():
    profile = {"code": "AXVE", "revision": 0, "name": "Pokémon Ruby"}
    bridge = MockBridge()
    wild = ROM_SYMBOLS[("AXVE", 0)]["wild"]
    info = 0x08010000
    mons = 0x08011000
    header = bytearray(20)
    header[0:2] = bytes((0, 18))
    header[4:8] = info.to_bytes(4, "little")
    bridge.put(wild, header + bytes((0xFF, 0xFF)) + b"\0" * 18)
    info_raw = bytearray(8)
    info_raw[0] = 20
    info_raw[4:8] = mons.to_bytes(4, "little")
    bridge.put(info, info_raw)
    # 60% Zigzagoon, 30% Poochyena, 10% Wingull under standard land weights.
    species = [288, 288, 288, 288, 286, 286, 286, 286, 309, 309, 309, 309]
    levels = [(2, 2), (2, 2), (2, 3), (2, 3), (2, 2), (2, 3), (2, 3), (3, 3), (2, 2), (2, 3), (3, 3), (3, 3)]
    raw = bytearray()
    for sid, (lo, hi) in zip(species, levels):
        raw += bytes((lo, hi)) + int(sid).to_bytes(2, "little")
    bridge.put(mons, raw)
    db = RSWorldDatabase(bridge, profile)
    assert db.species_percent(0, 18, 288, "land") == 60
    assert db.species_percent(0, 18, 286, "land") == 30
    assert db.species_percent(0, 18, 309, "land") == 10
    assert db.method_base_rate(0, 18, "land") == 20


def encounter_rate_test():
    base = effective_encounter_percent(20)
    assert abs(base - 11.1111111) < 0.001
    assert abs(effective_encounter_percent(20, illuminate=True) - 22.2222222) < 0.001
    assert abs(effective_encounter_percent(20, white_flute=True, illuminate=True) - 33.3333333) < 0.001
    assert abs(effective_encounter_percent(20, white_flute=True, illuminate=True, bike=True) - 26.6666667) < 0.001


def wurmple_test():
    assert wurmple_evolution(0x00000000, 290) == "Silcoon → Beautifly"
    assert wurmple_evolution(0x00040000, 290) == "Silcoon → Beautifly"
    assert wurmple_evolution(0x00050000, 290) == "Cascoon → Dustox"
    assert wurmple_evolution(0x00090000, 290) == "Cascoon → Dustox"
    assert wurmple_evolution(0x12345678, 288) is None


def policy_test():
    old = os.environ.get("APPDATA")
    with tempfile.TemporaryDirectory() as td:
        os.environ["APPDATA"] = td
        policy = HuntPolicy()
        policy.set_state("AXVE", 0, 18, "land", 286, "Target")
        policy.set_state("AXVE", 0, 18, "land", 309, "Target")
        policy.set_state("AXVE", 0, 18, "land", 288, "Blocked")
        assert policy.targets("AXVE", 0, 18, "land") == {286, 309}
        assert policy.state("AXVE", 0, 18, "land", 288) == "Blocked"
    if old is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old


def terrain_test():
    bridge = MockBridge()
    layout = 0x02010000
    map_ptr = 0x02011000
    tileset = 0x02012000
    attrs = 0x08020000
    bridge.put(ADDR["gMapHeader"], layout.to_bytes(4, "little"))
    raw_layout = bytearray(24)
    raw_layout[0:4] = (2).to_bytes(4, "little")
    raw_layout[4:8] = (1).to_bytes(4, "little")
    raw_layout[12:16] = map_ptr.to_bytes(4, "little")
    raw_layout[16:20] = tileset.to_bytes(4, "little")
    raw_layout[20:24] = tileset.to_bytes(4, "little")
    bridge.put(layout, raw_layout)
    # Tile 0 behaviour 2 (tall grass), tile 1 behaviour 16 (pond water).
    bridge.put(map_ptr, (0).to_bytes(2, "little") + (1).to_bytes(2, "little"))
    ts = bytearray(0x18)
    ts[0x10:0x14] = attrs.to_bytes(4, "little")
    bridge.put(tileset, ts)
    bridge.put(attrs, (2).to_bytes(2, "little") + (16).to_bytes(2, "little"))
    bit_table = TILE_BIT_ATTRIBUTES[("AXVE", 0)]
    table = bytearray(256)
    table[2] = 0x01
    table[16] = 0x03
    bridge.put(bit_table, table)
    reader = TerrainReader(bridge, {"code": "AXVE", "revision": 0})
    assert reader.tile(0, 0)["land"] is True
    assert reader.tile(1, 0)["water"] is True
    assert reader.can_step(1, 0, "land") is False
    assert reader.can_step(1, 0, "water") is True


def sprite_test():
    assert safe_name("Nidoran♀") == "Nidoran_f"
    assert safe_name("Nidoran♂") == "Nidoran_m"
    assert safe_name("Farfetch’d") == "Farfetch_d"
    assert filename("Unown") == "Unown (P).png"
    counts = asset_counts()
    for variant in ("normal", "shiny", "anti-shiny"):
        assert counts[variant] >= 7


def stats_test():
    old = os.environ.get("APPDATA")
    with tempfile.TemporaryDirectory() as td:
        os.environ["APPDATA"] = td
        store = StatsStore()
        store.begin_session({"mode": "Wild Encounters"})
        store.record_encounter({
            "encounter_no": 1, "attempt": 1, "species": "Wurmple", "species_id": 290,
            "pid": 0x00050000, "nature": "Hardy", "gender": "female", "ability": "Shield Dust",
            "ability_slot": 1, "held_item": "None", "held_item_id": 0,
            "ivs": {"hp":1,"atk":2,"def":3,"spa":4,"spd":5,"spe":6},
            "hidden_power": "Ice", "hidden_power_power": 40, "sv": 3, "shiny": True,
            "duplicate": False, "repeated_generation": False, "encounter_percent": 10,
            "wurmple_evolution": "Cascoon → Dustox", "hunt_state": "Target",
            "method": "Spin", "encounter_method": "land", "game": "Pokémon Ruby",
            "game_code": "AXVE", "revision": 0, "map_group": 0, "map_num": 18,
            "map_name": "Route 103",
        })
        rec = store.recent_shinies()[-1]
        assert rec["species_id"] == 290
        assert rec["encounter_percent"] == 10
        assert rec["wurmple_evolution"] == "Cascoon → Dustox"
        assert rec["map_name"] == "Route 103"
    if old is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old


def main():
    route103_test()
    encounter_rate_test()
    wurmple_test()
    policy_test()
    terrain_test()
    sprite_test()
    stats_test()
    print("v0p24 RS World/Hunt offline validation: PASS")


if __name__ == "__main__":
    main()
