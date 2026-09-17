#!/usr/bin/env python3
"""Verify that the hardware-passed starter implementation has not changed."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "STARTERS_FROZEN_v0p21_HF1.sha256"


def main():
    failures = []
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        expected, rel = raw.split(None, 1)
        rel = rel.strip()
        path = ROOT / rel
        if not path.exists():
            failures.append(f"MISSING {rel}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual.lower() != expected.lower():
            failures.append(f"CHANGED {rel}: {actual}")

    if failures:
        print("FROZEN STARTER CHECK: FAIL")
        for failure in failures:
            print(" -", failure)
        return 1

    print("FROZEN STARTER CHECK: PASS")
    print("v0p21 HF1 starter backend/UI/CIA are byte-for-byte unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
