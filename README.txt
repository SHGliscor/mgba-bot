Pokebot3DS-CFW — mGBA Gen 3 Bot v0p24 RS World/Hunt
======================================================

This package is built from the hardware-proven HF8 Wild Spin base. The starter
backend/UI/bridge binaries remain frozen at v0p21 HF1 while the PC app adds the
Ruby/Sapphire world database, Hunt tab, live party/encounter-rate telemetry,
sprite variants and movement-boundary data.

SUPPORTED GAMES
- Pokémon Ruby English AXVE rev 00 — v1.0
- Pokémon Ruby English AXVE rev 01 — v1.1
- Pokémon Sapphire English AXPE rev 00 — v1.0
- Pokémon Sapphire English AXPE rev 01 — v1.1

INSTALL
Install:
  build\Pokebot3DS-CFW-mGBA-StarterRNGGate-v0p21.cia

Then launch the CIA, load the in-game save at Professor Birch's bag on
Route 101 with an empty party, and run:
  run_UI.bat

STARTERS
- Treecko
- Torchic
- Mudkip
- Random

STABILITY
The starter engine is based on the v0p19 generation-aware build that completed
900+ Ruby resets without a duplicate PID in hardware testing. HF1 also restores
v0p19's closed-loop starter cursor validation instead of assuming a LEFT/RIGHT
input was accepted during fast-forward.

UI
HF1 restores the full v0p19 Current UI:
- Pokémon-themed dark dashboard
- styled header and tabs
- current Pokémon sprite/card
- full Encounter Log with normal/shiny/anti-shiny sprites
- Recent Shinies tab
- phase records and continuously updating phase timer
- RTC/RNG diagnostics with generation-aware telemetry
- session and lifetime statistics
- support export

RNG POLICY
- Live retail-style RTC
- generation-aware frame-local starter confirmation
- STARTER_RECORD feedback from decoded PK3
- no RNG RAM writes
- no RTC writes
- no artificial reseeding

Default bridge:
  192.168.1.21:4953

AppData:
  %APPDATA%\Pokebot3DS-CFW\Gen3\

============================================================
v0p22 MODULAR APP
============================================================
Starters are frozen at the verified v0p21 HF1 baseline.

Use run_UI.bat for the full Gen 3 app with the new Wild Encounters tab.
Use run_Starter_UI_Frozen.bat to launch the exact frozen starter-only UI.

First wild hardware milestone:
1. Stand on Route 101 grass with at least one Pokémon in the party.
2. Open Wild Encounters.
3. Method: Spin.
4. Start Wild Hunt.
5. Confirm encounter -> PK3 log -> RUN -> same tile -> Spin resumes.

The v0p22 wild code does not arm or modify the starter RNG gate.


v0p23 Dashboard mode selection
-------------------------------
The main Dashboard now contains a Mode selector.

Mode = Starters:
  the second selector contains Torchic / Treecko / Mudkip / Random.

Mode = Wild Encounters:
  the second selector becomes Method and contains Spin, Run, Walk, Acro Bike Bunny Hop, Sweet Scent, Fishing, Rock Smash, Safari and Feebas.

Spin is the first implemented wild method. Wild encounters use the same main Dashboard and encounter log rather than a separate tab.


v0p23 HF1 Wild Spin / RUN reliability
--------------------------------------
This hotfix addresses the 2026-09-15 Wild Spin hardware support bundle.

Stationary Spin no longer sends direction input while mGBA fast-forward is
active. FAST is forced off for the turn, the original tile is checked from the
player ObjectEvent, and the requested facing is verified before continuing.

Run Away no longer trusts the persistent action-cursor byte by itself and no
longer spams B through battle text. The worker waits for Ruby/Sapphire's live
player CHOOSEACTION controller command, navigates to RUN closed-loop at FAST=0,
re-confirms cursor 3, submits A, and verifies that the controller accepted RUN.
If escape fails and the command menu comes back, it repeats the same verified
sequence.

Starting Wild Spin while a battle is already active is a safety stop. The
existing opponent is not counted as a new encounter and is never automatically
run from. This prevents a worker restart from re-logging the same PID.

v0p23 HF2 Battle lifetime authority
------------------------------------
Wild startup/encounter/exit checks use Ruby/Sapphire's actual gMain.inBattle
bit. Stale gBattleTypeFlags are not treated as proof that a battle is active.

v0p23 HF3 Spin facing verification polling
-------------------------------------------
A KEY1 bridge acknowledgement can arrive before Ruby/Sapphire commits the new
ObjectEvent facing byte. HF3 sends exactly one direction pulse at FAST=0 and
then polls RAM for the facing change for up to 0.60 s. It never resends a
turn just because the first facing read is stale. X/Y is checked throughout;
if a real battle begins first, the encounter engine takes over immediately.


v0p23 HF4 Safe Spin direction guard
------------------------------------
Support capture 20:45 proved that a fixed direction sequence is unsafe after a
restart: if the player already faces the requested direction, Ruby/Sapphire can
move one tile instead of merely turning. HF4 reads live ObjectEvent facing
before every KEY1 and substitutes a clockwise direction whenever needed.

Confirmed turns also receive a 100 ms no-input settle window. An ignored turn
with unchanged X/Y is logged and retried safely rather than treated as a fatal
error. Actual X/Y drift remains an immediate safety stop.


v0p23 HF5 Run menu proof fallback
----------------------------------
Support capture 20:52 showed the Fight/Bag/Pokémon/Run menu visibly open with
the player controller-exec bit set, but the strict gBattleBufferA command-byte
check never reported CONTROLLER_CHOOSEACTION. Because of that, no RUN
navigation or A press occurred.

HF5 keeps command 18 as the preferred authority, but adds a safe fallback:
while FAST=0 it sends a harmless horizontal D-pad pulse and requires
gActionSelectionCursor to move to the exact predicted 2x2-menu position. Only
that observed RAM cursor transition proves the menu. The worker then navigates
to RUN, re-reads cursor 3, and only then sends A.

If the cursor does not react as predicted, the menu is not considered proven
and A is never sent. Support exports now include the broader action-menu
candidate state and raw command value for diagnosis.


v0p23 HF6 Wild intro A gate
----------------------------
Hardware testing showed the wild encounter was decoded correctly while the
battle was still stopped on "Wild <Pokémon> appeared!". The action menu cannot
exist until that text is advanced. HF6 therefore extends the existing safe
D-pad menu probe: when the probe proves the Fight/Bag/Pokémon/Run menu is NOT
interactive, the worker may send one bounded A press at FAST=0 to advance the
wild-intro text. It then probes again before any further A press.

This means A is never gambled on an unverified action menu. If the menu opens,
its cursor reacts to the D-pad proof and the worker switches immediately to the
RAM-verified RUN path. Up to three intro A presses are allowed only when each
preceding menu proof showed no cursor response. Shiny encounters still hold
before run-away logic and therefore receive no automatic intro A press.


v0p23 HF7 Encounter Result Fix
------------------------------
Wild Encounter Log Result now shows the actual completed outcome. Non-shinies
briefly show "Run pending…" and become "Ran Away" only after RAM confirms the
overworld return. Shinies show "Held — Shiny". The column is content-sized to
avoid ellipsized "Normal — ..." rows.


v0p23 HF8 Wild text controller fix
----------------------------------
A remaining hardware case showed a real wild encounter stopping on
"Wild <Pokémon> appeared!" without an A press. The earlier intro gate only
looked for player-side controller candidates (battlers 0/2), but Ruby/Sapphire
can own the opening PRINTSTRING command on the opponent-side controller
(battler 1/3).

HF8 scans every live controller-exec bit and recognises PRINTSTRING (16) and
PRINTSTRINGPLAYERONLY (17) directly. FAST is forced off, the command is re-read,
and A is sent only if that same text command is still live. If the action menu
appears during the hand-off, the A is cancelled and the RAM-verified RUN path
takes over immediately.

============================================================
v0p24 — RUBY / SAPPHIRE WORLD + HUNT
============================================================

The Hunt tab is RAM-driven and version-aware. When connected to Ruby/Sapphire it scans the
retail gWildMonHeaders table, caches every wild map in AppData, and lets you browse the
whole wild database or follow the current map automatically.

Each species can independently be Target, Allowed or Blocked. Multiple Target entries are
supported at the same time. Every shiny still causes a safety HOLD regardless of block state.

The tab also displays the map's base encounter probability and the live effective probability
after R/S modifiers such as Illuminate, White Flute and bike movement. The species-specific
slot percentage is shown in the Hunt list and encounter logs.

The Party tab refreshes all six party slots from RAM even while idle. Wurmple entries show
the deterministic PID branch (Silcoon → Beautifly or Cascoon → Dustox).

Sprite assets use the pinned 40Cakes/pokebot-gen3 normal/shiny/anti-shiny tree. The app can
sync missing UI sprites in the background; SYNC_40CAKES_SPRITES.bat performs a complete
pre-fetch. Hunting logic never depends on the sprite download.

Spin remains the hardware-validated wild method in this release. Other wild methods remain
disabled until their movement/action path is verified on hardware; the new terrain reader is
the grass/water boundary authority they will use.
