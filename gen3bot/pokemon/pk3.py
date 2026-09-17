#!/usr/bin/env python3
"""Gen 3 PK3 decoding used by modular wild modes.

The decoder intentionally remains independent from the frozen starter module.
Only the species metadata required by the first Route 101 milestone is named;
unknown species still decode safely as ``Species <internal id>`` and retain all
PID/IV/SV data.
"""
import struct

SUBSTRUCT_ORDERS = [
    (0,1,2,3),(0,1,3,2),(0,2,1,3),(0,3,1,2),(0,2,3,1),(0,3,2,1),
    (1,0,2,3),(1,0,3,2),(2,0,1,3),(3,0,1,2),(2,0,3,1),(3,0,2,1),
    (1,2,0,3),(1,3,0,2),(2,1,0,3),(3,1,0,2),(2,3,0,1),(3,2,0,1),
    (1,2,3,0),(1,3,2,0),(2,1,3,0),(3,1,2,0),(2,3,1,0),(3,2,1,0),
]
NATURES = [
    "Hardy","Lonely","Brave","Adamant","Naughty",
    "Bold","Docile","Relaxed","Impish","Lax",
    "Timid","Hasty","Serious","Jolly","Naive",
    "Modest","Mild","Quiet","Bashful","Rash",
    "Calm","Gentle","Sassy","Careful","Quirky",
]
HP_TYPES = [
    "Fighting","Flying","Poison","Ground","Rock","Bug","Ghost","Steel",
    "Fire","Water","Grass","Electric","Psychic","Ice","Dragon","Dark",
]

# Internal Gen 3 species IDs, primary ability, gender threshold.
SPECIES = {
    277: ("Treecko", "Overgrow", 31),
    280: ("Torchic", "Blaze", 31),
    283: ("Mudkip", "Torrent", 31),
    286: ("Poochyena", "Run Away", 127),
    288: ("Zigzagoon", "Pickup", 127),
    290: ("Wurmple", "Shield Dust", 127),
    291: ("Silcoon", "Shed Skin", 127),
    292: ("Beautifly", "Swarm", 127),
    293: ("Cascoon", "Shed Skin", 127),
    294: ("Dustox", "Shield Dust", 127),
    304: ("Taillow", "Guts", 127),
    309: ("Wingull", "Keen Eye", 127),
    310: ("Pelipper", "Keen Eye", 127),
}


def u16(data):
    return int.from_bytes(data, "little")


def u32(data):
    return int.from_bytes(data, "little")


def generation_fingerprint(mon):
    iv = mon["ivs"]
    return (
        int(mon["species_id"]), int(mon["pid"]),
        int(iv["hp"]), int(iv["atk"]), int(iv["def"]),
        int(iv["spa"]), int(iv["spd"]), int(iv["spe"]),
    )


def decode_mon(raw):
    if len(raw) < 100 or raw[:80] == b"\0" * 80:
        return None

    pid = u32(raw[0:4])
    ot32 = u32(raw[4:8])
    key = pid ^ ot32
    order = SUBSTRUCT_ORDERS[pid % 24]

    logical = [None] * 4
    for logical_index in range(4):
        physical_index = order[logical_index]
        enc = raw[32 + physical_index * 12 : 32 + (physical_index + 1) * 12]
        words = [u32(enc[i:i+4]) ^ key for i in range(0, 12, 4)]
        logical[logical_index] = b"".join(w.to_bytes(4, "little") for w in words)

    dec48 = b"".join(logical)
    stored = u16(raw[28:30])
    calc = sum(struct.unpack("<24H", dec48)) & 0xFFFF
    if stored != calc:
        return None

    species_id = u16(dec48[0:2])
    if species_id <= 0:
        return None

    species_name, ability, gender_threshold = SPECIES.get(
        species_id, (f"Species {species_id}", "Unknown", 255)
    )

    ivword = u32(dec48[40:44])
    ivs = {
        "hp": (ivword >> 0) & 31,
        "atk": (ivword >> 5) & 31,
        "def": (ivword >> 10) & 31,
        "spe": (ivword >> 15) & 31,
        "spa": (ivword >> 20) & 31,
        "spd": (ivword >> 25) & 31,
    }
    ability_slot = 2 if (ivword >> 31) & 1 else 1

    tid, sid = u16(raw[4:6]), u16(raw[6:8])
    sv = tid ^ sid ^ (pid & 0xFFFF) ^ (pid >> 16)
    shiny = sv < 8

    low = pid & 0xFF
    if gender_threshold == 255:
        gender = "-"
    elif gender_threshold == 254:
        gender = "female"
    elif gender_threshold == 0:
        gender = "male"
    else:
        gender = "female" if low < gender_threshold else "male"

    hp_type_bits = (
        ((ivs["hp"] & 1) << 0)
        | ((ivs["atk"] & 1) << 1)
        | ((ivs["def"] & 1) << 2)
        | ((ivs["spe"] & 1) << 3)
        | ((ivs["spa"] & 1) << 4)
        | ((ivs["spd"] & 1) << 5)
    )
    hp_type = HP_TYPES[(hp_type_bits * 15) // 63]

    hp_power_bits = (
        ((ivs["hp"] & 2) >> 1)
        | ((ivs["atk"] & 2) << 0)
        | ((ivs["def"] & 2) << 1)
        | ((ivs["spe"] & 2) << 2)
        | ((ivs["spa"] & 2) << 3)
        | ((ivs["spd"] & 2) << 4)
    )
    hp_power = (hp_power_bits * 40) // 63 + 30

    held_item_id = u16(dec48[2:4])
    evs = {
        "hp": dec48[24], "atk": dec48[25], "def": dec48[26],
        "spe": dec48[27], "spa": dec48[28], "spd": dec48[29],
    }
    pokerus = dec48[36]
    status_raw = u32(raw[80:84])
    status = "Healthy"
    if status_raw & 0x80: status = "Badly poisoned"
    elif status_raw & 0x40: status = "Paralysed"
    elif status_raw & 0x20: status = "Frozen"
    elif status_raw & 0x10: status = "Burned"
    elif status_raw & 0x08: status = "Poisoned"
    elif status_raw & 0x07: status = "Asleep"

    return {
        "species_id": species_id,
        "species": species_name,
        "pid": pid,
        "nature": NATURES[pid % 25],
        "tid": tid,
        "sid": sid,
        "sv": sv,
        "shiny": shiny,
        "gender": gender,
        "ivs": ivs,
        "ability": ability,
        "ability_slot": ability_slot,
        "hidden_power": hp_type,
        "hidden_power_power": hp_power,
        "level": raw[84],
        "hp": u16(raw[86:88]),
        "max_hp": u16(raw[88:90]),
        "stats": {
            "atk": u16(raw[90:92]), "def": u16(raw[92:94]),
            "spe": u16(raw[94:96]), "spa": u16(raw[96:98]), "spd": u16(raw[98:100]),
        },
        "held_item_id": held_item_id,
        "evs": evs,
        "pokerus_raw": pokerus,
        "pokerus": "Infected" if (pokerus & 0x0F) else ("Cured" if (pokerus & 0xF0) else "None"),
        "status": status,
        "checksum": stored,
    }


def apply_species_metadata(mon, meta, item_name=None):
    """Overlay authoritative ROM species/base-stat metadata on a decoded PK3."""
    if not mon:
        return mon
    out = dict(mon)
    if meta:
        out["species"] = meta.get("species", out.get("species"))
        threshold = int(meta.get("gender_threshold", 255))
        low = int(out.get("pid", 0)) & 0xFF
        if threshold == 255:
            out["gender"] = "-"
        elif threshold == 254:
            out["gender"] = "female"
        elif threshold == 0:
            out["gender"] = "male"
        else:
            out["gender"] = "female" if low < threshold else "male"
        abilities = list(meta.get("abilities") or [])
        slot = max(1, int(out.get("ability_slot", 1)))
        if abilities:
            out["ability"] = abilities[min(slot - 1, len(abilities) - 1)]
    if item_name is not None:
        out["held_item"] = item_name
    return out
