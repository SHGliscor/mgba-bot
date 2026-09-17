#!/usr/bin/env python3
import json
import os
from pathlib import Path

from stats_store import appdata_dir

class RngHistoryStore:
    def __init__(self):
        self.path = appdata_dir() / "rng_history.json"
        self.values = self._load()

    def _load(self):
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("values", [])
            return {int(v) & 0xFFFFFFFF for v in data}
        except Exception:
            return set()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = {
            "schema_version": 1,
            "count": len(self.values),
            "values": sorted(self.values),
        }
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def __contains__(self, value):
        return (int(value) & 0xFFFFFFFF) in self.values

    def add(self, value):
        self.values.add(int(value) & 0xFFFFFFFF)
        self._save()

    def reset(self):
        self.values.clear()
        self._save()

    def __len__(self):
        return len(self.values)
