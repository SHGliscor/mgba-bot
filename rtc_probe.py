#!/usr/bin/env python3
import sys
import time

from ruby_starter_hunter import Bridge, ADDR, player_state, main_callback2
from rtc_tools import read_rtc_snapshot, rtc_changed, same_rtc_minute

DEFAULT_IP = "192.168.1.21"
PORT = 4953

def party_count(b):
    return b.read(ADDR["gPlayerPartyCount"], 1)[0]

def show(label, s):
    print(f"\n--- {label} ---")
    print(f"RTC mode:       {s['mode']}")
    print(f"RTC value:      {s['timestamp']}")
    print(f"Ruby seed:      0x{s['seed']:04X}")
    print(f"RTC status:     0x{s['status']:02X}")
    print(f"Error status:   0x{s['error_status']:04X}")
    print(f"Probe result:   0x{s['probe_result']:02X}")
    print(f"Live gRngValue: 0x{s['rng']:08X}")
    print(f"Raw sRtc:       {s['raw_hex']}")

def signature(b):
    st = player_state(b)
    return {
        "map_group": st["map_group"],
        "map_num": st["map_num"],
        "x": st.get("x"),
        "y": st.get("y"),
        "facing": st.get("facing"),
        "callback": main_callback2(b),
        "party_count": party_count(b),
    }

def sig_text(s):
    return (
        f"map={s['map_group']}/{s['map_num']} "
        f"coords=({s['x']},{s['y']}) facing={s['facing']} "
        f"callback=0x{s['callback']:08X} party={s['party_count']}"
    )

def wait_until_left_start_state(b, start, timeout=5.0):
    # SaveBlock map bytes can stay stale through reset, so they are NOT enough.
    # Require the live callback or coordinates to leave the original state.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            now = signature(b)
            if (
                now["callback"] != start["callback"]
                or now["x"] != start["x"]
                or now["y"] != start["y"]
            ):
                return now
        except Exception:
            # A transient unreadable period during reset also proves departure.
            return None
        time.sleep(0.015)
    raise RuntimeError(
        "Soft reset was sent, but the game never left the original loaded-save state."
    )

def wait_for_exact_saved_position(b, start, timeout=25.0):
    # Advance boot/title/Continue with A, but only PASS after the exact saved
    # overworld signature returns.
    deadline = time.monotonic() + timeout
    last_a = 0.0
    last = None

    while time.monotonic() < deadline:
        try:
            now = signature(b)
            last = now
            if (
                now["map_group"] == start["map_group"]
                and now["map_num"] == start["map_num"]
                and now["x"] == start["x"]
                and now["y"] == start["y"]
                and now["callback"] == start["callback"]
                and now["party_count"] == start["party_count"]
            ):
                return now
        except Exception:
            pass

        t = time.monotonic()
        if t - last_a >= 0.18:
            try:
                b.key("A", 70)
            except Exception:
                pass
            last_a = t
        time.sleep(0.035)

    detail = sig_text(last) if last else "no readable final state"
    raise RuntimeError(
        "Timed out before the exact saved Birch-bag position returned. "
        f"Last state: {detail}"
    )

def reset_and_sample(b, start, label):
    print(f"\nSending soft reset for {label}...")
    b.soft_reset()

    departed = wait_until_left_start_state(b, start)
    if departed:
        print("Reset departure confirmed:", sig_text(departed))
    else:
        print("Reset departure confirmed during transient reset state.")

    # Let Ruby execute startup/RTC seeding before sampling the cached sRtc.
    time.sleep(0.20)
    snap = read_rtc_snapshot(b)
    show(label, snap)

    returned = wait_for_exact_saved_position(b, start)
    print("Exact saved position restored:", sig_text(returned))
    return snap

def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IP

    print("============================================================")
    print(" Pokebot3DS-CFW mGBA Ruby RTC Probe v0p2")
    print("============================================================")
    print(f"Target: {ip}:{PORT}")
    print("No CIA reinstall required.\n")

    b = Bridge(ip)
    print(b.cmd("PING"))
    game = b.cmd("GAME")
    print(game)

    if " AXVE " not in game:
        raise SystemExit("ERROR: English Pokemon Ruby (AXVE) is required.")

    rev = b.read(ADDR["revision"], 1)[0]
    print(f"ROM revision byte: {rev:02X}")
    if rev != 1:
        raise SystemExit("ERROR: Ruby v1.1 / revision 01 is required.")

    b.fast(False)

    start = signature(b)
    print("\nStarting loaded-save signature:")
    print(sig_text(start))

    if (start["map_group"], start["map_num"]) != (0, 16):
        raise SystemExit("ERROR: Start from the saved Route 101 Birch-bag position.")
    if start["x"] is None or start["y"] is None:
        raise SystemExit("ERROR: Could not resolve the loaded player coordinates.")

    current = read_rtc_snapshot(b)
    show("CURRENT RUBY RTC CACHE", current)

    first = reset_and_sample(b, start, "RESET SAMPLE 1")
    time.sleep(3.0)
    second = reset_and_sample(b, start, "RESET SAMPLE 2")

    print("\n============================================================")
    print(" RESULT")
    print("============================================================")

    if first["dead_seed"] and second["dead_seed"]:
        print("RTC RESULT: FAIL — DEAD/FIXED RTC")
        print("Ruby is receiving dummy 2000-01-01 00:00:00 / seed 0x05A0.")
    elif rtc_changed(first, second):
        print("RTC RESULT: PASS — LIVE RTC")
        if same_rtc_minute(first, second):
            print(
                f"Both resets were in the same RTC minute, so the initial Ruby seed "
                f"correctly remained 0x{first['seed']:04X}."
            )
            print(
                "Retail Ruby changes this initial seed when the RTC minute changes, "
                "not on every reset."
            )
        else:
            print(
                f"RTC minute changed, and the Ruby seed changed "
                f"0x{first['seed']:04X} -> 0x{second['seed']:04X}."
            )
    else:
        print("RTC RESULT: FAIL/INCONCLUSIVE — RTC did not advance.")

    print("\nRESET RETURN RESULT: PASS")
    print(
        "Both reset samples left the old overworld state and returned to the "
        "exact original saved position before being accepted."
    )
    print("\nSend this complete output back to me.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProbe stopped.")
    except Exception as e:
        print(f"\nERROR: {e}")
    input("\nPress Enter to close...")
