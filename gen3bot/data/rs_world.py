#!/usr/bin/env python3
"""Pokémon Ruby/Sapphire runtime world database.

The database is read from the retail game's own ``gWildMonHeaders`` table over
PB3/mGBA.  This keeps Ruby/Sapphire version differences authoritative and lets
one data source drive:

* current-map encounter species and slot percentages;
* the Hunt tab target/allowed/blocked lists;
* per-Pokémon Encounter % in logs/current encounter/recent shinies;
* the all-map world cache written to AppData;
* movement-containment metadata used by future Walk/Run/Bike modes.

Addresses below are the English US Ruby/Sapphire v1.0/v1.1 symbols from
pret/pokeruby's symbols branch.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

LAND_WEIGHTS = (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1)
WATER_WEIGHTS = (60, 30, 5, 4, 1)
ROCK_WEIGHTS = WATER_WEIGHTS
# Fishing has ten physical slots.  Each rod samples only its own subset and
# those subsets each sum to 100%.
FISH_WEIGHTS = (70, 30, 60, 20, 20, 40, 40, 15, 4, 1)
FISH_GROUPS = {
    "old_rod": (0, 1),
    "good_rod": (2, 3, 4),
    "super_rod": (5, 6, 7, 8, 9),
}
METHOD_LABELS = {
    "land": "Land / Grass",
    "water": "Surf",
    "rock_smash": "Rock Smash",
    "old_rod": "Old Rod",
    "good_rod": "Good Rod",
    "super_rod": "Super Rod",
}

ROM_SYMBOLS = {
    ("AXVE", 0): {
        "wild": 0x0839D454, "species": 0x081F716C, "base": 0x081FEC18,
        "abilities": 0x081FA248, "items": 0x083C5564,
    },
    ("AXVE", 1): {
        "wild": 0x0839D46C, "species": 0x081F7184, "base": 0x081FEC30,
        "abilities": 0x081FA260, "items": 0x083C5580,
    },
    ("AXPE", 0): {
        "wild": 0x0839D29C, "species": 0x081F70FC, "base": 0x081FEBA8,
        "abilities": 0x081FA1D8, "items": 0x083C55BC,
    },
    ("AXPE", 1): {
        "wild": 0x0839D2B4, "species": 0x081F7114, "base": 0x081FEBC0,
        "abilities": 0x081FA1F0, "items": 0x083C55DC,
    },
}

# Exact R/S map-group 0 names.  Wild headers do not exist for every entry, but
# keeping all of group 0 means the UI can identify every numbered route even
# before an encounter table is consulted.
_GROUP0 = [
    "Petalburg City", "Slateport City", "Mauville City", "Rustboro City",
    "Fortree City", "Lilycove City", "Mossdeep City", "Sootopolis City",
    "Ever Grande City", "Littleroot Town", "Oldale Town", "Dewford Town",
    "Lavaridge Town", "Fallarbor Town", "Verdanturf Town", "Pacifidlog Town",
]
_GROUP0 += [f"Route {n}" for n in range(101, 135)]
_GROUP0 += ["Underwater Route 124", "Underwater Route 126"]

# Exact R/S dungeon map-group 24 order from pret/pokeruby map_groups.json.
_GROUP24 = [
    "Meteor Falls 1F (Room 1)", "Meteor Falls 1F (Room 2)",
    "Meteor Falls B1F (Room 1)", "Meteor Falls B1F (Room 2)",
    "Rusturf Tunnel", "Underwater Sootopolis City", "Desert Ruins",
    "Granite Cave 1F", "Granite Cave B1F", "Granite Cave B2F",
    "Granite Cave — Steven's Room", "Petalburg Woods", "Mt. Chimney",
    "Jagged Pass", "Fiery Path", "Mt. Pyre 1F", "Mt. Pyre 2F",
    "Mt. Pyre 3F", "Mt. Pyre 4F", "Mt. Pyre 5F", "Mt. Pyre 6F",
    "Mt. Pyre Exterior", "Mt. Pyre Summit", "Aqua Hideout 1F",
    "Aqua Hideout B1F", "Aqua Hideout B2F", "Underwater Seafloor Cavern",
    "Seafloor Cavern Entrance", "Seafloor Cavern Room 1",
    "Seafloor Cavern Room 2", "Seafloor Cavern Room 3",
    "Seafloor Cavern Room 4", "Seafloor Cavern Room 5",
    "Seafloor Cavern Room 6", "Seafloor Cavern Room 7",
    "Seafloor Cavern Room 8", "Seafloor Cavern Room 9",
    "Cave of Origin Entrance", "Cave of Origin 1F", "Cave of Origin B1F",
    "Cave of Origin B2F", "Cave of Origin B3F", "Cave of Origin B4F",
    "Victory Road 1F", "Victory Road B1F", "Victory Road B2F",
    "Shoal Cave — Low Tide Entrance", "Shoal Cave — Low Tide Inner",
    "Shoal Cave — Low Tide Stairs", "Shoal Cave — Low Tide Lower",
    "Shoal Cave — High Tide Entrance", "Shoal Cave — High Tide Inner",
    "New Mauville Entrance", "New Mauville Inside", "Abandoned Ship Deck",
    "Abandoned Ship Corridors 1F", "Abandoned Ship Rooms 1F",
    "Abandoned Ship Corridors B1F", "Abandoned Ship Rooms B1F",
    "Abandoned Ship Rooms 2 B1F", "Abandoned Ship Underwater 1",
    "Abandoned Ship Room B1F", "Abandoned Ship Rooms 2 1F",
    "Abandoned Ship Captain's Office", "Abandoned Ship Underwater 2",
    "Abandoned Ship Hidden Floor Corridors", "Abandoned Ship Hidden Floor Rooms",
    "Island Cave", "Ancient Tomb", "Underwater Route 134",
    "Underwater Sealed Chamber", "Sealed Chamber Outer Room",
    "Sealed Chamber Inner Room", "Scorched Slab", "Magma Hideout 1F",
    "Magma Hideout B1F", "Magma Hideout B2F", "Sky Pillar Entrance",
    "Sky Pillar Outside", "Sky Pillar 1F", "Sky Pillar 2F", "Sky Pillar 3F",
    "Sky Pillar 4F", "Shoal Cave — Low Tide Ice Room", "Sky Pillar 5F",
    "Sky Pillar Top",
]
_GROUP26 = [
    "Safari Zone Northwest", "Safari Zone Northeast", "Safari Zone Southwest",
    "Safari Zone Southeast", "Battle Tower Outside", "Battle Tower Lobby",
    "Battle Tower Elevator", "Battle Tower Corridor", "Battle Tower Battle Room",
    "Southern Island Exterior", "Southern Island Interior", "Safari Zone Rest House",
]

MAP_NAMES = {(0, i): name for i, name in enumerate(_GROUP0)}
MAP_NAMES.update({(24, i): name for i, name in enumerate(_GROUP24)})
MAP_NAMES.update({(26, i): name for i, name in enumerate(_GROUP26)})

# R/S international character table.  Only ordinary names are needed here,
# but using the game codec avoids hard-coding every species/ability/item.
_INT_TABLE = list(
    " ÀÁÂÇÈÉÊËÌ ÎÏÒÓÔ" + "ŒÙÚÛÑßàá çèéêëì " + "îïòóôœùúûñºªᵉ&+ " +
    "    L=;         " + "                " + "▯¿¡       Í%()  " +
    "        â      í" + "         ⬆⬇⬅➡***" + "****ᵉ<>         " +
    "                " + " 0123456789!?.-・" + "…“”‘’♂♀$,×/ABCDE" +
    "FGHIJKLMNOPQRSTU" + "VWXYZabcdefghijk" + "lmnopqrstuvwxyz▶" +
    ":ÄÖÜäöü         "
)
_INT_TABLE[0x34] = "Lv"
_INT_TABLE[0x53] = "Pk"
_INT_TABLE[0x54] = "Mn"
_INT_TABLE[0x55] = "Po"
_INT_TABLE[0x56] = "Ké"
_INT_TABLE[0x57] = "BL"
_INT_TABLE[0x58] = "OC"
_INT_TABLE[0x59] = "K"
_INT_TABLE[0xA0] = "re"


def decode_text(data: bytes) -> str:
    out = []
    for value in data:
        if value == 0xFF:
            break
        if value == 0xFE:
            out.append(" ")
            continue
        if 0 <= value < len(_INT_TABLE):
            out.append(_INT_TABLE[value])
    return "".join(out).strip()


def display_species_name(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return "Unknown"
    specials = {
        "NIDORAN♀": "Nidoran♀", "NIDORAN♂": "Nidoran♂",
        "MR. MIME": "Mr. Mime", "FARFETCH’D": "Farfetch’d",
        "FARFETCH'D": "Farfetch’d", "HO-OH": "Ho-Oh",
    }
    if value in specials:
        return specials[value]
    return value.lower().title().replace("'S", "'s")


def map_name(group: int, num: int) -> str:
    key = (int(group), int(num))
    return MAP_NAMES.get(key, f"Map {key[0]}/{key[1]}")


def _valid_rom_ptr(pointer: int) -> bool:
    return 0x08000000 <= int(pointer) < 0x0A000000


def _u16(data: bytes) -> int:
    return int.from_bytes(data, "little")


def _u32(data: bytes) -> int:
    return int.from_bytes(data, "little")


@dataclass
class EncounterSpecies:
    species_id: int
    min_level: int
    max_level: int
    percent: int


class RSWorldDatabase:
    """Live version-correct Ruby/Sapphire encounter database."""

    def __init__(self, bridge, profile):
        self.bridge = bridge
        self.profile = dict(profile)
        key = (self.profile.get("code"), int(self.profile.get("revision", 0)))
        if key not in ROM_SYMBOLS:
            raise RuntimeError(f"No R/S world symbols for {key!r}")
        self.symbols = ROM_SYMBOLS[key]
        self._headers = None
        self._species_cache = {}
        self._ability_cache = {}
        self._item_cache = {0: "None"}
        self._map_cache = {}

    def species_meta(self, species_id: int) -> dict:
        species_id = int(species_id)
        if species_id in self._species_cache:
            return dict(self._species_cache[species_id])
        raw_name = self.bridge.read(self.symbols["species"] + species_id * 11, 11)
        name = display_species_name(decode_text(raw_name))
        base = self.bridge.read(self.symbols["base"] + species_id * 28, 28)
        gender = base[0x10]
        ability_ids = [base[0x16]]
        if base[0x17] and base[0x17] != base[0x16]:
            ability_ids.append(base[0x17])
        abilities = [self.ability_name(value) for value in ability_ids]
        meta = {
            "species_id": species_id,
            "species": name,
            "gender_threshold": gender,
            "abilities": abilities,
            "ability1": abilities[0] if abilities else "Unknown",
            "ability2": abilities[1] if len(abilities) > 1 else None,
        }
        self._species_cache[species_id] = meta
        return dict(meta)

    def ability_name(self, ability_id: int) -> str:
        ability_id = int(ability_id)
        if ability_id in self._ability_cache:
            return self._ability_cache[ability_id]
        name = decode_text(
            self.bridge.read(self.symbols["abilities"] + ability_id * 13, 13)
        ) or f"Ability {ability_id}"
        name = name.title()
        self._ability_cache[ability_id] = name
        return name

    def item_name(self, item_id: int) -> str:
        item_id = int(item_id)
        if item_id in self._item_cache:
            return self._item_cache[item_id]
        try:
            # struct Item is 44 bytes in R/S and starts with name[14].
            name = decode_text(
                self.bridge.read(self.symbols["items"] + item_id * 44, 14)
            ).title()
        except Exception:
            name = ""
        if not name:
            name = f"Item {item_id}"
        self._item_cache[item_id] = name
        return name

    def _load_headers(self):
        if self._headers is not None:
            return self._headers
        headers = []
        address = self.symbols["wild"]
        # gWildMonHeaders is 0x7A8 bytes: 97 headers plus FF/FF terminator.
        for index in range(100):
            raw = self.bridge.read(address + index * 20, 20)
            group, num = raw[0], raw[1]
            if group == 0xFF and num == 0xFF:
                break
            headers.append({
                "map_group": group,
                "map_num": num,
                "land": _u32(raw[4:8]),
                "water": _u32(raw[8:12]),
                "rock_smash": _u32(raw[12:16]),
                "fishing": _u32(raw[16:20]),
            })
        self._headers = headers
        return headers

    def _read_info(self, pointer: int, method: str):
        if not _valid_rom_ptr(pointer):
            return None
        raw = self.bridge.read(pointer, 8)
        rate = raw[0]
        mons_ptr = _u32(raw[4:8])
        if not _valid_rom_ptr(mons_ptr):
            return None
        if method == "land":
            count, weights = 12, LAND_WEIGHTS
        elif method == "water":
            count, weights = 5, WATER_WEIGHTS
        elif method == "rock_smash":
            count, weights = 5, ROCK_WEIGHTS
        elif method == "fishing":
            count, weights = 10, FISH_WEIGHTS
        else:
            return None
        mons_raw = self.bridge.read(mons_ptr, count * 4)
        slots = []
        for index in range(count):
            block = mons_raw[index * 4:(index + 1) * 4]
            lo, hi = block[0], block[1]
            if lo > hi:
                lo, hi = hi, lo
            slots.append((lo, hi, _u16(block[2:4]), weights[index]))
        if method == "fishing":
            groups = {
                name: self._aggregate([slots[i] for i in indices])
                for name, indices in FISH_GROUPS.items()
            }
            return {"encounter_rate": rate, "groups": groups, "slots": slots}
        return {
            "encounter_rate": rate,
            "species": self._aggregate(slots),
            "slots": slots,
        }

    @staticmethod
    def _aggregate(slots):
        agg = {}
        order = []
        for min_level, max_level, species_id, percent in slots:
            if species_id not in agg:
                agg[species_id] = [min_level, max_level, 0]
                order.append(species_id)
            row = agg[species_id]
            row[0] = min(row[0], min_level)
            row[1] = max(row[1], max_level)
            row[2] += percent
        return [
            EncounterSpecies(species_id, *agg[species_id]).__dict__
            for species_id in order
        ]

    def map_data(self, group: int, num: int) -> dict:
        key = (int(group), int(num))
        if key in self._map_cache:
            return self._map_cache[key]
        header = next(
            (
                value for value in self._load_headers()
                if (value["map_group"], value["map_num"]) == key
            ),
            None,
        )
        result = {
            "map_group": key[0],
            "map_num": key[1],
            "map_name": map_name(*key),
            "methods": {},
        }
        if header:
            for method in ("land", "water", "rock_smash", "fishing"):
                info = self._read_info(header[method], method)
                if info:
                    result["methods"][method] = info
        self._map_cache[key] = result
        return result

    def _named_map_data(self, group: int, num: int) -> dict:
        """Return a JSON-friendly copy with species names/abilities embedded."""
        source = self.map_data(group, num)
        result = {
            "map_group": source["map_group"],
            "map_num": source["map_num"],
            "map_name": source["map_name"],
            "methods": {},
        }
        for method, info in source.get("methods", {}).items():
            dst = {"encounter_rate": info.get("encounter_rate")}
            if method == "fishing":
                dst["groups"] = {}
                for rod, rows in (info.get("groups") or {}).items():
                    dst["groups"][rod] = [
                        {**dict(row), **self.species_meta(row["species_id"])}
                        for row in rows
                    ]
            else:
                dst["species"] = [
                    {**dict(row), **self.species_meta(row["species_id"])}
                    for row in (info.get("species") or [])
                ]
            result["methods"][method] = dst
        return result

    def scan_all(self, stop_requested=None) -> dict:
        """Decode every R/S wild header into one complete world payload."""
        maps = []
        for header in self._load_headers():
            if stop_requested is not None and stop_requested():
                break
            maps.append(self._named_map_data(header["map_group"], header["map_num"]))
        return {
            "schema_version": 2,
            "game": self.profile.get("name"),
            "game_code": self.profile.get("code"),
            "revision": self.profile.get("revision"),
            "map_count": len(maps),
            "maps": maps,
        }

    def save_cache(self, payload=None) -> Path:
        if payload is None:
            payload = self.scan_all()
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        out = (
            root / "Pokebot3DS-CFW" / "Gen3" /
            f"rs_world_{self.profile.get('code', 'RS')}_rev{int(self.profile.get('revision', 0)):02X}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, out)
        return out

    def method_species(self, group, num, method="land"):
        data = self.map_data(group, num)
        methods = data.get("methods", {})
        if method in FISH_GROUPS:
            fishing = methods.get("fishing") or {}
            return fishing.get("groups", {}).get(method, [])
        return (methods.get(method) or {}).get("species", [])

    def species_percent(self, group, num, species_id, method="land"):
        for row in self.method_species(group, num, method):
            if int(row["species_id"]) == int(species_id):
                return int(row["percent"])
        return None

    def method_base_rate(self, group, num, method="land"):
        # Fishing probability is governed by the fishing interaction rather than
        # StandardWildEncounter's step/turn dice roll, so do not present a
        # misleading per-check percentage for rods.
        if method in FISH_GROUPS:
            return None
        return (
            self.map_data(group, num).get("methods", {}).get(method) or {}
        ).get("encounter_rate")


def effective_encounter_percent(
    base_rate,
    *,
    white_flute=False,
    illuminate=False,
    bike=False,
    stench=False,
    black_flute=False,
    cleanse_tag=False,
):
    """R/S StandardWildEncounter per-check probability after live modifiers.

    DoWildEncounterTest multiplies the map's rate by 16, applies bike/flute/
    Cleanse Tag/lead-ability modifiers, caps at 2880, then rolls against 2880.
    The separate 60% terrain-change gate is not folded into this display because
    stationary Spin does not change metatile behaviour and future movement modes
    can evaluate that gate from their actual movement path.
    """
    if base_rate is None:
        return None
    rate = int(base_rate) * 16
    if bike:
        rate = rate * 80 // 100
    if white_flute:
        rate += rate // 2
    elif black_flute:
        rate //= 2
    if cleanse_tag:
        rate = rate * 2 // 3
    if stench:
        rate //= 2
    if illuminate:
        rate *= 2
    rate = min(rate, 2880)
    return rate / 2880.0 * 100.0


def wurmple_evolution(pid: int, species_id: int | None = None) -> str | None:
    """Return Wurmple's deterministic R/S evolution branch from its PID."""
    if species_id is not None and int(species_id) != 290:
        return None
    branch = (int(pid) >> 16) % 10
    return "Silcoon → Beautifly" if branch <= 4 else "Cascoon → Dustox"
