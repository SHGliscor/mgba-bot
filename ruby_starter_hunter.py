#!/usr/bin/env python3
import socket, struct, sys, time

DEFAULT_IP = "192.168.1.21"
PORT = 4953

# English Pokémon Ruby/Sapphire v1.0-v1.1 / AXVE/AXPE revisions 00-01
ADDR = {
    "gPlayerPartyCount": 0x03004350,
    "gPlayerParty":      0x03004360,
    "gEnemyParty":       0x030045C0,
    "gObjectEvents":     0x030048A0,
    "gTasks":            0x03004B20,
    "gPlayerAvatar":     0x0202E858,
    "gSaveBlock1":       0x02025734,
    "gSaveBlock2":       0x02024EA4,
    "gMain":             0x03001770,
    "gRngValue":         0x03004818,
    "revision":          0x080000BC,
}

STARTER_SYMBOLS_BY_REVISION = {
    0: {"Task_StarterChoose2": 0x0810A178, "CB2_ChooseStarter": 0x08109E80},
    1: {"Task_StarterChoose2": 0x0810A198, "CB2_ChooseStarter": 0x08109EA0},
}
TASK_SIZE = 40
TASK_COUNT = 16

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
HP_TYPES = ["Fighting","Flying","Poison","Ground","Rock","Bug","Ghost","Steel",
            "Fire","Water","Grass","Electric","Psychic","Ice","Dragon","Dark"]

SPECIES = {
    277: ("Treecko", "Overgrow", 31),
    280: ("Torchic", "Blaze", 31),
    283: ("Mudkip", "Torrent", 31),
    286: ("Poochyena", "Run Away", 127),
}
TARGET_IDS = {"TREECKO":277, "TORCHIC":280, "MUDKIP":283}

GAME_PROFILES = {
    "AXVE": {"name": "Pokémon Ruby", "short_name": "Ruby"},
    "AXPE": {"name": "Pokémon Sapphire", "short_name": "Sapphire"},
}
SUPPORTED_REVISIONS = {0: "1.0", 1: "1.1"}

def detect_supported_game(bridge):
    """Detect English Ruby/Sapphire v1.0-v1.1 from PB3 GAME + ROM revision byte."""
    game_reply = bridge.cmd("GAME")
    fields = game_reply.split()
    code = next((f for f in fields if f in GAME_PROFILES), None)
    revision = bridge.read(ADDR["revision"], 1)[0]
    if code is None:
        raise RuntimeError(
            f"Unsupported ROM. Expected English Ruby AXVE or Sapphire AXPE; "
            f"got {game_reply}, rev {revision:02X}."
        )
    profile = dict(GAME_PROFILES[code])
    if revision not in SUPPORTED_REVISIONS:
        raise RuntimeError(
            f"Unsupported {profile['short_name']} revision {revision:02X}. "
            "This build supports revision 00 (v1.0) and revision 01 (v1.1)."
        )
    profile.update({
        "code": code,
        "revision": revision,
        "version": SUPPORTED_REVISIONS[revision],
        "game_reply": game_reply,
        "starter_symbols": dict(STARTER_SYMBOLS_BY_REVISION[revision]),
    })
    return profile


class Bridge:
    """PB3 UDP client with reply correlation.

    Probe 1 has no transaction IDs, so delayed UDP replies can remain queued.
    Never assume the next PB3 packet belongs to the command we just sent.
    """

    def __init__(self, ip, port=PORT, timeout=0.8):
        self.addr = (ip, port)
        self.timeout = float(timeout)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)
        self.ignored_replies = 0
        self.last_ignored_reply = None

    @staticmethod
    def _expected(command, reply):
        if not reply.startswith("PB3 "):
            return False

        fields = command.split()
        if not fields:
            return False
        op = fields[0].upper()

        if reply.startswith("PB3 ERR "):
            # An error is relevant only when it names the operation where
            # possible; otherwise surface it rather than silently spinning.
            return True

        if op == "PING":
            return reply.startswith("PB3 PONG ")
        if op == "GAME":
            return reply.startswith("PB3 GAME ")
        if op == "STATUS":
            return reply.startswith("PB3 STATUS ")

        if op == "READ" and len(fields) >= 3:
            parts = reply.split()
            if len(parts) != 5 or parts[:2] != ["PB3", "DATA"]:
                return False
            try:
                wanted_addr = int(fields[1], 16)
                wanted_len = int(fields[2], 0)
                got_addr = int(parts[2], 16)
                got_len = int(parts[3], 0)
            except ValueError:
                return False
            return got_addr == wanted_addr and got_len == wanted_len

        if op == "KEY" and len(fields) >= 2:
            # Firmware clamps duration to 16..5000 ms.
            wanted_name = fields[1].upper()
            wanted_ms = 100
            if len(fields) >= 3:
                try:
                    wanted_ms = int(fields[2], 0)
                except ValueError:
                    pass
            wanted_ms = max(16, min(5000, wanted_ms))
            return reply == f"PB3 OK KEY {wanted_name} {wanted_ms}"

        if op == "SOFTRESET":
            return reply == "PB3 OK SOFTRESET"

        if op == "FAST" and len(fields) >= 2:
            wanted = "1" if fields[1] not in ("0", "OFF", "False", "false") else "0"
            return reply == f"PB3 OK FAST {wanted}"

        if op == "KEY1" and len(fields) >= 2:
            return reply == f"PB3 OK KEY1 {fields[1].upper()}"
        if op == "STARTER_OPEN":
            return reply.startswith("PB3 STARTER_OPEN ")
        if op == "STARTER_ARM":
            return reply.startswith("PB3 OK STARTER_ARM ")
        if op == "STARTER_RECORD":
            return reply.startswith("PB3 OK STARTER_RECORD ")
        if op == "STARTER_RESULT":
            return reply.startswith("PB3 STARTER_RESULT ")
        if op == "STARTER_CANCEL":
            return reply == "PB3 OK STARTER_CANCEL"
        if op == "STARTER_CLEAR":
            return reply == "PB3 OK STARTER_CLEAR"

        return reply.startswith("PB3 ")

    def _receive_matching(self, command, deadline):
        last = None
        while time.monotonic() < deadline:
            remaining = max(0.001, deadline - time.monotonic())
            self.sock.settimeout(remaining)
            try:
                data, peer = self.sock.recvfrom(2048)
            except (TimeoutError, socket.timeout) as e:
                last = e
                break

            # Ignore packets from anything other than the configured 3DS.
            if peer[0] != self.addr[0] or peer[1] != self.addr[1]:
                self.ignored_replies += 1
                continue

            reply = data.decode("ascii", errors="replace").strip()
            if self._expected(command, reply):
                if reply.startswith("PB3 ERR "):
                    raise RuntimeError(f"Bridge error for {command}: {reply}")
                return reply

            # This is almost always a delayed response to an older command.
            self.ignored_replies += 1
            self.last_ignored_reply = reply
            last = RuntimeError(f"ignored stale reply: {reply}")

        raise RuntimeError(f"No matching bridge reply for {command}: {last}")

    def cmd(self, command, retries=None):
        payload = ("PB3 " + command).encode("ascii")
        op = command.split()[0].upper() if command.split() else ""

        # KEY/SOFTRESET/FAST have side effects. Never retransmit them merely
        # because their acknowledgement was delayed: duplicate KEY commands
        # can carry input into the next game state and duplicate SOFTRESET is
        # obviously undesirable.
        side_effect = op in {"KEY", "KEY1", "STARTER_OPEN", "SOFTRESET", "FAST", "STARTER_ARM", "STARTER_RECORD", "STARTER_CANCEL", "STARTER_CLEAR"}
        if retries is None:
            retries = 1 if side_effect else 3
        if side_effect:
            retries = 1

        last = None
        original_timeout = self.sock.gettimeout()
        try:
            for attempt in range(max(1, retries)):
                try:
                    self.sock.sendto(payload, self.addr)

                    # Side-effect acknowledgements may be delayed while mGBA
                    # is running uncapped, so give them a little more room.
                    wait = max(self.timeout, 1.15 if side_effect else self.timeout)
                    return self._receive_matching(
                        command, time.monotonic() + wait
                    )
                except (TimeoutError, socket.timeout, OSError, RuntimeError) as e:
                    last = e
                    if side_effect:
                        break
                    if attempt + 1 < retries:
                        # Briefly consume obvious stale packets before a safe
                        # idempotent retry. Reply matching still remains active.
                        drain_until = time.monotonic() + 0.015
                        self.sock.setblocking(False)
                        try:
                            while time.monotonic() < drain_until:
                                try:
                                    data, _ = self.sock.recvfrom(2048)
                                    self.ignored_replies += 1
                                    self.last_ignored_reply = data.decode(
                                        "ascii", errors="replace"
                                    ).strip()
                                except BlockingIOError:
                                    break
                        finally:
                            self.sock.setblocking(True)
                            self.sock.settimeout(self.timeout)

            raise RuntimeError(
                f"No bridge reply for {command}: {last}"
            )
        finally:
            self.sock.settimeout(original_timeout if original_timeout is not None else self.timeout)

    def read(self, address, length):
        out = bytearray()
        while length:
            n = min(length, 128)
            reply = self.cmd(f"READ {address:08X} {n}")

            parts = reply.split()
            if len(parts) != 5 or parts[0] != "PB3" or parts[1] != "DATA":
                # _expected() should already have filtered this.
                raise RuntimeError("Bad READ reply after correlation: " + reply)
            try:
                reply_address = int(parts[2], 16)
                reply_length = int(parts[3], 0)
                chunk = bytes.fromhex(parts[4])
            except ValueError as e:
                raise RuntimeError("Malformed READ reply: " + reply) from e

            if reply_address != address:
                raise RuntimeError(
                    f"READ address mismatch after correlation: "
                    f"requested {address:08X}, got {reply_address:08X}"
                )
            if reply_length != n:
                raise RuntimeError(
                    f"READ length mismatch: requested {n}, got {reply_length}"
                )
            if len(chunk) != n:
                raise RuntimeError(
                    f"Short READ at {address:08X}: {len(chunk)} != {n}"
                )

            out += chunk
            address += n
            length -= n
        return bytes(out)

    def key(self, name, ms=80):
        return self.cmd(f"KEY {name} {ms}", retries=1)

    def key1(self, name):
        return self.cmd(f"KEY1 {name}", retries=1)

    def starter_open(self):
        return self.cmd("STARTER_OPEN", retries=1)

    def soft_reset(self):
        return self.cmd("SOFTRESET", retries=1)

    def fast(self, enabled):
        return self.cmd("FAST 1" if enabled else "FAST 0", retries=1)

    def starter_arm(self, seed, delay_frames=0):
        seed = int(seed) & 0xFFFF
        delay_frames = max(0, min(240, int(delay_frames)))
        return self.cmd(
            f"STARTER_ARM {seed:04X} {delay_frames}",
            retries=1,
        )

    def starter_record(self, generation_pre_rng, confirm_to_pid_calls=0):
        generation_pre_rng = int(generation_pre_rng) & 0xFFFFFFFF
        confirm_to_pid_calls = max(0, min(255, int(confirm_to_pid_calls or 0)))
        return self.cmd(
            f"STARTER_RECORD {generation_pre_rng:08X} {confirm_to_pid_calls}",
            retries=1,
        )

    def starter_result(self):
        return self.cmd("STARTER_RESULT", retries=3)

    def starter_cancel(self):
        return self.cmd("STARTER_CANCEL", retries=1)

    def starter_clear(self):
        return self.cmd("STARTER_CLEAR", retries=1)

def parse_starter_result(reply):
    """Parse PB3 STARTER_RESULT into a compact dict."""
    parts = reply.split()
    if len(parts) < 4 or parts[:2] != ["PB3", "STARTER_RESULT"]:
        raise RuntimeError("Bad STARTER_RESULT reply: " + reply)
    out = {"state": parts[2]}
    for token in parts[3:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.lower()
        try:
            if key in ("seed",):
                out[key] = int(value, 16)
            elif key in ("rng",):
                out[key] = int(value, 16)
            else:
                out[key] = int(value, 0)
        except ValueError:
            out[key] = value
    return out

def u16(b): return int.from_bytes(b, "little")
def u32(b): return int.from_bytes(b, "little")

def decode_mon(raw):
    if len(raw) < 100 or raw == b"\0"*100:
        return None
    pid = u32(raw[0:4])
    ot32 = u32(raw[4:8])
    key = pid ^ ot32
    order = SUBSTRUCT_ORDERS[pid % 24]
    logical = [None]*4
    for logical_index in range(4):
        physical_index = order[logical_index]
        enc = raw[32 + physical_index*12 : 32 + (physical_index+1)*12]
        words = [u32(enc[i:i+4]) ^ key for i in range(0,12,4)]
        logical[logical_index] = b"".join(w.to_bytes(4,"little") for w in words)
    dec48 = b"".join(logical)
    stored = u16(raw[28:30])
    calc = sum(struct.unpack("<24H", dec48)) & 0xFFFF
    if stored != calc:
        return None

    species_id = u16(dec48[0:2])
    species_name, ability, gender_threshold = SPECIES.get(species_id, (f"Species {species_id}", "Unknown", 255))
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

    a = ((ivs["hp"]&1)<<0)|((ivs["atk"]&1)<<1)|((ivs["def"]&1)<<2)|((ivs["spe"]&1)<<3)|((ivs["spa"]&1)<<4)|((ivs["spd"]&1)<<5)
    hp_type = HP_TYPES[(a*15)//63]
    b = (((ivs["hp"]&2)>>1)|((ivs["atk"]&2)<<0)|((ivs["def"]&2)<<1)|((ivs["spe"]&2)<<2)|((ivs["spa"]&2)<<3)|((ivs["spd"]&2)<<4))
    hp_power = (b*40)//63 + 30

    return {
        "species_id": species_id, "species": species_name, "pid": pid, "nature": NATURES[pid % 25],
        "tid": tid, "sid": sid, "sv": sv, "shiny": shiny, "gender": gender,
        "ivs": ivs, "ability": ability, "ability_slot": ability_slot,
        "hidden_power": hp_type, "hidden_power_power": hp_power,
        "level": raw[84], "hp": u16(raw[86:88]), "checksum": stored,
    }

def player_state(b):
    # Probe 2 proved that Ruby/Sapphire's authoritative loaded map is in
    # gSaveBlock1[4:6]. Do not reject a valid save based on transient
    # ObjectEvent flag bits.
    save_head = b.read(ADDR["gSaveBlock1"], 8)
    map_group = save_head[4]
    map_num = save_head[5]

    avatar = b.read(ADDR["gPlayerAvatar"], 0x24)
    oid = avatar[5]

    state = {
        "map_group": map_group,
        "map_num": map_num,
        "x": None,
        "y": None,
        "facing": "?",
        "object_event_id": oid,
    }

    # Object event is only used for local X/Y/facing. The map check above
    # remains valid even during the short frames where object flags are
    # being rebuilt after a reset/map load.
    if oid < 16:
        obj = b.read(ADDR["gObjectEvents"] + oid*0x24, 0x24)
        flags = u32(obj[0:4])
        if flags & 1:
            state["x"] = u16(obj[0x10:0x12]) - 7
            state["y"] = u16(obj[0x12:0x14]) - 7
            direction = u16(obj[0x18:0x1A]) & 0xF
            state["facing"] = {1:"Down",2:"Up",3:"Left",4:"Right"}.get(direction,"?")

    return state

def task_active(b, function_address):
    raw = b.read(ADDR["gTasks"], TASK_SIZE*TASK_COUNT)
    for i in range(TASK_COUNT):
        task = raw[i*TASK_SIZE:(i+1)*TASK_SIZE]
        if task[4] and u32(task[0:4]):
            fn = u32(task[0:4]) - 1
            if fn == function_address:
                return True
    return False

STARTER_SELECTION_INDEX = {
    "TREECKO": 0,
    "TORCHIC": 1,
    "MUDKIP": 2,
}

def _starter_symbols_for_loaded_rom(b):
    revision = b.read(ADDR["revision"], 1)[0]
    try:
        return STARTER_SYMBOLS_BY_REVISION[revision]
    except KeyError:
        raise RuntimeError(
            f"Unsupported Ruby/Sapphire revision {revision:02X}. "
            "Supported revisions are 00 (v1.0) and 01 (v1.1)."
        )

def starter_selection(b):
    """Return Ruby/Sapphire's authoritative Birch-bag cursor index.

    Task_StarterChoose2 stores tStarterSelection in data[0]:
      0 = Treecko, 1 = Torchic, 2 = Mudkip.

    Reading this value avoids assuming that a LEFT/RIGHT pulse was accepted
    while mGBA is running at fast-forward.
    """
    raw = b.read(ADDR["gTasks"], TASK_SIZE * TASK_COUNT)
    for i in range(TASK_COUNT):
        task = raw[i*TASK_SIZE:(i+1)*TASK_SIZE]
        if not task[4]:
            continue
        ptr = u32(task[0:4])
        if ptr and (ptr - 1) == _starter_symbols_for_loaded_rom(b)["Task_StarterChoose2"]:
            selection = u16(task[8:10])
            if 0 <= selection <= 2:
                return selection
    return None

def select_starter_cursor(b, target_name, fast_forward=False, precise_input=False, timeout=1.5):
    """Move the Birch starter cursor and verify the actual task value.

    This is deliberately closed-loop: input is repeated only until Ruby/Sapphire itself
    reports the requested tStarterSelection. It prevents Random mode from
    silently falling back to the default centre Torchic when a one-frame
    LEFT/RIGHT input is missed during fast-forward.
    """
    desired = STARTER_SELECTION_INDEX[target_name]
    pulse = 38 if fast_forward else 60
    deadline = time.monotonic() + timeout
    last_selection = None
    moves = 0

    while time.monotonic() < deadline:
        # If generation somehow already occurred, let the caller validate the
        # resulting species rather than sending any more direction inputs.
        try:
            if b.read(ADDR["gPlayerPartyCount"], 1)[0] >= 1:
                return None
        except RuntimeError:
            pass

        selection = starter_selection(b)
        if selection is not None:
            last_selection = selection
            if selection == desired:
                return selection

            direction = "LEFT" if selection > desired else "RIGHT"
            if precise_input:
                b.key1(direction)
            else:
                b.key(direction, pulse)
            moves += 1

            # Ensure there is at least one input-poll opportunity with the
            # direction released before another JOY_NEW direction is sent.
            time.sleep(0.025 if fast_forward else 0.055)
            continue

        # Task_StarterChoose1 exists for a very short setup frame before
        # Task_StarterChoose2. Wait for the authoritative selection task.
        if starter_screen_active(b):
            time.sleep(0.010 if fast_forward else 0.020)
            continue

        time.sleep(0.010)

    raise RuntimeError(
        f"Starter cursor failed to reach {target_name} "
        f"(wanted index {desired}, last={last_selection}, moves={moves})."
    )

def main_callback2(b):
    # gMain.callback2 is at +4. Clear Thumb bit if present.
    ptr = u32(b.read(ADDR["gMain"] + 4, 4))
    return ptr & ~1

def starter_screen_active(b):
    return main_callback2(b) == _starter_symbols_for_loaded_rom(b)["CB2_ChooseStarter"]

def wait_for_loaded_route101(b, timeout=20):
    end = time.monotonic() + timeout
    last_a = 0.0
    while time.monotonic() < end:
        try:
            st = player_state(b)
            if st and st["map_group"] == 0 and st["map_num"] == 16:
                return st
        except RuntimeError:
            pass
        now = time.monotonic()
        if now - last_a > 0.18:
            try: b.key("A", 70)
            except RuntimeError: pass
            last_a = now
        time.sleep(0.04)
    raise RuntimeError("Timed out waiting for the saved Route 101 position after reset.")

def open_starter_bag(b, st, fast_forward=False, timeout=5, precise_input=False):
    # Same geometry used by PokéBot Gen3: from the right side face left,
    # otherwise face up. If ObjectEvent coordinates are still rebuilding,
    # wait briefly for them before choosing a direction.
    if st.get("x") is None or st.get("y") is None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            newer = player_state(b)
            if newer.get("x") is not None and newer.get("y") is not None:
                st = newer
                break
            time.sleep(0.03)

    pulse = 20 if fast_forward else 70

    if st.get("x") == 8 and st.get("y") == 14:
        if precise_input:
            b.key1("LEFT")
        else:
            b.key("LEFT", pulse)
    else:
        if precise_input:
            b.key1("UP")
        else:
            b.key("UP", pulse)
    time.sleep(0.04 if fast_forward else 0.12)

    # v0p11 CIA path: STARTER_OPEN performs an atomic callback check inside
    # mGBA immediately before returning the emulated input. It sends a
    # one-frame A only while we are NOT yet in CB2_ChooseStarter, so an A
    # can no longer carry across the transition and accidentally choose
    # Torchic before the RNG gate is armed.
    if precise_input:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            reply = b.starter_open()
            if reply.startswith("PB3 STARTER_OPEN SCREEN"):
                return "screen"
            time.sleep(0.008 if fast_forward else 0.02)
        raise RuntimeError("CIA STARTER_OPEN timed out before starter screen.")

    # Compatibility path for older CIAs.
    end = time.monotonic() + timeout
    last_a = 0.0
    while time.monotonic() < end:
        if starter_screen_active(b):
            return "screen"

        try:
            if b.read(ADDR["gPlayerPartyCount"], 1)[0] >= 1:
                return "generated"
        except RuntimeError:
            pass

        now = time.monotonic()
        interval = 0.12 if fast_forward else 0.25
        if now - last_a > interval:
            b.key("A", pulse)
            last_a = now
        time.sleep(0.02 if fast_forward else 0.04)

    # Final fallback: the transient task still helps at normal speed.
    if task_active(b, _starter_symbols_for_loaded_rom(b)["Task_StarterChoose2"]):
        return "screen"
    raise RuntimeError(
        f"Starter screen was not detected (callback=0x{main_callback2(b):08X})."
    )

def generate_starter(b, target_name, entry_state="screen", fast_forward=False, timeout=8, before_confirm=None, local_confirm=None, precise_input=False):
    # At fast-forward speeds, very short pulses can be missed entirely.
    # 50 ms proved a better compromise: long enough to register, still short
    # enough not to sit on A for a large number of emulated frames.
    pulse = 50 if fast_forward else 65

    # Under mGBA fast-forward an A pulse can occasionally carry through the
    # bag transition and generate the centre starter before our next UDP poll.
    # A checksum-valid PK3 is still a real encounter; never turn it into a
    # false safety-stop failure.
    if entry_state == "generated":
        end = time.monotonic() + min(timeout, 2.0)
        while time.monotonic() < end:
            count = b.read(ADDR["gPlayerPartyCount"], 1)[0]
            if count >= 1:
                mon = decode_mon(b.read(ADDR["gPlayerParty"], 100))
                if mon:
                    mon["_rng_guard_bypassed"] = (before_confirm is not None or local_confirm is not None)
                    return mon
            time.sleep(0.012 if fast_forward else 0.025)
        raise RuntimeError(
            "Starter was generated visually, but no checksum-valid PK3 "
            "could be read from gPlayerParty."
        )

    if entry_state == "screen":
        # Closed-loop cursor navigation. Never assume a direction pulse was
        # accepted: verify Ruby/Sapphire's Task_StarterChoose2.tStarterSelection before
        # arming the RNG gate / pressing A.
        select_starter_cursor(
            b,
            target_name,
            fast_forward=fast_forward,
            precise_input=precise_input,
        )

    # A fast-forward input can complete generation between the screen poll
    # above and this exact point. Trust party count + checksum-valid PK3 first.
    try:
        if b.read(ADDR["gPlayerPartyCount"], 1)[0] >= 1:
            mon = decode_mon(b.read(ADDR["gPlayerParty"], 100))
            if mon:
                mon["_rng_guard_bypassed"] = (before_confirm is not None or local_confirm is not None)
                return mon
    except RuntimeError:
        pass

    # Best path: let the modified mGBA CIA sample gRngValue and inject A
    # inside the emulator input poll. This removes the Wi-Fi round trip from
    # the RNG-critical confirmation frame.
    local_confirmation_injected = False
    if local_confirm is not None:
        local_confirm(target_name)
        local_confirmation_injected = True
    elif before_confirm is not None:
        # Compatibility fallback for older CIAs.
        before_confirm()

    end = time.monotonic() + timeout
    last_a = time.monotonic() if local_confirmation_injected else 0.0
    pulses = 0

    while time.monotonic() < end:
        count = b.read(ADDR["gPlayerPartyCount"], 1)[0]
        if count >= 1:
            mon = decode_mon(b.read(ADDR["gPlayerParty"], 100))
            if mon:
                return mon

        now = time.monotonic()

        # Do not require CB2_ChooseStarter to still be visible before every A.
        # The callback/task can transition between UDP polls at fast-forward.
        # Party count is the authoritative completion signal.
        interval = 0.10 if fast_forward else 0.14
        # The CIA gate already injected a one-emulated-frame A. Give the game
        # enough time to create the PK3 before any compatibility fallback A.
        min_after_local = 0.65 if fast_forward else 0.90
        if (
            (not local_confirmation_injected or now - last_a >= min_after_local)
            and now - last_a > interval
        ):
            b.key("A", pulse)
            last_a = now
            pulses += 1
            local_confirmation_injected = False

        time.sleep(0.015 if fast_forward else 0.025)

    raise RuntimeError(
        f"Timed out waiting for generated starter in gPlayerParty "
        f"after {pulses} A pulses (callback=0x{main_callback2(b):08X})."
    )

def print_mon(mon, attempt, elapsed):
    iv = mon["ivs"]
    rate = attempt / elapsed * 3600 if elapsed > 0 else 0
    print(f"\n[{attempt}] {mon['species']}  PID {mon['pid']:08X}  {mon['nature']}  {mon['gender']}")
    print(f"    IVs {iv['hp']}/{iv['atk']}/{iv['def']}/{iv['spa']}/{iv['spd']}/{iv['spe']}  "
          f"HP {mon['hidden_power']} {mon['hidden_power_power']}  SV {mon['sv']}")
    print(f"    Shiny: {'YES' if mon['shiny'] else 'no'}   Rate: {rate:.1f}/hour")

def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IP
    print("============================================================")
    print(" Pokebot3DS-CFW mGBA Gen 3 Starter Hunter v0p21 HF1")
    print("============================================================")
    print(f"Target: {ip}:{PORT}")
    print("Support: English Ruby AXVE / Sapphire AXPE revisions 00 (v1.0) and 01 (v1.1)")
    print("RAM policy: READ ONLY (inputs/reset/fast-forward only)\n")

    choice = input("Starter [1=Treecko, 2=Torchic, 3=Mudkip] (default 2): ").strip() or "2"
    target = {"1":"TREECKO","2":"TORCHIC","3":"MUDKIP"}.get(choice)
    if not target:
        raise SystemExit("Invalid starter choice.")
    target_id = TARGET_IDS[target]

    ff_choice = input("mGBA fast-forward [0=Off, 1=On] (default 0): ").strip() or "0"
    if ff_choice not in ("0", "1"):
        raise SystemExit("Invalid fast-forward choice.")
    fast_forward = ff_choice == "1"

    b = Bridge(ip)
    print(b.cmd("PING"))
    try:
        profile = detect_supported_game(b)
    except RuntimeError as e:
        raise SystemExit(f"ERROR: {e}")
    print(profile["game_reply"])
    print(
        f"Detected game: {profile['name']} {profile['code']} "
        f"v{profile['version']} rev {profile['revision']:02X}"
    )

    st = player_state(b)
    print(
        f"Detected loaded state: map {st['map_group']}/{st['map_num']}  "
        f"coords=({st.get('x')},{st.get('y')}) facing={st.get('facing')}  "
        f"object={st.get('object_event_id')}"
    )
    if (st["map_group"], st["map_num"]) != (0,16):
        raise SystemExit(
            "ERROR: SaveBlock reports a map other than Route 101 (0/16). "
            "Do not press A manually; load the in-game save at Birch's bag and rerun."
        )
    print(f"Starting position accepted: {profile['short_name']} Route 101 / Birch starter bag area.")
    print(f"Target starter: {target.title()}")
    print(f"mGBA fast-forward: {'ON' if fast_forward else 'OFF'}")

    # FAST is an emulator-level override, equivalent to mGBA fast-forward.
    # HF3 verifies that the installed CIA keeps it enabled persistently.
    b.fast(fast_forward)
    time.sleep(0.15)
    status = b.cmd("STATUS")
    print(status)
    if fast_forward and "FAST=1" not in status:
        raise SystemExit(
            "ERROR: Fast-forward did not stay enabled. Install the FastOverride CIA "
            "or rerun with fast-forward Off. Physical ZR can still be used manually."
        )

    print("\nHunting. Ctrl+C stops safely.\n")

    attempt = 0
    started = time.monotonic()
    failures = 0

    try:
        while True:
            attempt += 1
            try:
                # Clear prior battle/party state by authentic Gen 3 soft reset.
                # Reassert emulator speed each attempt for robustness.
                b.fast(fast_forward)
                b.soft_reset()
                time.sleep(0.25)

                st = wait_for_loaded_route101(b)
                # Ensure the reset restored the empty pre-starter party.
                party_count = b.read(ADDR["gPlayerPartyCount"], 1)[0]
                if party_count != 0:
                    raise RuntimeError(f"Expected empty pre-starter party, got party count {party_count}.")

                bag_state = open_starter_bag(b, st, fast_forward=fast_forward)
                mon = generate_starter(
                    b, target,
                    entry_state=bag_state,
                    fast_forward=fast_forward,
                )

                if mon["species_id"] != target_id:
                    raise RuntimeError(
                        f"Selected {mon['species']} instead of {target.title()} "
                        f"(fast-forward input carried through the selection screen)."
                    )

                elapsed = time.monotonic() - started
                print_mon(mon, attempt, elapsed)
                failures = 0

                if mon["shiny"]:
                    b.fast(False)
                    print("\n" + "="*60)
                    print(f" SHINY {mon['species'].upper()} FOUND ON ATTEMPT {attempt}!")
                    print("="*60)
                    try:
                        import winsound
                        for _ in range(4):
                            winsound.Beep(1200, 250)
                            time.sleep(0.08)
                    except Exception:
                        print("\a\a\a")
                    print("Bot stopped. The N3DS has been left on the shiny starter.")
                    input("\nPress Enter to close this window...")
                    return

                # Short guard so the generated Pokémon is fully committed before reset.
                time.sleep(0.12)

            except RuntimeError as e:
                failures += 1
                try:
                    cb = main_callback2(b)
                    pc = b.read(ADDR["gPlayerPartyCount"], 1)[0]
                    print(
                        f"\n[attempt {attempt}] RECOVERY: {e} "
                        f"[callback=0x{cb:08X}, party_count={pc}]"
                    )
                except Exception:
                    print(f"\n[attempt {attempt}] RECOVERY: {e}")
                if failures >= 3:
                    b.fast(False)
                    print("Three consecutive failures. Safety stop.")
                    input("Press Enter to close...")
                    return
                time.sleep(0.5)

    except KeyboardInterrupt:
        try: b.fast(False)
        except Exception: pass
        print("\nStopped by user. Fast-forward disabled.")
        input("Press Enter to close...")

if __name__ == "__main__":
    main()
