#!/usr/bin/env python3
"""Registry for modular Gen 3 wild encounter methods.

The Dashboard reads this registry when Mode = Wild Encounters. A method can be
visible before its worker lands; only entries with implemented=True can start.
"""
MODES = {
    "Spin": {
        "implemented": True,
        "module": "gen3bot.modes.wild_spin",
        "stationary": True,
    },
    "Run": {"implemented": False, "stationary": False},
    "Walk": {"implemented": False, "stationary": False},
    "Acro Bike Bunny Hop": {"implemented": False, "stationary": True},
    "Sweet Scent": {"implemented": False, "stationary": True},
    "Fishing": {"implemented": False, "stationary": True},
    "Rock Smash": {"implemented": False, "stationary": False},
    "Safari": {"implemented": False, "stationary": False},
    "Feebas": {"implemented": False, "stationary": False},
}
