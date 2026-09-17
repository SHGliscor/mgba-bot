# Pokebot3DS-CFW Gen 3 modular architecture

## Frozen starter layer
The following remain frozen at the hardware-passed v0p21 HF1 state:

- `ruby_starter_hunter.py`
- `ruby_starter_ui.py`
- `rng_history.py`
- `rtc_tools.py`
- `rtc_probe.py`
- `build/Pokebot3DS-CFW-mGBA-StarterRNGGate-v0p21.cia`
- `build/Pokebot3DS-CFW-mGBA-StarterRNGGate-v0p21.3dsx`

Run `python verify_frozen_starters.py` to verify the SHA-256 freeze manifest.

Verified before freeze:

| Game | Revision | Status |
| --- | --- | --- |
| Ruby | v1.0 / rev00 | PASS |
| Ruby | v1.1 / rev01 | PASS |
| Sapphire | v1.0 / rev00 | PASS |
| Sapphire | v1.1 / rev01 | PASS |

The v0p19 generation-aware starter RNG path remains the frozen RNG base. Ruby v1.1 exceeded 900 resets without a duplicate PID before the freeze.

## Main Dashboard mode router
`gen3_app.py` subclasses the frozen MainWindow without changing it. The existing Pokémon-themed Dashboard is the single hunt surface.

```text
Dashboard
└── Mode
    ├── Starters
    │   └── Starter: Torchic / Treecko / Mudkip / Random
    └── Wild Encounters
        └── Method: Spin / Run / Walk / Bunny Hop / Sweet Scent /
                    Fishing / Rock Smash / Safari / Feebas
```

The second selector is dynamic. Wild encounters update the same Current Encounter card, Shiny Phase panel, Encounter Log, Phase Records, Lifetime stats, and Recent Shinies used by starter hunts.

## Modular wild layer

```text
gen3_app.py                 # Dashboard mode router only

gen3bot/
├── core/
│   └── bridge.py           # shared reply-correlated bridge
├── games/
│   └── rs.py               # Ruby/Sapphire v1.0-v1.1 RAM profile
├── pokemon/
│   └── pk3.py              # generic wild PK3 decoder
└── modes/
    ├── registry.py         # visible wild methods + implementation state
    ├── wild_engine.py      # common battle/PK3/run-away pipeline
    └── wild_spin.py        # first implemented trigger method
```

`gen3bot/ui/wild_tab.py` is retained only as an older development reference and is no longer installed into the application. New wild methods should add workers under `gen3bot/modes/` and register themselves in `registry.py`; they should not create another full dashboard.

## v0p23 HF2 battle lifetime authority

`gBattleTypeFlags` describes the current/most recent battle configuration and may remain non-zero after returning to the field. It must not be used as an `in battle` boolean.

Ruby/Sapphire's authoritative lifetime bit is `gMain.inBattle`, stored in the packed byte at `gMain + 0x43D`, mask `0x02`. Wild-mode start guards, encounter detection, RUN completion, overworld recovery and stationary-turn transition checks all use this bit. `gBattleTypeFlags` is consulted only after `gMain.inBattle` confirms a live battle, for metadata such as trainer/wild battle type.


## v0p23 HF3 stationary-turn verification

A bridge KEY1 acknowledgement is not the same thing as the game having committed the new ObjectEvent facing byte. Support capture 2026-09-15 20:39 showed facing commonly one direction behind on the first immediate read. HF3 therefore treats direction input as edge-triggered: exactly one pulse is sent at FAST=0, then ObjectEvent X/Y/facing and gMain.inBattle are polled. No retry pulse is allowed merely because facing RAM is stale.


## HF4 stationary-spin direction invariant

A stationary turn is only safe when the requested direction differs from the
player ObjectEvent's live facing. Sending the same direction can advance one
tile. Therefore Wild Spin derives each pulse from live RAM and rotates clockwise
from the current facing. X/Y is checked before, during, and after each turn.

The field engine can also ignore a second KEY1 delivered too soon after a turn.
HF4 adds a short FAST=0 settle window and treats an ignored pulse as non-fatal
provided X/Y never changed.


## v0p23 HF5 action-menu proof fallback

The preferred Run Away authority remains the player-side
`CONTROLLER_CHOOSEACTION` controller command. Hardware support capture
`20260915_205204` showed a real visible Fight/Bag/Pokémon/Run menu with the
player controller-exec bit live while the strict command-byte gate still
returned no menu.

HF5 therefore adds a second, input-safe proof:

1. Require `gMain.inBattle`.
2. Require a player controller-exec bit (battler 0/2).
3. Force mGBA fast-forward off.
4. Send one horizontal D-pad pulse only.
5. Require `gActionSelectionCursor` to move to the exact value predicted by the
   2x2 action-menu layout.
6. Only after that observed RAM transition may the worker navigate to cursor 3.
7. Re-read cursor 3 immediately before sending A.

D-pad alone cannot advance battle text, and A is never sent if the cursor proof
fails. This prevents a stale action-cursor byte from being treated as a live
menu while allowing retail/mGBA builds whose controller command byte does not
match the decomp-derived strict value to continue safely.

## v0p23 HF6 wild-intro text gate

A stable enemy PK3 can be available before Ruby/Sapphire has created the
Fight/Bag/Pokémon/Run action menu. Hardware testing showed the game waiting on
`Wild <species> appeared!`, so waiting only for `CONTROLLER_CHOOSEACTION` (or
probing the action cursor) deadlocked. HF6 treats a non-reactive D-pad menu
probe as proof that the 2x2 menu is not currently interactive. Only then, with
FAST forced off, it may send A to advance the intro text. Before every further
A it repeats the menu proof, preventing a newly-opened Fight menu from ever
receiving an accidental A.


## v0p23 HF7 encounter outcome reporting

The encounter table's `Result` column is an outcome field. A wild encounter is
inserted as `Run pending…`; the worker emits a separate outcome event only after
`run_away()` completes and `wait_for_overworld()` RAM-confirms battle exit. The
UI then changes the row to `Ran Away`. A shiny stops immediately with
`Held — Shiny`. This prevents the table from claiming RUN succeeded before the
game has actually left battle.

## v0p23 HF8 all-battler battle-text authority

Ruby/Sapphire battle text is not guaranteed to be owned by the player's
controller slot. The wild intro can expose `CONTROLLER_PRINTSTRING` (16) or
`CONTROLLER_PRINTSTRINGPLAYERONLY` (17) on battler 1/3 while the player-side
battler 0/2 has no live menu candidate. Therefore wild-intro progression scans
all four `gBattleControllerExecFlags` bits and reads each corresponding
`gBattleBufferA[battler][0]` command.

The A gate is:

1. `gMain.inBattle == 1`.
2. No strict `CONTROLLER_CHOOSEACTION` menu is active.
3. A live controller on any battler reports command 16 or 17.
4. Force FAST=0 and re-read both action-menu and text-controller state.
5. If the same text command is still live, send exactly one A.
6. If command 18 appeared instead, cancel the text A and enter RUN navigation.

This removes the false stop on `Wild <Pokémon> appeared!` without permitting a
late A to select Fight from an already-open action menu.

## v0p24 R/S world and Hunt authority

`gen3bot/data/rs_world.py` reads the retail ROM's `gWildMonHeaders` table rather
than maintaining route encounter lists in UI code. Version-specific ROM symbols
cover AXVE/AXPE rev00/rev01. The decoded table is the common authority for the
Hunt tab, species Encounter %, all-map browser and AppData world cache.

Hunt policy is orthogonal to shiny safety. Each `(game, map group, map number,
method, species)` independently stores Target/Allowed/Blocked, allowing multiple
Targets. A genuine shiny always holds the battle even when its species is marked
Blocked.

`gen3bot/ui/live_state.py` owns the idle RAM poller. It reads party data,
Illuminate/Stench, White/Black Flute flags, Cleanse Tag, bike state and terrain,
then applies the exact R/S `DoWildEncounterTest` modifier order for the live
encounter percentage display.

`gen3bot/data/terrain.py` resolves the loaded MapLayout and metatile-attribute
pointer and then consults R/S `sTileBitAttributes`: bit 0 is wild-encounter
terrain and bit 1 is surfable. Future movement modes must test the destination
through this reader before issuing Walk/Run/Bike input, so the edge of a grass
patch is a RAM-derived hard boundary rather than a timing guess.
