#!/usr/bin/env python3
from pathlib import Path
import sys

SOUND_PATH=Path(__file__).resolve().parents[2]/"assets"/"sounds"/"shiny_found.wav"

def play_shiny_sound():
    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(str(SOUND_PATH), winsound.SND_FILENAME|winsound.SND_ASYNC)
            return True
    except Exception: pass
    return False
