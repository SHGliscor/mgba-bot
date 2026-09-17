#!/usr/bin/env python3
"""Ruby/Sapphire encounter-terrain reader for movement containment.

The running map layout and metatile attributes are read directly from RAM/ROM.
R/S's ``sTileBitAttributes`` table is the authority for whether a metatile is
wild-encounter terrain, so Walk/Run/Bike containment does not rely on a small
hand-maintained list of grass behaviour IDs.
"""
from gen3bot.games.rs import ADDR, detect_profile

PRIMARY_METATILES = 512

# pret/pokeruby symbols, English US Ruby/Sapphire v1.0/v1.1.
TILE_BIT_ATTRIBUTES = {
    ("AXVE", 0): 0x08308E2C,
    ("AXVE", 1): 0x08308E44,
    ("AXPE", 0): 0x08308DBC,
    ("AXPE", 1): 0x08308DD4,
}


def u16(data):
    return int.from_bytes(data, "little")


def u32(data):
    return int.from_bytes(data, "little")


def _ptr_ok(pointer):
    return 0x02000000 <= int(pointer) < 0x0A000000


class TerrainReader:
    def __init__(self, bridge, profile=None):
        self.bridge = bridge
        self.profile = dict(profile or detect_profile(bridge))
        key = (self.profile.get("code"), int(self.profile.get("revision", 0)))
        try:
            self.bit_attributes = TILE_BIT_ATTRIBUTES[key]
        except KeyError as exc:
            raise RuntimeError(f"No R/S terrain symbols for {key!r}") from exc

    def layout(self):
        layout_ptr = u32(self.bridge.read(ADDR["gMapHeader"], 4))
        if not _ptr_ok(layout_ptr):
            raise RuntimeError(f"Invalid MapLayout pointer 0x{layout_ptr:08X}")
        raw = self.bridge.read(layout_ptr, 24)
        width, height = u32(raw[0:4]), u32(raw[4:8])
        if width <= 0 or height <= 0 or width > 512 or height > 512:
            raise RuntimeError(f"Implausible map layout {width}x{height}")
        return {
            "ptr": layout_ptr,
            "width": width,
            "height": height,
            "map_ptr": u32(raw[12:16]),
            "primary": u32(raw[16:20]),
            "secondary": u32(raw[20:24]),
        }

    def tile(self, x, y):
        layout = self.layout()
        x, y = int(x), int(y)
        if x < 0 or y < 0 or x >= layout["width"] or y >= layout["height"]:
            return {
                "x": x,
                "y": y,
                "inside": False,
                "encounter": False,
                "land": False,
                "water": False,
            }

        value = u16(
            self.bridge.read(
                layout["map_ptr"] + 2 * (y * layout["width"] + x), 2
            )
        )
        metatile = value & 0x03FF
        tileset = (
            layout["primary"] if metatile < PRIMARY_METATILES else layout["secondary"]
        )
        metatile_index = (
            metatile if metatile < PRIMARY_METATILES else metatile - PRIMARY_METATILES
        )
        if not _ptr_ok(tileset):
            raise RuntimeError(f"Invalid Tileset pointer 0x{tileset:08X}")

        # Tileset +0x10 is metatileAttributes.  The low byte is behaviour.
        attributes_ptr = u32(self.bridge.read(tileset + 0x10, 4))
        if not _ptr_ok(attributes_ptr):
            raise RuntimeError(
                f"Invalid metatile-attributes pointer 0x{attributes_ptr:08X}"
            )
        attributes = u16(
            self.bridge.read(attributes_ptr + metatile_index * 2, 2)
        )
        behaviour = attributes & 0xFF

        # sTileBitAttributes[behaviour]: bit0=wildEncounter, bit1=surfable.
        bit_attributes = self.bridge.read(self.bit_attributes + behaviour, 1)[0]
        encounter = bool(bit_attributes & 0x01)
        surfable = bool(bit_attributes & 0x02)
        water = encounter and surfable
        land = encounter and not surfable
        return {
            "x": x,
            "y": y,
            "inside": True,
            "metatile_id": metatile,
            "behavior": behaviour,
            "tile_bits": bit_attributes,
            "surfable": surfable,
            "land": land,
            "water": water,
            "encounter": encounter,
        }

    def can_step(self, x, y, method="land"):
        tile = self.tile(x, y)
        if method == "land":
            return bool(tile.get("land"))
        if method == "water":
            return bool(tile.get("water"))
        return bool(tile.get("encounter"))

    def edge_mask(self, x, y, method="land"):
        directions = {
            "UP": (0, -1),
            "RIGHT": (1, 0),
            "DOWN": (0, 1),
            "LEFT": (-1, 0),
        }
        return {
            direction: self.can_step(int(x) + dx, int(y) + dy, method)
            for direction, (dx, dy) in directions.items()
        }
