#!/usr/bin/env python3
from gen3bot.data.sprite_assets import sync_all, asset_counts, pack_complete, EXPECTED_PER_VARIANT


def progress(done, total, name, variant):
    if done == 1 or done == total or done % 50 == 0:
        print(f"[{done}/{total}] {variant}/{name}")


if __name__ == "__main__":
    try:
        done, total = sync_all(progress)
    except Exception as exc:
        print(f"Sprite sync failed: {exc}")
        raise SystemExit(1)
    counts = asset_counts()
    print(f"Sprite sync checked {done}/{total} upstream assets.")
    for variant, count in counts.items():
        print(f"  {variant}: {count}/{EXPECTED_PER_VARIANT}")
    if pack_complete():
        print("Complete 40Cakes sprite tree is present.")
        raise SystemExit(0)
    print("Sprite tree is incomplete. Check internet access and run this utility again.")
    raise SystemExit(2)
