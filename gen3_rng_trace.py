#!/usr/bin/env python3
"""Diagnostic-only Ruby/Sapphire RNG trace.

This module deliberately does not write gRngValue, RTC state, or any other
emulated RAM.  It samples the existing gRngValue address at fixed points in
the existing starter flow so repeated RTC seeds can be distinguished from
actual RNG reinitialisation.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import ruby_starter_hunter as rs


@dataclass
class RngTrace:
    reset: int
    rtc: str
    rtc_seed: str
    rng_after_reset: str
    rng_overworld: str
    rng_before_encounter: str
    rng_after_encounter: str
    pid: str
    iv_rng: str
    species: str
    shiny: bool
    ivs: dict
    starter_result: dict

    def format(self) -> str:
        return "\n".join(
            (
                f"RTC:       {self.rtc}",
                f"RTC seed:  {self.rtc_seed}",
                f"gRngValue immediately after seed: {self.rng_after_reset}",
                f"gRngValue at overworld:            {self.rng_overworld}",
                f"gRngValue before encounter:        {self.rng_before_encounter}",
                f"gRngValue after encounter:         {self.rng_after_encounter}",
                f"PID:       {self.pid}",
                f"IV RNG:    {self.iv_rng}",
            )
        )


def read_rng(b: rs.Bridge) -> int:
    raw = b.read(rs.ADDR["gRngValue"], 4)
    if len(raw) != 4:
        raise RuntimeError("short gRngValue read")
    return int.from_bytes(raw, "little")


def parse_status_fields(status: str) -> tuple[str, str]:
    """Use only values actually exposed by the connected bridge.

    Different CIA revisions may expose RTC/seed fields under different names.
    We never substitute the PC clock or invent a seed.
    """
    fields = {}
    for key, value in re.findall(r"([A-Za-z_]+)=([^\s]+)", status):
        fields[key.upper()] = value

    rtc = fields.get("RTC") or fields.get("TIME") or fields.get("RTC_TIME")
    seed = fields.get("RTC_SEED") or fields.get("SEED")
    return rtc or "unavailable", seed or "unavailable"


def iv_rng_from_result(result: dict) -> str:
    # generation_pre_rng is actual runtime telemetry recorded by the CIA.
    # Do not attempt to reconstruct a 32-bit RNG state from IVs.
    for key in ("iv_rng", "ivrng", "generation_pre_rng"):
        value = result.get(key)
        if value is not None:
            return f"{int(value) & 0xFFFFFFFF:08X}"
    return "unavailable"


def capture(b: rs.Bridge, reset_number: int, target: str, fast_forward: bool) -> RngTrace:
    b.fast(fast_forward)
    b.soft_reset()
    time.sleep(0.25)

    status = b.cmd("STATUS")
    rtc, status_seed = parse_status_fields(status)

    # Bridge-visible first sample after reset. This is intentionally named
    # rng_after_reset internally: without a firmware hook at the exact seed
    # assignment instruction we must not pretend this sample is cycle-exact.
    rng_after_reset = read_rng(b)

    st = rs.wait_for_loaded_route101(b)
    rng_overworld = read_rng(b)

    entry_state = rs.open_starter_bag(b, st, fast_forward=fast_forward)
    rng_before_encounter = read_rng(b)

    mon = rs.generate_starter(
        b,
        target,
        entry_state=entry_state,
        fast_forward=fast_forward,
    )
    rng_after_encounter = read_rng(b)

    result = rs.parse_starter_result(b.starter_result())
    # CIA STARTER_RESULT's seed is preferred when STATUS does not expose one.
    seed = status_seed
    if seed == "unavailable" and result.get("seed") is not None:
        seed = f"{int(result['seed']) & 0xFFFFFFFF:08X}"

    return RngTrace(
        reset=reset_number,
        rtc=rtc,
        rtc_seed=seed,
        rng_after_reset=f"{rng_after_reset:08X}",
        rng_overworld=f"{rng_overworld:08X}",
        rng_before_encounter=f"{rng_before_encounter:08X}",
        rng_after_encounter=f"{rng_after_encounter:08X}",
        pid=f"{mon['pid']:08X}",
        iv_rng=iv_rng_from_result(result),
        species=mon["species"],
        shiny=bool(mon["shiny"]),
        ivs=mon["ivs"],
        starter_result=result,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Ruby/Sapphire RNG diagnostic trace")
    p.add_argument("ip")
    p.add_argument("--attempts", type=int, default=20)
    p.add_argument("--target", choices=("TREECKO", "TORCHIC", "MUDKIP"), default="TORCHIC")
    p.add_argument("--fast", action="store_true")
    p.add_argument("--log", default="gen3_rng_trace.jsonl")
    args = p.parse_args()

    b = rs.Bridge(args.ip)
    log_path = Path(args.log)
    previous = None

    with log_path.open("a", encoding="utf-8") as log:
        for n in range(1, args.attempts + 1):
            trace = capture(b, n, args.target, args.fast)
            print(f"\n=== Reset {n} ===")
            print(trace.format())
            print(f"Species: {trace.species}  Shiny: {trace.shiny}  IVs: {trace.ivs}")

            if previous is not None and trace.rtc_seed == previous.rtc_seed:
                same_rng = trace.rng_after_reset == previous.rng_after_reset
                print(
                    "Same RTC seed as previous reset: "
                    f"gRngValue {'also repeated' if same_rng else 'changed'}"
                )
            previous = trace

            log.write(json.dumps(asdict(trace), sort_keys=True) + "\n")
            log.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
