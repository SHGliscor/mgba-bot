# Pokebot mGBA 3DS CIA build

This experimental branch builds a separate **Pokebot mGBA** CIA, intended for Ruby/Sapphire on New 3DS. The production bot in the repository default branch is unchanged.

Source is derived from `mgba-emu/mgba` using the bridge overlay and `apply_to_mgba.py` supplied in the Pokebot-mGBA Bridge v1 archive. The separate CIA title ID is intended to avoid replacing stock mGBA. **Back up ROM saves before any on-hardware testing**: a distinct CIA identity does not guarantee distinct save file paths.

Test requirements: CIA compiles; loads to HOME menu and boots Ruby/Sapphire; host sends PING, INFO, 32-byte RAM read; then separately verifies safe bounded input and physical X+Y panic stop. No real hardware validation yet.
