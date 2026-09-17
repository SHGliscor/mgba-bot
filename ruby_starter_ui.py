#!/usr/bin/env python3
import sys
import time
import math
import secrets
import threading
from pathlib import Path
from datetime import datetime
from collections import deque

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize, QRectF
from PySide6.QtGui import QFont, QColor, QPixmap, QIcon, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QGroupBox, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFrame, QTabWidget, QLayout, QSizePolicy
)

from stats_store import StatsStore
from rtc_tools import read_rtc_snapshot, G_RNG_VALUE
from rng_history import RngHistoryStore
from support_export import export_support_bundle

from ruby_starter_hunter import (
    Bridge, ADDR, TARGET_IDS, decode_mon, player_state,
    wait_for_loaded_route101, open_starter_bag, generate_starter,
    main_callback2, parse_starter_result, detect_supported_game,
)

APP_TITLE = "Pokebot3DS-CFW — Gen 3 Starter Hunter v0p21 HF1 Current UI — Ruby/Sapphire v1.0/v1.1"
DEFAULT_IP = "192.168.1.21"
PORT = 4953
ODDS = 8192

LCG_A = 0x41C64E6D
LCG_C = 0x6073
GEN_TRACE_MAX_CALLS = 250000

def parse_key_value_reply(reply):
    """Parse space-delimited KEY=VALUE fields from PB3 bridge replies."""
    out = {}
    for token in str(reply).split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.lower()
        try:
            if key in ("gen", "rng", "seed"):
                out[key] = int(value, 16)
            else:
                out[key] = int(value, 0)
        except ValueError:
            out[key] = value
    return out

def trace_generation_from_seed(seed, mon, confirm_rng=None, max_calls=GEN_TRACE_MAX_CALLS):
    """Reconstruct the exact Ruby/Sapphire Random() call that produced PID+IVs.

    Ruby/Sapphire's CreateBoxMon uses Random32() for the personality (two Random calls),
    followed by two Random calls for the six IVs when fixedIV == 32. Matching
    PID plus both packed IV words therefore identifies the real generation point
    far more reliably than the pre-confirm gRngValue sample.
    """
    seed = int(seed) & 0xFFFF
    target_pid = int(mon["pid"]) & 0xFFFFFFFF
    iv = mon["ivs"]
    target_iv1 = (int(iv["hp"]) | (int(iv["atk"]) << 5) | (int(iv["def"]) << 10)) & 0x7FFF
    target_iv2 = (int(iv["spe"]) | (int(iv["spa"]) << 5) | (int(iv["spd"]) << 10)) & 0x7FFF
    target_confirm = None if confirm_rng is None else (int(confirm_rng) & 0xFFFFFFFF)

    state = seed
    confirm_index = 0 if target_confirm == state else None
    window = deque(maxlen=4)

    for call_index in range(1, int(max_calls) + 1):
        pre_state = state
        state = (LCG_A * state + LCG_C) & 0xFFFFFFFF
        out = (state >> 16) & 0xFFFF
        if confirm_index is None and target_confirm is not None and state == target_confirm:
            confirm_index = call_index
        window.append((call_index, pre_state, state, out))
        if len(window) < 4:
            continue

        a, b, c, d = window
        pid = a[3] | (b[3] << 16)
        if pid != target_pid:
            continue
        if (c[3] & 0x7FFF) != target_iv1 or (d[3] & 0x7FFF) != target_iv2:
            continue

        pid_call_index = a[0]
        calls_from_confirm = (
            pid_call_index - confirm_index
            if confirm_index is not None else None
        )
        return {
            "pid_call_index": pid_call_index,
            "generation_pre_rng": a[1],
            "generation_pid_low_state": a[2],
            "generation_pid_high_state": b[2],
            "confirm_call_index": confirm_index,
            "confirm_to_pid_calls": calls_from_confirm,
            "trace_found": True,
        }

    return {
        "pid_call_index": None,
        "generation_pre_rng": None,
        "generation_pid_low_state": None,
        "generation_pid_high_state": None,
        "confirm_call_index": confirm_index,
        "confirm_to_pid_calls": None,
        "trace_found": False,
    }

# Ruby/Sapphire v1.0/v1.1 share these battle RAM symbols; used only for optional battle display.
G_BATTLE_TYPE_FLAGS = 0x020239F8
G_ENEMY_PARTY = 0x030045C0

STARTERS = ["Treecko", "Torchic", "Mudkip"]
STARTER_KEYS = {
    "Treecko": "TREECKO",
    "Torchic": "TORCHIC",
    "Mudkip": "MUDKIP",
}


POKEMON_THEME_QSS = r"""
QWidget#appRoot {
    background: #0d141c;
    color: #eaf0f6;
    font-family: "Segoe UI";
    font-size: 10pt;
}

QFrame#appHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #17212c, stop:0.55 #1b2734, stop:1 #242f3b);
    border: 1px solid #334354;
    border-radius: 15px;
}

QLabel#appTitle {
    color: #ffffff;
    font-size: 21px;
    font-weight: 800;
}

QLabel#appSubtitle {
    color: #91a3b5;
    font-size: 10px;
    font-weight: 600;
}

QLabel#appSectionTitle {
    color: #f5f8fb;
    font-size: 20px;
    font-weight: 800;
}

QLabel#sectionNote {
    color: #8fa0b0;
    font-size: 10px;
}

QLabel#smallChip {
    background: #202c39;
    color: #b9c8d6;
    border: 1px solid #34475a;
    border-radius: 9px;
    padding: 3px 8px;
    font-size: 9px;
    font-weight: 700;
}

QLabel#connectionBadge {
    border-radius: 10px;
    padding: 6px 11px;
    font-weight: 800;
    border: 1px solid #536273;
    background: #2a3440;
    color: #d9e3ec;
}
QLabel#connectionBadge[state="connected"] {
    background: #153c2b;
    border: 1px solid #2e8d62;
    color: #8de0b9;
}
QLabel#connectionBadge[state="connecting"] {
    background: #3a3117;
    border: 1px solid #9d8130;
    color: #f0cf6a;
}
QLabel#connectionBadge[state="stopped"] {
    background: #243243;
    border: 1px solid #44627e;
    color: #aac7e2;
}
QLabel#connectionBadge[state="error"] {
    background: #481f23;
    border: 1px solid #9c4248;
    color: #ffaaa7;
}

QTabWidget::pane {
    background: #101821;
    border: 1px solid #2a3949;
    border-radius: 12px;
    top: -1px;
}

QTabBar::tab {
    background: #141e28;
    color: #8fa0b0;
    border: 1px solid #2b3948;
    border-bottom: 0;
    padding: 9px 17px;
    margin-right: 4px;
    min-width: 94px;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    font-weight: 700;
}
QTabBar::tab:hover {
    background: #1d2a37;
    color: #dce6ee;
}
QTabBar::tab:selected {
    background: #c73b35;
    color: #ffffff;
    border-color: #e2534c;
    border-bottom: 3px solid #f1c84b;
}

QTabWidget#dashboardLogTabs::pane {
    background: #121b24;
    border: 1px solid #2a3949;
    border-radius: 12px;
    top: -1px;
}
QTabWidget#dashboardLogTabs QTabBar::tab {
    min-width: 120px;
    padding: 7px 14px;
    margin-right: 3px;
}
QTabWidget#dashboardLogTabs QTabBar::tab:selected {
    background: #68447c;
    border-color: #8c62a0;
    border-bottom: 3px solid #f1c84b;
}

QGroupBox {
    background: #121b24;
    border: 1px solid #2a3949;
    border-radius: 12px;
    margin-top: 14px;
    padding: 12px 8px 8px 8px;
    font-weight: 800;
    color: #dfe8f0;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 7px;
    color: #f1c84b;
    background: #121b24;
}

QFrame#overlayPanel {
    background: #121b24;
    border: 1px solid #2a3949;
    border-radius: 14px;
}

QLabel#overlayHeader {
    background: #1e2a36;
    color: #eaf1f6;
    border: 0;
    border-bottom: 1px solid #2f4051;
    border-top-left-radius: 13px;
    border-top-right-radius: 13px;
    padding: 7px 11px;
    font-size: 11px;
    font-weight: 800;
}
QLabel#overlayHeader[accent="red"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b93632, stop:1 #7d2928);
    border-bottom: 1px solid #d34b46;
}
QLabel#overlayHeader[accent="blue"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #245e8f, stop:1 #1e4466);
    border-bottom: 1px solid #3c7fb5;
}
QLabel#overlayHeader[accent="gold"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #80671d, stop:1 #5b4a1e);
    border-bottom: 1px solid #a88c31;
    color: #fff2bd;
}
QLabel#overlayHeader[accent="teal"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #23685f, stop:1 #194840);
    border-bottom: 1px solid #33897e;
}
QLabel#overlayHeader[accent="violet"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #68447c, stop:1 #483055);
    border-bottom: 1px solid #8c62a0;
}

QLabel#overlaySubtle {
    color: #91a2b2;
    font-size: 10px;
}
QLabel#overlaySpecies {
    color: #ffffff;
    font-size: 28px;
    font-weight: 900;
}
QLabel#overlayColumnHead {
    color: #8294a5;
    font-size: 9px;
    font-weight: 700;
    padding-bottom: 2px;
}
QLabel#overlayColumnValue {
    color: #edf3f7;
    font-size: 13px;
    font-weight: 800;
}
QFrame#overlayStatRow {
    background: transparent;
    border-top: 1px solid #243342;
}
QLabel#overlayStatIcon {
    color: #f1c84b;
    font-size: 12px;
}
QLabel#overlayStatLabel {
    color: #8fa0b0;
    font-size: 10px;
}
QLabel#overlayStatValue {
    color: #f2f6fa;
    font-size: 15px;
    font-weight: 800;
}

QFrame#statTile {
    background: #17222d;
    border: 1px solid #2a3a49;
    border-radius: 10px;
}
QLabel#statTileTitle {
    color: #8fa0b0;
    font-size: 10px;
    font-weight: 600;
}
QLabel#statTileValue {
    color: #f4f8fb;
    font-size: 19px;
    font-weight: 800;
}

QLabel#spriteCard {
    background: #0d151d;
    border: 1px solid #314050;
    border-radius: 12px;
}

QLineEdit, QComboBox {
    background: #0f1821;
    color: #edf3f7;
    border: 1px solid #334556;
    border-radius: 7px;
    padding: 6px 8px;
    min-height: 20px;
    selection-background-color: #b83a35;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #d2554f;
}
QComboBox QAbstractItemView {
    background: #15202a;
    color: #eaf0f6;
    border: 1px solid #344758;
    selection-background-color: #a53330;
    selection-color: white;
}

QCheckBox {
    color: #c9d4de;
    spacing: 7px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #41566a;
    border-radius: 4px;
    background: #0e171f;
}
QCheckBox::indicator:checked {
    background: #c73b35;
    border: 1px solid #ee6b64;
}

QPushButton {
    background: #263544;
    color: #eaf0f6;
    border: 1px solid #3b4e60;
    border-radius: 8px;
    padding: 7px 12px;
    font-weight: 700;
}
QPushButton:hover {
    background: #314355;
    border-color: #526b82;
}
QPushButton:pressed {
    background: #1f2c38;
}
QPushButton:disabled {
    color: #627181;
    background: #18222c;
    border-color: #263441;
}
QPushButton#startButton {
    background: #257554;
    border-color: #369a72;
    color: #f1fff8;
}
QPushButton#startButton:hover { background: #2d8b64; }
QPushButton#stopButton {
    background: #8c302f;
    border-color: #b84b48;
    color: #fff2f1;
}
QPushButton#stopButton:hover { background: #a13a38; }
QPushButton#supportButton {
    background: #285b83;
    border-color: #3a78aa;
    color: #eef8ff;
}
QPushButton#supportButton:hover { background: #326f9e; }
QPushButton#dangerButton {
    background: #492528;
    border-color: #774044;
    color: #ffcfcc;
}
QPushButton#dangerButton:hover { background: #5b2d31; }

QProgressBar {
    background: #0d161e;
    color: #f1f4f7;
    border: 1px solid #324354;
    border-radius: 7px;
    text-align: center;
    min-height: 19px;
    font-weight: 700;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b83732, stop:0.75 #d94b43, stop:1 #f1c84b);
    border-radius: 6px;
}

QTableWidget {
    background: #0e171f;
    alternate-background-color: #131e28;
    color: #e8eff4;
    border: 1px solid #293949;
    border-radius: 9px;
    gridline-color: #243340;
    selection-background-color: #304c65;
    selection-color: white;
    outline: 0;
}
QTableWidget::item {
    padding: 4px 5px;
}
QHeaderView::section {
    background: #1c2935;
    color: #aebdca;
    border: 0;
    border-right: 1px solid #2c3b49;
    border-bottom: 1px solid #354657;
    padding: 6px 5px;
    font-size: 9px;
    font-weight: 800;
}
QTableCornerButton::section {
    background: #1c2935;
    border: 0;
}

QScrollBar:vertical {
    background: #0f1820;
    width: 11px;
    margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #35495c;
    min-height: 28px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover { background: #486077; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #0f1820;
    height: 11px;
    margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #35495c;
    min-width: 28px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover { background: #486077; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QFrame#footerBar {
    background: #111b24;
    border: 1px solid #253544;
    border-radius: 9px;
}
QLabel#footerStatus { color: #a9b7c4; font-size: 9px; }
QLabel#footerGame { color: #7f93a4; font-size: 9px; }

QToolTip {
    background: #18232e;
    color: #f1f5f8;
    border: 1px solid #3c5063;
    padding: 4px;
}
"""


class PokeBallMark(QWidget):
    def __init__(self, size=42, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self.setToolTip("Pokebot3DS-CFW")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pad = 2.5
        rect = QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2)
        painter.setPen(QPen(QColor("#0a0f14"), 2.4))
        painter.setBrush(QBrush(QColor("#d6423a")))
        painter.drawEllipse(rect)
        painter.setClipRect(QRectF(0, self.height() / 2, self.width(), self.height() / 2))
        painter.setBrush(QBrush(QColor("#f2f4f6")))
        painter.drawEllipse(rect)
        painter.setClipping(False)
        y = self.height() / 2
        painter.setPen(QPen(QColor("#0a0f14"), 4.0))
        painter.drawLine(int(pad + 1), int(y), int(self.width() - pad - 1), int(y))
        center = self.width() / 2
        painter.setPen(QPen(QColor("#0a0f14"), 2.4))
        painter.setBrush(QBrush(QColor("#f6f8f9")))
        painter.drawEllipse(QRectF(center - 6.2, y - 6.2, 12.4, 12.4))
        painter.setPen(QPen(QColor("#778696"), 1.2))
        painter.setBrush(QBrush(QColor("#dfe6eb")))
        painter.drawEllipse(QRectF(center - 2.8, y - 2.8, 5.6, 5.6))

def fmt_elapsed(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def fmt_eta(hours):
    if not math.isfinite(hours):
        return "—"
    if hours <= 0:
        return "Reached"
    total = int(hours * 3600)
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m"

class HuntWorker(QThread):
    connected = Signal(dict)
    encounter = Signal(dict)
    stats = Signal(dict)
    rtc = Signal(dict)
    rng = Signal(dict)
    log = Signal(str)
    stopped = Signal(str)
    error = Signal(str)

    def __init__(self, ip, starter_mode, fast_forward, battle_mode, unique_rng=True):
        super().__init__()
        self.ip = ip
        self.starter_mode = starter_mode
        self.fast_forward = fast_forward
        self.battle_mode = battle_mode
        self.unique_rng = unique_rng
        self._stop_requested = threading.Event()
        self.bridge = None
        self.game_profile = None

        self.attempts = 0
        self.successful = 0
        self.unique_pids = set()
        self.duplicates = 0
        self.shinies = 0
        self.started_at = None
        self.consecutive_failures = 0
        self.rng_history = RngHistoryStore()
        self.rng_repeats_avoided = 0
        self.seed_counts = {}
        self.last_rtc = None
        self.last_confirm_rng = None
        self.last_jitter_ms = 0
        self.last_delay_frames = 0
        self.cia_rng_gate = False
        self.cia_gate_used = 0
        self.cia_gate_blocked = 0
        self.cia_seed_arms = 0
        self.cia_seed_changes = 0
        self.cia_blocked_total = 0
        self.cia_exact_blocked = 0
        self.cia_exact_blocked_total = 0
        self.cia_generation_used = 0
        self.cia_generation_blocked = 0
        self.cia_generation_blocked_total = 0
        self.cia_predicted_pid_calls = 0
        self.cia_window_min = 48
        self.cia_window_max = 72
        self.cia_capacity = 0
        self.trace_successes = 0
        self.trace_failures = 0
        self.effective_rolls = 0
        self.duplicate_fingerprints = set()
        self.recovery_log = deque(maxlen=25)
        # Random mode uses shuffled groups of all three starters. This keeps
        # the order random while guaranteeing Random cannot accidentally look
        # like a fixed-starter mode over a short test.
        self._random_starter_bag = []

    def request_stop(self):
        self._stop_requested.set()

    def should_stop(self):
        return self._stop_requested.is_set()

    def safe_fast_off(self):
        if self.bridge is not None:
            try:
                self.bridge.fast(False)
            except Exception:
                pass

    def choose_starter(self):
        if self.starter_mode == "Random":
            if not self._random_starter_bag:
                self._random_starter_bag = list(STARTERS)
                secrets.SystemRandom().shuffle(self._random_starter_bag)
            chosen = self._random_starter_bag.pop()
            self.log.emit(
                f"Random starter target for attempt {self.attempts}: {chosen}."
            )
            return chosen
        return self.starter_mode

    def emit_stats(self):
        elapsed = max(0.001, time.monotonic() - self.started_at)
        rate = self.successful / elapsed * 3600.0
        unique_rate = len(self.unique_pids) / elapsed * 3600.0
        effective_rate = self.effective_rolls / elapsed * 3600.0
        cumulative = (1.0 - ((ODDS - 1) / ODDS) ** self.effective_rolls) * 100.0
        eta_hours = (
            max(0, ODDS - self.effective_rolls) / effective_rate
            if effective_rate > 0 else math.inf
        )
        self.stats.emit({
            "attempts": self.attempts,
            "encounters": self.successful,
            "unique": len(self.unique_pids),
            "duplicates": self.duplicates,
            "shinies": self.shinies,
            "rate": rate,
            "unique_rate": unique_rate,
            "effective_rate": effective_rate,
            "elapsed": elapsed,
            "cumulative": cumulative,
            "eta_hours": eta_hours,
            "odds_progress": min(100.0, self.effective_rolls / ODDS * 100.0),
            "rng_repeats_avoided": self.rng_repeats_avoided,
            "rng_history_size": len(self.rng_history),
            "effective_rolls": self.effective_rolls,
        })

    def wait_for_unique_rng_value(self):
        """Retail-safe RSE anti-repeat gate.

        Mirrors Pokebot Gen3's persistent gRngValue history, but remains
        strictly read-only. A small timing nudge is applied first so mGBA
        fast-forward does not keep presenting the confirmation input on the
        same deterministic emulation cadence.
        """
        # 0-120 ms real-time variation costs very little throughput but,
        # under fast-forward, changes many emulated-frame opportunities.
        jitter_ms = secrets.randbelow(121)
        self.last_jitter_ms = jitter_ms
        if jitter_ms:
            deadline = time.monotonic() + jitter_ms / 1000.0
            while time.monotonic() < deadline:
                if self.should_stop():
                    raise RuntimeError(
                        "Stopped during RNG timing diversification."
                    )
                time.sleep(0.003)

        blocked_values = 0
        started = time.monotonic()
        last_seen = None

        while not self.should_stop():
            rng_value = int.from_bytes(
                self.bridge.read(G_RNG_VALUE, 4), "little"
            )
            last_seen = rng_value

            if rng_value not in self.rng_history:
                # Reserve before confirmation so another session/attempt cannot
                # intentionally reuse the same decision-state.
                self.rng_history.add(rng_value)
                waited = blocked_values > 0
                if waited:
                    self.rng_repeats_avoided += 1

                self.last_confirm_rng = rng_value
                self.rng.emit({
                    "value": rng_value,
                    "waited": waited,
                    "blocked_values": blocked_values,
                    "wait_seconds": time.monotonic() - started,
                    "history_size": len(self.rng_history),
                    "repeats_avoided": self.rng_repeats_avoided,
                    "jitter_ms": jitter_ms,
                })
                return

            blocked_values += 1
            if blocked_values == 1:
                self.log.emit(
                    f"RNG state 0x{rng_value:08X} was already used; "
                    "waiting for a unique generation frame..."
                )

            # Let Ruby/mGBA advance naturally. No RAM write is ever used.
            time.sleep(0.004 if self.fast_forward else 0.012)

        raise RuntimeError(
            "Stopped while waiting for a unique RNG value "
            f"(last=0x{(last_seen or 0):08X})."
        )

    def confirm_unique_in_cia(self, target_name):
        """Frame-local generation-aware starter confirmation.

        The CIA still requires an unused exact confirmation gRngValue, but it
        no longer rejects a broad ±LCG neighbourhood. Instead, the PC records
        the actual generation_pre_rng reconstructed from each finished PK3.
        Before injecting A, the CIA predicts every generation_pre_rng reachable
        in the guarded Confirm→PID window and rejects only candidates that would
        reproduce a generation state already observed this hunt.
        """
        if not self.cia_rng_gate:
            return self.wait_for_unique_rng_value()

        if self.last_rtc is None:
            raise RuntimeError("CIA RNG gate has no RTC seed for this reset.")

        seed = int(self.last_rtc["seed"]) & 0xFFFF
        reset_no = max(1, int(self.last_rtc.get("resets_on_seed", 1)))

        # Preserve the v0p11 timing spread that tested well on hardware.
        delay_frames = 3 + ((reset_no - 1) * 7) % 53
        self.last_delay_frames = delay_frames
        self.last_jitter_ms = 0

        self.bridge.starter_arm(seed, delay_frames)
        self.log.emit(
            f"CIA generation-aware RNG gate armed: seed 0x{seed:04X}, "
            f"delay {delay_frames} emulated frames, "
            f"PID-call window {self.cia_window_min}–{self.cia_window_max}."
        )

        deadline = time.monotonic() + (4.0 if self.fast_forward else 8.0)
        last = None
        while time.monotonic() < deadline and not self.should_stop():
            result = parse_starter_result(self.bridge.starter_result())
            last = result
            state = result.get("state")
            if state == "READY":
                rng_value = int(result["rng"]) & 0xFFFFFFFF
                self.last_confirm_rng = rng_value
                self.cia_gate_used = int(result.get("session_used", result.get("used", 0)))
                self.cia_generation_used = int(result.get("gen_used", self.cia_generation_used))
                self.cia_gate_blocked = int(result.get("blocked", 0))
                self.cia_seed_arms = int(result.get("seed_arms", reset_no))
                self.cia_seed_changes = int(result.get("seed_changes", 0))
                self.cia_blocked_total = int(result.get("blocked_total", self.cia_gate_blocked))
                self.cia_exact_blocked = int(result.get("exact", 0))
                self.cia_exact_blocked_total = int(result.get("exact_total", 0))
                self.cia_generation_blocked = int(result.get("gen_blocked", result.get("near", 0)))
                self.cia_generation_blocked_total = int(
                    result.get("gen_blocked_total", result.get("near_total", 0))
                )
                self.cia_predicted_pid_calls = int(
                    result.get("pred_calls", result.get("last_dist", 0))
                )
                self.cia_window_min = int(result.get("win_min", self.cia_window_min))
                self.cia_window_max = int(result.get("win_max", self.cia_window_max))
                self.cia_capacity = int(result.get("capacity", 32768))

                already_pc = rng_value in self.rng_history
                self.rng_history.add(rng_value)
                if self.cia_gate_blocked > 0:
                    self.rng_repeats_avoided += 1

                self.rng.emit({
                    "value": rng_value,
                    "waited": self.cia_gate_blocked > 0,
                    "blocked_values": self.cia_gate_blocked,
                    "blocked_total": self.cia_blocked_total,
                    "wait_seconds": 0.0,
                    "history_size": len(self.rng_history),
                    "repeats_avoided": self.rng_repeats_avoided,
                    "jitter_ms": 0,
                    "delay_frames": delay_frames,
                    "cia_gate": True,
                    "cia_session_used": self.cia_gate_used,
                    "cia_generation_used": self.cia_generation_used,
                    "cia_seed_arms": self.cia_seed_arms,
                    "cia_seed_changes": self.cia_seed_changes,
                    "cia_capacity": self.cia_capacity,
                    "cia_exact_blocked": self.cia_exact_blocked,
                    "cia_exact_blocked_total": self.cia_exact_blocked_total,
                    "cia_generation_blocked": self.cia_generation_blocked,
                    "cia_generation_blocked_total": self.cia_generation_blocked_total,
                    "cia_predicted_pid_calls": self.cia_predicted_pid_calls,
                    "cia_window_min": self.cia_window_min,
                    "cia_window_max": self.cia_window_max,
                    "cia_frames": int(result.get("frames", 0)),
                    "pc_history_repeat": already_pc,
                })
                return rng_value

            if state not in ("PENDING", "IDLE"):
                raise RuntimeError(f"Unexpected CIA RNG gate state: {result}")
            time.sleep(0.008 if self.fast_forward else 0.016)

        try:
            self.bridge.starter_cancel()
        except Exception:
            pass
        raise RuntimeError(
            "CIA generation-aware RNG gate timed out before a safe generation "
            f"state (last={last})."
        )

    def wait_for_battle_view(self):
        # "View battle": slow emulator down once the PK3 has already been read,
        # let the Poochyena/battle state become visible, then pause briefly.
        self.bridge.fast(False)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not self.should_stop():
            try:
                flags = int.from_bytes(self.bridge.read(G_BATTLE_TYPE_FLAGS, 4), "little")
                enemy = decode_mon(self.bridge.read(G_ENEMY_PARTY, 100))
                if flags != 0 and enemy is not None:
                    time.sleep(1.25)
                    break
            except Exception:
                pass
            time.sleep(0.03)

        if not self.should_stop() and self.fast_forward:
            self.bridge.fast(True)

    def run(self):
        try:
            self.bridge = Bridge(self.ip)
            pong = self.bridge.cmd("PING")
            profile = detect_supported_game(self.bridge)
            self.game_profile = profile
            game = profile["game_reply"]
            rev = profile["revision"]

            st = player_state(self.bridge)
            if (st["map_group"], st["map_num"]) != (0, 16):
                raise RuntimeError(
                    f"{profile['short_name']} is loaded on map "
                    f"{st['map_group']}/{st['map_num']}, not Route 101 (0/16)."
                )

            self.bridge.fast(self.fast_forward)
            time.sleep(0.15)
            status = self.bridge.cmd("STATUS")
            if self.fast_forward and "FAST=1" not in status:
                raise RuntimeError(
                    "Fast-forward did not stay enabled. Install the FastOverride CIA or disable fast-forward."
                )

            # v0p11 CIA capability probe. Older CIAs remain usable through the
            # PC-side read-only compatibility gate, but do not get frame-local
            # confirmation.
            try:
                gate_probe = parse_starter_result(self.bridge.starter_result())
                self.cia_rng_gate = gate_probe.get("state") in ("IDLE", "PENDING", "READY")
                if self.cia_rng_gate:
                    # CIA history is intentionally session-wide. Clear it once
                    # when this hunt starts, never on RTC minute/seed changes.
                    self.bridge.starter_clear()
            except Exception:
                self.cia_rng_gate = False

            self.log.emit(
                "RNG confirmation: "
                + ("CIA generation-aware frame-local gate" if self.cia_rng_gate
                   else "PC compatibility gate (install v0p21 CIA for best results)")
            )

            self.connected.emit({
                "pong": pong,
                "game": game,
                "game_name": profile["name"],
                "short_name": profile["short_name"],
                "game_code": profile["code"],
                "game_version": profile["version"],
                "revision": rev,
                "status": status,
                "map_group": st["map_group"],
                "map_num": st["map_num"],
                "x": st.get("x"),
                "y": st.get("y"),
                "facing": st.get("facing"),
                "cia_rng_gate": self.cia_rng_gate,
            })

            self.started_at = time.monotonic()
            self.log.emit("Hunt started.")

            while not self.should_stop():
                self.attempts += 1
                self.last_confirm_rng = None
                self.last_jitter_ms = 0
                self.last_delay_frames = 0
                chosen_name = self.choose_starter()
                chosen_key = STARTER_KEYS[chosen_name]
                target_id = TARGET_IDS[chosen_key]

                try:
                    self.last_confirm_rng = None
                    self.last_jitter_ms = 0
                    self.last_delay_frames = 0
                    self.bridge.fast(self.fast_forward)
                    self.bridge.soft_reset()
                    time.sleep(0.25)

                    # sRtc is refreshed by Ruby/Sapphire's startup SeedRngWithRtc path.
                    rtc = read_rtc_snapshot(self.bridge)
                    seed = rtc["seed"]
                    self.seed_counts[seed] = self.seed_counts.get(seed, 0) + 1
                    rtc["resets_on_seed"] = self.seed_counts[seed]
                    rtc["attempt"] = self.attempts
                    self.last_rtc = rtc
                    self.rtc.emit(dict(rtc))

                    st = wait_for_loaded_route101(self.bridge)
                    if self.should_stop():
                        break

                    party_count = self.bridge.read(ADDR["gPlayerPartyCount"], 1)[0]
                    if party_count != 0:
                        raise RuntimeError(f"Expected empty pre-starter party, got party count {party_count}.")

                    bag_state = open_starter_bag(
                        self.bridge, st,
                        fast_forward=self.fast_forward,
                        precise_input=self.cia_rng_gate,
                    )
                    mon = generate_starter(
                        self.bridge,
                        chosen_key,
                        entry_state=bag_state,
                        fast_forward=self.fast_forward,
                        before_confirm=(
                            self.wait_for_unique_rng_value
                            if self.unique_rng and not self.cia_rng_gate else None
                        ),
                        local_confirm=(
                            self.confirm_unique_in_cia
                            if self.unique_rng and self.cia_rng_gate else None
                        ),
                        precise_input=self.cia_rng_gate,
                    )

                    if mon.get("_rng_guard_bypassed"):
                        self.log.emit(
                            f"Attempt {self.attempts}: valid starter PK3 was generated "
                            "before the RNG guard poll; accepted as an encounter."
                        )

                    if mon["species_id"] != target_id:
                        raise RuntimeError(
                            f"Selected {mon['species']} instead of {chosen_name}."
                        )

                    generation_trace = trace_generation_from_seed(
                        seed, mon, self.last_confirm_rng
                    )

                    if generation_trace.get("trace_found"):
                        self.trace_successes += 1
                        if self.cia_rng_gate:
                            try:
                                record_reply = self.bridge.starter_record(
                                    generation_trace["generation_pre_rng"],
                                    generation_trace.get("confirm_to_pid_calls") or 0,
                                )
                                record_fields = parse_key_value_reply(record_reply)
                                self.cia_generation_used = int(
                                    record_fields.get("gen_used", self.cia_generation_used)
                                )
                                self.cia_window_min = int(
                                    record_fields.get("win_min", self.cia_window_min)
                                )
                                self.cia_window_max = int(
                                    record_fields.get("win_max", self.cia_window_max)
                                )
                            except RuntimeError as record_error:
                                self.log.emit(
                                    "Generation trace was valid, but CIA STARTER_RECORD "
                                    f"failed: {record_error}"
                                )
                    else:
                        self.trace_failures += 1
                        self.log.emit(
                            f"Attempt {self.attempts}: exact PID/IV RNG generation "
                            "trace could not be reconstructed."
                        )

                    self.successful += 1
                    duplicate = mon["pid"] in self.unique_pids
                    if duplicate:
                        self.duplicates += 1
                    else:
                        self.unique_pids.add(mon["pid"])

                    # A PID+IV+species fingerprint distinguishes a true repeated
                    # generated result from an astronomically rare PID-only collision.
                    iv = mon["ivs"]
                    fingerprint = (
                        mon["species_id"],
                        mon["pid"],
                        iv["hp"], iv["atk"], iv["def"],
                        iv["spa"], iv["spd"], iv["spe"],
                    )
                    repeated_generation = fingerprint in self.duplicate_fingerprints
                    if not repeated_generation:
                        self.duplicate_fingerprints.add(fingerprint)
                        self.effective_rolls += 1
                    elif self.cia_rng_gate:
                        self.log.emit(
                            "Exact PID+IV generation repeat detected despite the "
                            "generation-aware CIA guard; retained as a diagnostic "
                            "repeat and excluded from Effective Rolls."
                        )

                    mon = dict(mon)
                    mon.update(generation_trace)
                    mon["attempt"] = self.attempts
                    mon["encounter_no"] = self.successful
                    mon["chosen"] = chosen_name
                    mon["duplicate"] = duplicate
                    mon["starter_mode"] = self.starter_mode
                    mon["confirm_rng"] = self.last_confirm_rng
                    mon["rng_jitter_ms"] = self.last_jitter_ms
                    mon["rng_delay_frames"] = self.last_delay_frames
                    mon["rng_gate_mode"] = (
                        "CIA generation-aware" if self.cia_rng_gate
                        else ("PC compatibility" if self.unique_rng else "Disabled")
                    )
                    mon["cia_session_used"] = self.cia_gate_used
                    mon["cia_seed_arms"] = self.cia_seed_arms
                    mon["cia_seed_changes"] = self.cia_seed_changes
                    mon["cia_blocked_total"] = self.cia_blocked_total
                    mon["cia_exact_blocked"] = self.cia_exact_blocked
                    mon["cia_exact_blocked_total"] = self.cia_exact_blocked_total
                    mon["cia_generation_used"] = self.cia_generation_used
                    mon["cia_generation_blocked"] = self.cia_generation_blocked
                    mon["cia_generation_blocked_total"] = self.cia_generation_blocked_total
                    mon["cia_predicted_pid_calls"] = self.cia_predicted_pid_calls
                    mon["cia_window_min"] = self.cia_window_min
                    mon["cia_window_max"] = self.cia_window_max
                    mon["cia_capacity"] = self.cia_capacity
                    mon["trace_successes"] = self.trace_successes
                    mon["trace_failures"] = self.trace_failures
                    mon["repeated_generation"] = repeated_generation
                    mon["effective_roll_no"] = self.effective_rolls
                    if self.last_rtc is not None:
                        mon["rtc_seed"] = self.last_rtc.get("seed")
                        mon["rtc_timestamp"] = self.last_rtc.get("timestamp")
                    self.encounter.emit(mon)
                    self.consecutive_failures = 0
                    self.emit_stats()

                    if mon["shiny"]:
                        self.shinies += 1
                        self.safe_fast_off()
                        self.emit_stats()
                        try:
                            import winsound
                            for _ in range(4):
                                winsound.Beep(1300, 220)
                                time.sleep(0.08)
                        except Exception:
                            pass
                        self.stopped.emit(f"SHINY {mon['species']} FOUND")
                        return

                    if self.battle_mode == "View battle":
                        self.wait_for_battle_view()

                    # Skip battle mode resets immediately after validated PK3 read.
                    time.sleep(0.08)

                except RuntimeError as e:
                    self.consecutive_failures += 1
                    try:
                        cb = main_callback2(self.bridge)
                        pc = self.bridge.read(ADDR["gPlayerPartyCount"], 1)[0]
                        recovery = (
                            f"Attempt {self.attempts} recovery: {e} "
                            f"[callback=0x{cb:08X}, party={pc}]"
                        )
                    except Exception:
                        recovery = f"Attempt {self.attempts} recovery: {e}"

                    self.recovery_log.append(recovery)
                    self.log.emit(recovery)

                    self.emit_stats()
                    if self.consecutive_failures >= 3:
                        self.safe_fast_off()
                        self.error.emit(
                            "Three consecutive failures. Safety stop. "
                            "Click Export Support and send the ZIP."
                        )
                        return
                    time.sleep(0.35)

            self.safe_fast_off()
            self.stopped.emit("Stopped by user.")

        except Exception as e:
            self.safe_fast_off()
            self.error.emit(str(e))

class StatTile(QFrame):
    def __init__(self, title, value="—"):
        super().__init__()
        self.setObjectName("statTile")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("statTileTitle")
        self.value = QLabel(value)
        self.value.setObjectName("statTileValue")
        layout.addWidget(self.title)
        layout.addWidget(self.value)

class OverlayStatRow(QFrame):
    def __init__(self, label, value="0", icon="◆"):
        super().__init__()
        self.setObjectName("overlayStatRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)

        self.icon = QLabel(icon)
        self.icon.setObjectName("overlayStatIcon")
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setFixedWidth(22)

        self.label = QLabel(label)
        self.label.setObjectName("overlayStatLabel")
        self.label.setWordWrap(True)

        self.value = QLabel(value)
        self.value.setObjectName("overlayStatValue")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        row.addWidget(self.icon)
        row.addWidget(self.label, 1)
        row.addWidget(self.value)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.worker = None
        self.recent = []
        self.stats_store = StatsStore()
        self.rng_history_store = RngHistoryStore()
        self._session_active = False
        self.support_events = deque(maxlen=500)
        self._last_stats_elapsed = 0.0
        self._last_rng_event = {}
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(250)
        self.ui_timer.timeout.connect(self.refresh_live_phase_timer)
        self.ui_timer.start()

        root = QWidget()
        root.setObjectName("appRoot")
        root.setStyleSheet(POKEMON_THEME_QSS)
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 10)
        main.setSpacing(9)

        # Do not propagate the combined size hints of all tab pages into a
        # giant Windows minimum-track size. Tabs may shrink to the available
        # desktop work area; their own controls remain laid out normally.
        main.setSizeConstraint(QLayout.SetNoConstraint)
        root.setMinimumSize(0, 0)
        root.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Header — Pokédex-inspired without sacrificing readability.
        header_frame = QFrame()
        header_frame.setObjectName("appHeader")
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(13, 9, 13, 9)
        header.setSpacing(10)

        header.addWidget(PokeBallMark(42))

        title_stack = QVBoxLayout()
        title_stack.setSpacing(1)
        title = QLabel("Pokebot3DS-CFW")
        title.setObjectName("appTitle")
        subtitle = QLabel("GEN 3 STARTER HUNTER   •   RUBY v1.1   •   mGBA / NEW 3DS")
        subtitle.setObjectName("appSubtitle")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)

        # Small starter trio makes the app immediately read as Pokémon while
        # keeping the title bar compact on 1280×800-class displays.
        starter_strip = QHBoxLayout()
        starter_strip.setSpacing(3)
        for starter_name in ("Treecko", "Torchic", "Mudkip"):
            starter_icon = QLabel()
            starter_icon.setFixedSize(34, 34)
            starter_icon.setAlignment(Qt.AlignCenter)
            starter_icon.setToolTip(starter_name)
            starter_path = Path(__file__).resolve().parent / "assets" / "pokemon" / "normal" / f"{starter_name}.png"
            starter_pix = QPixmap(str(starter_path))
            if not starter_pix.isNull():
                starter_icon.setPixmap(starter_pix.scaled(32, 32, Qt.KeepAspectRatio, Qt.FastTransformation))
            starter_strip.addWidget(starter_icon)
        header.addLayout(starter_strip)

        header.addStretch()
        self.export_support_btn = QPushButton("Export Support")
        self.export_support_btn.setObjectName("supportButton")
        self.export_support_btn.setToolTip(
            "Create a Pokebot3DS-CFW support ZIP with logs, AppData stats, "
            "RNG history and a fresh bridge snapshot."
        )
        self.connection_badge = QLabel("Disconnected")
        self.connection_badge.setObjectName("connectionBadge")
        self.connection_badge.setProperty("state", "stopped")

        header.addWidget(self.export_support_btn)
        header.addWidget(self.connection_badge)
        main.addWidget(header_frame)

        # Main application tabs
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setMinimumSize(0, 0)
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        main.addWidget(self.tabs, 1)

        # ================================================================
        # DASHBOARD TAB — inspired by PokéBot Gen3's stream overlay
        # ================================================================
        dashboard_tab = QWidget()
        dashboard_tab.setMinimumSize(0, 0)
        dashboard_tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        dashboard_tab.setObjectName("overlayDashboard")

        # Theme is applied globally from appRoot so every tab shares one visual language.


        dashboard = QVBoxLayout(dashboard_tab)
        dashboard.setContentsMargins(6, 6, 6, 6)
        dashboard.setSpacing(6)

        # ------------------------------------------------------------
        # TOP: controls + current encounter overlay
        # ------------------------------------------------------------
        top = QHBoxLayout()
        top.setSpacing(6)

        controls_panel = QFrame()
        controls_panel.setObjectName("overlayPanel")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(0, 0, 0, 8)
        controls_layout.setSpacing(6)

        controls_header = QLabel("Hunt Control")
        controls_header.setObjectName("overlayHeader")
        controls_header.setProperty("accent", "red")
        controls_layout.addWidget(controls_header)

        controls_body = QWidget()
        sgrid = QGridLayout(controls_body)
        sgrid.setContentsMargins(10, 4, 10, 0)
        sgrid.setHorizontalSpacing(8)
        sgrid.setVerticalSpacing(5)

        self.ip_edit = QLineEdit(DEFAULT_IP)
        self.starter_combo = QComboBox()
        self.starter_combo.addItems(["Torchic", "Treecko", "Mudkip", "Random"])
        self.ff_check = QCheckBox("mGBA fast-forward")
        self.rng_guard_check = QCheckBox("Frame-perfect unique RNG gate")
        self.rng_guard_check.setChecked(True)
        self.battle_combo = QComboBox()
        self.battle_combo.addItems(["Skip battle", "View battle"])
        self.start_btn = QPushButton("Start Hunt")
        self.start_btn.setObjectName("startButton")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setEnabled(False)
        self.start_btn.setMinimumHeight(30)
        self.stop_btn.setMinimumHeight(30)

        sgrid.addWidget(QLabel("N3DS IP"), 0, 0)
        sgrid.addWidget(self.ip_edit, 0, 1)
        sgrid.addWidget(QLabel("Starter"), 1, 0)
        sgrid.addWidget(self.starter_combo, 1, 1)
        sgrid.addWidget(QLabel("Battle"), 2, 0)
        sgrid.addWidget(self.battle_combo, 2, 1)
        sgrid.addWidget(self.ff_check, 3, 0, 1, 2)
        sgrid.addWidget(self.rng_guard_check, 4, 0, 1, 2)
        sgrid.addWidget(self.start_btn, 5, 0)
        sgrid.addWidget(self.stop_btn, 5, 1)

        controls_layout.addWidget(controls_body)
        top.addWidget(controls_panel, 2)

        # Current encounter panel follows PokéBot Gen3's compact stat table:
        # PID + six IVs + IV sum + nature + SV, with details beneath.
        live_panel = QFrame()
        live_panel.setObjectName("overlayPanel")
        live_layout = QVBoxLayout(live_panel)
        live_layout.setContentsMargins(0, 0, 0, 8)
        live_layout.setSpacing(5)

        live_header = QLabel("Current Encounter")
        live_header.setObjectName("overlayHeader")
        live_header.setProperty("accent", "blue")
        live_layout.addWidget(live_header)

        # Current encounter is visual-first: the Pokémon sprite is the identity.
        # Species name and PID remain available internally/support exports but are
        # intentionally not duplicated on the Dashboard.
        self.species = QLabel("—")
        self.species.setVisible(False)
        self.chosen = QLabel("Selected —")
        self.chosen.setVisible(False)
        self.pid = QLabel("—")
        self.pid.setVisible(False)

        encounter_content = QHBoxLayout()
        encounter_content.setContentsMargins(12, 8, 12, 2)
        encounter_content.setSpacing(14)

        sprite_wrap = QVBoxLayout()
        sprite_wrap.setSpacing(4)
        sprite_wrap.setAlignment(Qt.AlignCenter)
        self.sprite_label = QLabel()
        self.sprite_label.setFixedSize(118, 118)
        self.sprite_label.setAlignment(Qt.AlignCenter)
        self.sprite_label.setObjectName("spriteCard")
        self.sprite_variant = QLabel("—")
        self.sprite_variant.setObjectName("overlaySubtle")
        self.sprite_variant.setAlignment(Qt.AlignCenter)
        sprite_wrap.addWidget(self.sprite_label, 0, Qt.AlignCenter)
        sprite_wrap.addWidget(self.sprite_variant, 0, Qt.AlignCenter)
        encounter_content.addLayout(sprite_wrap)

        encounter_right = QVBoxLayout()
        encounter_right.setSpacing(7)

        status_row = QHBoxLayout()
        status_row.addStretch()
        self.shiny_badge = QLabel("NORMAL")
        self.shiny_badge.setAlignment(Qt.AlignCenter)
        self.shiny_badge.setMinimumWidth(104)
        self.shiny_badge.setStyleSheet(
            "padding: 5px 10px; border-radius: 8px; background: #243140; border: 1px solid #3d5062; color: #dce7f2; font-weight: 800;"
        )
        status_row.addWidget(self.shiny_badge)
        encounter_right.addLayout(status_row)

        encounter_grid = QGridLayout()
        encounter_grid.setContentsMargins(0, 0, 0, 0)
        encounter_grid.setHorizontalSpacing(10)
        encounter_grid.setVerticalSpacing(3)

        headings = ["HP", "ATK", "DEF", "SPA", "SPD", "SPE", "Σ", "NATURE", "SV"]
        for col, heading in enumerate(headings):
            lbl = QLabel(heading)
            lbl.setObjectName("overlayColumnHead")
            lbl.setAlignment(Qt.AlignCenter)
            encounter_grid.addWidget(lbl, 0, col)

        self.iv_hp = QLabel("—")
        self.iv_atk = QLabel("—")
        self.iv_def = QLabel("—")
        self.iv_spa = QLabel("—")
        self.iv_spd = QLabel("—")
        self.iv_spe = QLabel("—")
        self.iv_sum = QLabel("—")
        self.nature = QLabel("—")
        self.sv = QLabel("—")

        for col, lbl in enumerate([
            self.iv_hp, self.iv_atk, self.iv_def,
            self.iv_spa, self.iv_spd, self.iv_spe, self.iv_sum,
            self.nature, self.sv
        ]):
            lbl.setObjectName("overlayColumnValue")
            lbl.setAlignment(Qt.AlignCenter)
            encounter_grid.addWidget(lbl, 1, col)

        encounter_grid.setColumnStretch(7, 2)
        encounter_right.addLayout(encounter_grid)

        details_line = QHBoxLayout()
        details_line.setContentsMargins(0, 4, 0, 0)
        self.gender = QLabel("Gender —")
        self.ability = QLabel("Ability —")
        self.hp = QLabel("Hidden Power —")
        self.gender.setObjectName("overlaySubtle")
        self.ability.setObjectName("overlaySubtle")
        self.hp.setObjectName("overlaySubtle")
        details_line.addWidget(self.gender)
        details_line.addWidget(self.ability)
        details_line.addWidget(self.hp)
        details_line.addStretch()
        encounter_right.addLayout(details_line)

        encounter_content.addLayout(encounter_right, 1)
        live_layout.addLayout(encounter_content)

        self.duplicate = QLabel("")
        self.duplicate.setObjectName("overlaySubtle")
        self.duplicate.setContentsMargins(10, 0, 10, 0)
        live_layout.addWidget(self.duplicate)

        top.addWidget(live_panel, 5)
        dashboard.addLayout(top)

        # ------------------------------------------------------------
        # BOTTOM: Shiny Phase + encounter log
        # ------------------------------------------------------------
        bottom = QHBoxLayout()
        bottom.setSpacing(6)

        phase_panel = QFrame()
        phase_panel.setObjectName("overlayPanel")
        phase_layout = QVBoxLayout(phase_panel)
        phase_layout.setContentsMargins(0, 0, 0, 6)
        phase_layout.setSpacing(0)

        phase_header = QLabel("Shiny Phase")
        phase_header.setObjectName("overlayHeader")
        phase_header.setProperty("accent", "gold")
        phase_header.setAlignment(Qt.AlignCenter)
        phase_layout.addWidget(phase_header)

        self.stat_elapsed = OverlayStatRow("Phase Timer", "00:00:00", "◷")
        self.stat_enc = OverlayStatRow("Phase Encounters", "0", "#")
        self.stat_effective = OverlayStatRow("Effective Rolls", "0", "◆")
        self.stat_rate = OverlayStatRow("Encounter Rate / hr", "0", "»")
        self.stat_unique = OverlayStatRow("Unique PIDs", "0", "★")
        self.stat_dup = OverlayStatRow("Duplicate PIDs", "0", "×")
        self.stat_unique_rate = OverlayStatRow("Unique / hr", "0", "↗")

        for row in [
            self.stat_elapsed, self.stat_enc, self.stat_effective,
            self.stat_rate, self.stat_unique, self.stat_dup, self.stat_unique_rate
        ]:
            phase_layout.addWidget(row)

        odds_wrap = QWidget()
        odds_layout = QVBoxLayout(odds_wrap)
        odds_layout.setContentsMargins(8, 6, 8, 2)
        odds_layout.setSpacing(3)

        self.odds_bar = QProgressBar()
        self.odds_bar.setRange(0, ODDS)
        self.odds_bar.setValue(0)
        self.odds_bar.setFormat("0 effective / 8192")
        self.chance_label = QLabel("Estimated shiny chance: 0.00%")
        self.eta_label = QLabel("ETA to 8192: —")
        self.chance_label.setObjectName("overlaySubtle")
        self.eta_label.setObjectName("overlaySubtle")
        odds_layout.addWidget(self.odds_bar)
        odds_layout.addWidget(self.chance_label)
        odds_layout.addWidget(self.eta_label)
        phase_layout.addWidget(odds_wrap)

        bottom.addWidget(phase_panel, 2)

        records_panel = QFrame()
        records_panel.setObjectName("overlayPanel")
        records_layout = QVBoxLayout(records_panel)
        records_layout.setContentsMargins(0, 0, 0, 6)
        records_layout.setSpacing(0)

        records_header = QLabel("Phase Records")
        records_header.setObjectName("overlayHeader")
        records_header.setProperty("accent", "teal")
        records_header.setAlignment(Qt.AlignCenter)
        records_layout.addWidget(records_header)

        self.record_low_sv = OverlayStatRow("Best SV (lowest)", "—", "↓")
        self.record_high_sv = OverlayStatRow("Worst SV (highest)", "—", "↑")
        self.record_high_iv = OverlayStatRow("Best IV Sum", "—", "★")
        self.record_low_iv = OverlayStatRow("Worst IV Sum", "—", "◇")
        self.record_phase_count = OverlayStatRow("Phase Encounters", "0", "#")

        for row in [
            self.record_low_sv, self.record_high_sv,
            self.record_high_iv, self.record_low_iv,
            self.record_phase_count
        ]:
            records_layout.addWidget(row)

        self.phase_record_note = QLabel(
            "Persists through restarts; resets after a shiny."
        )
        self.phase_record_note.setObjectName("overlaySubtle")
        self.phase_record_note.setWordWrap(True)
        self.phase_record_note.setContentsMargins(8, 6, 8, 2)
        records_layout.addWidget(self.phase_record_note)

        bottom.addWidget(records_panel, 2)

        log_panel = QFrame()
        log_panel.setObjectName("overlayPanel")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        log_header = QLabel("Encounter Log")
        log_header.setObjectName("overlayHeader")
        log_header.setProperty("accent", "violet")
        log_header.setAlignment(Qt.AlignCenter)
        log_layout.addWidget(log_header)

        # PokéBot Gen3's encounter log is IV/SV focused; keep the same shape.
        self.table = QTableWidget(0, 13)
        self.table.setMinimumSize(0, 170)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels([
            "#", "Sprite", "Pokémon", "HP", "ATK", "DEF", "SPA", "SPD", "SPE",
            "Σ", "SV", "Nature", "Result"
        ])
        self.table.setIconSize(QSize(40, 40))
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for col in (0, 1, 3, 4, 5, 6, 7, 8, 9, 10):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        log_layout.addWidget(self.table)

        # Encounter Log / Recent Shinies share the old Dashboard log area.
        # They are selectable sub-tabs so the Dashboard remains the single
        # place to watch a hunt.
        self.dashboard_log_tabs = QTabWidget()
        self.dashboard_log_tabs.setObjectName("dashboardLogTabs")
        self.dashboard_log_tabs.setDocumentMode(True)
        self.dashboard_log_tabs.setMinimumHeight(205)
        self.dashboard_log_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        encounter_log_view = QWidget()
        encounter_log_view_layout = QVBoxLayout(encounter_log_view)
        encounter_log_view_layout.setContentsMargins(0, 0, 0, 0)
        encounter_log_view_layout.setSpacing(0)
        log_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        encounter_log_view_layout.addWidget(log_panel, 1)
        self.dashboard_log_tabs.addTab(encounter_log_view, "Encounter Log")

        bottom.addWidget(self.dashboard_log_tabs, 5)
        dashboard.addLayout(bottom, 1)

        self.tabs.addTab(dashboard_tab, "Dashboard")

        # ================================================================
        # RNG / RTC TAB
        # ================================================================
        rng_tab = QWidget()
        rng_tab.setMinimumSize(0, 0)
        rng_tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        rng_main = QVBoxLayout(rng_tab)
        rng_main.setContentsMargins(10, 10, 10, 10)
        rng_main.setSpacing(12)

        rtc_box = QGroupBox("Ruby / Sapphire RNG / RTC diagnostics")
        rtc_grid = QGridLayout(rtc_box)

        self.rtc_mode_tile = StatTile("RTC", "—")
        self.rtc_time_tile = StatTile("RTC time", "—")
        self.rtc_seed_tile = StatTile("RTC initial seed", "—")
        self.seed_count_tile = StatTile("Resets on seed", "0")
        self.confirm_rng_tile = StatTile("Starter RNG state", "—")
        self.rng_history_tile = StatTile("PC persistent states", str(len(self.rng_history_store)))
        self.rng_avoided_tile = StatTile("Repeat frames avoided", "0")
        self.rng_gate_tile = StatTile("RNG gate", "—")
        self.rng_delay_tile = StatTile("CIA delay", "—")
        self.seed_cadence_tile = StatTile("R/S seed cadence", "1 minute")
        self.cia_session_used_tile = StatTile("CIA confirm states", "0")
        self.cia_generation_used_tile = StatTile("Recorded generation states", "0")
        self.cia_seed_used_tile = StatTile("Attempts on seed", "0")
        self.cia_seed_changes_tile = StatTile("Seed changes", "0")
        self.cia_blocked_total_tile = StatTile("CIA blocked total", "0")
        self.cia_generation_blocked_tile = StatTile("Predicted repeats blocked", "0")
        self.cia_exact_blocked_tile = StatTile("Exact confirm repeats blocked", "0")
        self.cia_window_tile = StatTile("Generation guard window", "PID calls 48–72")
        self.cia_capacity_tile = StatTile("CIA history capacity", "32768")
        self.pid_call_index_tile = StatTile("PID RNG call index", "—")
        self.confirm_to_pid_tile = StatTile("Confirm → PID calls", "—")
        self.generation_pre_rng_tile = StatTile("Generation pre-RNG", "—")
        self.trace_coverage_tile = StatTile("Generation trace coverage", "0 / 0")
        self.generation_repeat_tile = StatTile("Exact generation repeats", "0")

        rtc_tiles = [
            self.rtc_mode_tile, self.rtc_time_tile, self.rtc_seed_tile,
            self.seed_count_tile, self.confirm_rng_tile, self.rng_gate_tile,
            self.rng_delay_tile, self.seed_cadence_tile,
            self.cia_session_used_tile, self.cia_generation_used_tile,
            self.cia_seed_used_tile, self.cia_seed_changes_tile,
            self.cia_blocked_total_tile, self.cia_generation_blocked_tile,
            self.cia_exact_blocked_tile, self.cia_window_tile,
            self.cia_capacity_tile, self.rng_history_tile,
            self.rng_avoided_tile, self.pid_call_index_tile,
            self.confirm_to_pid_tile, self.generation_pre_rng_tile,
            self.trace_coverage_tile, self.generation_repeat_tile
        ]
        for idx, tile in enumerate(rtc_tiles):
            rtc_grid.addWidget(tile, idx // 4, idx % 4)

        rng_main.addWidget(rtc_box)

        current_rng_box = QGroupBox("Current generation")
        current_rng_layout = QVBoxLayout(current_rng_box)
        self.seed_rng = QLabel("RTC initial seed —   •   Starter RNG —   •   CIA frame delay —")
        self.seed_rng.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.seed_rng.setWordWrap(True)
        current_rng_layout.addWidget(self.seed_rng)

        self.rng_strategy_label = QLabel(
            "RNG strategy: Retail RTC + CIA-local gRngValue gate + one-frame local A injection"
        )
        self.rng_strategy_label.setObjectName("sectionNote")
        self.rng_strategy_label.setWordWrap(True)
        current_rng_layout.addWidget(self.rng_strategy_label)
        rng_main.addWidget(current_rng_box)

        history_box = QGroupBox("Persistent RNG history")
        history_layout = QGridLayout(history_box)
        self.rng_history_path_label = QLabel(str(self.rng_history_store.path))
        self.rng_history_path_label.setWordWrap(True)
        self.rng_history_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.rng_history_path_label.setObjectName("sectionNote")
        self.reset_rng_btn = QPushButton("Reset RNG History")
        self.reset_rng_btn.setObjectName("dangerButton")
        history_layout.addWidget(QLabel("History file"), 0, 0)
        history_layout.addWidget(self.rng_history_path_label, 0, 1)
        history_layout.addWidget(self.reset_rng_btn, 1, 1)
        rng_main.addWidget(history_box)

        decisions_box = QGroupBox("Recent RNG decisions")
        decisions_layout = QVBoxLayout(decisions_box)
        self.rng_decision_table = QTableWidget(0, 13)
        self.rng_decision_table.setHorizontalHeaderLabels([
            "#", "RTC Seed", "Seed Attempt", "Confirm RNG", "Delay",
            "Gen Window", "Pred Blocked", "PID Call", "Δ Confirm→PID",
            "Generation pre-RNG", "Recorded Gen", "PID / IVΣ", "Result"
        ])
        self.rng_decision_table.verticalHeader().setVisible(False)
        self.rng_decision_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rng_decision_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rng_decision_table.setAlternatingRowColors(True)
        self.rng_decision_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for col in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            self.rng_decision_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.rng_decision_table.setMaximumHeight(190)
        decisions_layout.addWidget(self.rng_decision_table)
        rng_main.addWidget(decisions_box)

        explanation = QLabel(
            "Ruby/Sapphire's live-battery initial seed is minute-based, so around "
            "11 fast resets can legitimately share one initial seed at ~670/hour. "
            "That does not make them the same roll. v0p19 no longer uses the over-broad "
            "±64 confirmation-state guard. After every valid PK3 the PC reconstructs the "
            "actual generation_pre_rng used by CreateBoxMon and records it in the CIA. "
            "Before the next local A injection, the CIA predicts every possible generation "
            "state in the observed Confirm→PID window (currently 48–72 calls) and blocks "
            "only candidates that would reproduce a generation already seen this hunt. "
            "This targets real PID/IV repeats without stalling hundreds of adjacent frames."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("sectionNote")
        rng_main.addWidget(explanation)
        rng_main.addStretch(1)

        self.tabs.addTab(rng_tab, "RNG / RTC")

        # ================================================================
        # RECENT SHINIES TAB
        # ================================================================
        shiny_tab = QWidget()
        shiny_tab.setMinimumSize(0, 0)
        shiny_tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        shiny_layout = QVBoxLayout(shiny_tab)
        shiny_layout.setContentsMargins(10, 10, 10, 10)
        shiny_layout.setSpacing(8)

        shiny_head = QHBoxLayout()
        shiny_title = QLabel("Recent Shinies")
        shiny_title.setObjectName("appSectionTitle")
        self.clear_shinies_btn = QPushButton("Clear Recent Shinies")
        self.clear_shinies_btn.setObjectName("dangerButton")
        shiny_head.addWidget(shiny_title)
        shiny_head.addStretch()
        shiny_head.addWidget(self.clear_shinies_btn)
        shiny_layout.addLayout(shiny_head)

        shiny_note = QLabel(
            "Stored in AppData and retained across bot updates. Up to the 100 most recent shiny encounters are kept."
        )
        shiny_note.setObjectName("sectionNote")
        shiny_note.setWordWrap(True)
        shiny_layout.addWidget(shiny_note)

        self.shiny_table = QTableWidget(0, 16)
        self.shiny_table.setHorizontalHeaderLabels([
            "Seen", "Sprite", "#", "Pokémon", "PID", "HP", "ATK", "DEF",
            "SPA", "SPD", "SPE", "Σ", "SV", "Nature", "Ability", "Hidden Power"
        ])
        self.shiny_table.setIconSize(QSize(48, 48))
        self.shiny_table.verticalHeader().setVisible(False)
        self.shiny_table.verticalHeader().setDefaultSectionSize(52)
        self.shiny_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.shiny_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.shiny_table.setAlternatingRowColors(True)
        self.shiny_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for col in (0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12):
            self.shiny_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        shiny_layout.addWidget(self.shiny_table, 1)

        self.dashboard_log_tabs.addTab(shiny_tab, "Recent Shinies")
        self.refresh_recent_shinies()

        # ================================================================
        # STATISTICS TAB
        # ================================================================
        statistics_tab = QWidget()
        statistics_tab.setMinimumSize(0, 0)
        statistics_tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        statistics_layout = QVBoxLayout(statistics_tab)
        statistics_layout.setContentsMargins(10, 10, 10, 10)
        statistics_layout.setSpacing(12)

        lifetime_box = QGroupBox("Lifetime statistics")
        lifetime_grid = QGridLayout(lifetime_box)
        self.life_enc = StatTile("Total encounters", "0")
        self.life_shiny = StatTile("Shinies", "0")
        self.life_dup = StatTile("Duplicate PIDs", "0")
        self.life_sessions = StatTile("Sessions", "0")
        self.life_rng_avoided = StatTile("RNG repeats avoided", "0")
        life_tiles = [
            self.life_enc, self.life_shiny, self.life_dup,
            self.life_sessions, self.life_rng_avoided
        ]
        for idx, tile in enumerate(life_tiles):
            lifetime_grid.addWidget(tile, 0, idx)
        statistics_layout.addWidget(lifetime_box)

        appdata_box = QGroupBox("AppData persistence")
        appdata_layout = QGridLayout(appdata_box)
        self.stats_path_label = QLabel(str(self.stats_store.path))
        self.stats_path_label.setWordWrap(True)
        self.stats_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.stats_path_label.setObjectName("sectionNote")
        self.reset_lifetime_btn = QPushButton("Reset Lifetime Stats")
        self.reset_lifetime_btn.setObjectName("dangerButton")
        appdata_layout.addWidget(QLabel("Stats file"), 0, 0)
        appdata_layout.addWidget(self.stats_path_label, 0, 1)
        appdata_layout.addWidget(self.reset_lifetime_btn, 1, 1)
        statistics_layout.addWidget(appdata_box)

        stats_info = QLabel(
            "Session counters reset when a new hunt starts. Lifetime totals are stored in AppData "
            "and survive bot-folder updates until Reset Lifetime Stats is used."
        )
        stats_info.setWordWrap(True)
        stats_info.setObjectName("sectionNote")
        statistics_layout.addWidget(stats_info)
        statistics_layout.addStretch(1)

        self.tabs.addTab(statistics_tab, "Statistics")
        self.refresh_lifetime_stats()

        # Footer status
        footer_frame = QFrame()
        footer_frame.setObjectName("footerBar")
        footer = QHBoxLayout(footer_frame)
        footer.setContentsMargins(10, 5, 10, 5)
        self.status_label = QLabel("Ready. Load Ruby or Sapphire at Birch's bag.")
        self.status_label.setObjectName("footerStatus")
        self.game_label = QLabel("Game: —")
        self.game_label.setObjectName("footerGame")
        footer.addWidget(self.status_label)
        footer.addStretch()
        footer.addWidget(self.game_label)
        main.addWidget(footer_frame)

        self.start_btn.clicked.connect(self.start_hunt)
        self.stop_btn.clicked.connect(self.stop_hunt)
        self.reset_lifetime_btn.clicked.connect(self.reset_lifetime_stats)
        self.reset_rng_btn.clicked.connect(self.reset_rng_history)
        self.export_support_btn.clicked.connect(self.export_support)
        self.clear_shinies_btn.clicked.connect(self.clear_recent_shinies)
        self.refresh_phase_records()

    def set_connection_state(self, text, state):
        self.connection_badge.setText(text)
        self.connection_badge.setProperty("state", state)
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)
        self.connection_badge.update()

    def _support_event(self, kind, payload):
        self.support_events.append({
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "kind": kind,
            "payload": payload,
        })

    def _record_text(self, rec):
        if not rec:
            return "—"
        # Phase Records are value-only. Pokémon identity and PID are already
        # available in Encounter Log / support data and only clutter this card.
        return str(rec.get("value", "—"))

    def refresh_phase_records(self, records=None):
        records = records if records is not None else self.stats_store.phase_records()
        self.record_low_sv.value.setText(self._record_text(records.get("lowest_sv")))
        self.record_high_sv.value.setText(self._record_text(records.get("highest_sv")))
        self.record_high_iv.value.setText(self._record_text(records.get("highest_iv_sum")))
        self.record_low_iv.value.setText(self._record_text(records.get("lowest_iv_sum")))
        self.record_phase_count.value.setText(str(records.get("encounters", 0)))
        if records.get("pending_reset"):
            self.phase_record_note.setText(
                "Shiny phase complete — records clear when the next hunt starts."
            )
        else:
            self.phase_record_note.setText(
                "Persists through restarts; resets after a shiny."
            )

    def sprite_info(self, mon):
        species = mon.get("species") or mon.get("starter") or "Unknown"
        if mon.get("shiny"):
            variant = "shiny"
            label = "SHINY"
        elif int(mon.get("sv", 0) or 0) >= 65528:
            variant = "anti-shiny"
            label = "ANTI-SHINY"
        else:
            variant = "normal"
            label = "NORMAL"
        path = Path(__file__).resolve().parent / "assets" / "pokemon" / variant / f"{species}.png"
        return path, variant, label

    def update_sprite(self, mon):
        path, _variant, label = self.sprite_info(mon)
        pix = QPixmap(str(path))
        if not pix.isNull():
            self.sprite_label.setPixmap(
                pix.scaled(110, 110, Qt.KeepAspectRatio, Qt.FastTransformation)
            )
        else:
            self.sprite_label.clear()
            self.sprite_label.setText("?")
        self.sprite_variant.setText(label)

    def sprite_item(self, mon):
        path, _variant, label = self.sprite_info(mon)
        item = QTableWidgetItem()
        if path.exists():
            item.setIcon(QIcon(str(path)))
        item.setToolTip(label)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def refresh_live_phase_timer(self):
        # Keep the phase timer moving independently of encounter/reset events.
        if self.worker and self.worker.isRunning() and self.worker.started_at is not None:
            elapsed = max(0.0, time.monotonic() - self.worker.started_at)
            self._last_stats_elapsed = elapsed
            self.stat_elapsed.value.setText(fmt_elapsed(elapsed))

    def refresh_recent_shinies(self):
        if not hasattr(self, "shiny_table"):
            return
        rows = self.stats_store.recent_shinies()
        self.shiny_table.setRowCount(0)
        for rec in rows:
            row = self.shiny_table.rowCount()
            self.shiny_table.insertRow(row)
            iv = rec.get("ivs") or {}
            iv_values = [int(iv.get(k, 0)) for k in ("hp", "atk", "def", "spa", "spd", "spe")]
            seen = str(rec.get("recorded_at", "—")).replace("T", " ")[:19]
            values = [
                seen, None, str(rec.get("encounter_no", "—")),
                rec.get("species") or rec.get("starter") or "—",
                rec.get("pid", "—"),
                *[str(v) for v in iv_values],
                str(sum(iv_values)), str(rec.get("sv", "—")),
                rec.get("nature", "—"), rec.get("ability", "—"),
                f"{rec.get('hidden_power', '—')} {rec.get('hidden_power_power', '')}".strip(),
            ]
            for col, value in enumerate(values):
                item = self.sprite_item(rec) if col == 1 else QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                self.shiny_table.setItem(row, col, item)
        if self.shiny_table.rowCount():
            self.shiny_table.scrollToBottom()

    def clear_recent_shinies(self):
        answer = QMessageBox.question(
            self, "Clear Recent Shinies",
            "Clear the stored Recent Shinies list? Lifetime shiny totals are not changed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.stats_store.clear_recent_shinies()
            self.refresh_recent_shinies()
            self.status_label.setText("Recent Shinies cleared.")

    def export_support(self):
        settings = {
            "ip": self.ip_edit.text().strip(),
            "starter": self.starter_combo.currentText(),
            "fast_forward": self.ff_check.isChecked(),
            "battle_display": self.battle_combo.currentText(),
            "avoid_reused_rng": self.rng_guard_check.isChecked(),
            "connection_badge": self.connection_badge.text(),
            "status": self.status_label.text(),
            "game": self.game_label.text(),
        }
        try:
            self.status_label.setText("Exporting support ZIP…")
            QApplication.processEvents()
            path = export_support_bundle(
                settings["ip"],
                settings,
                self.support_events,
                APP_TITLE,
                Path(__file__).resolve().parent,
            )
            self.status_label.setText(f"Support ZIP exported: {path}")
            QMessageBox.information(
                self,
                "Export Support",
                f"Support package created:\n\n{path}\n\nSend this ZIP with the issue report."
            )
        except Exception as e:
            self.status_label.setText(f"Support export failed: {e}")
            QMessageBox.critical(self, "Export Support", str(e))

    def refresh_lifetime_stats(self):
        life = self.stats_store.lifetime()
        self.life_enc.value.setText(str(life.get("encounters", 0)))
        self.life_shiny.value.setText(str(life.get("shinies", 0)))
        self.life_dup.value.setText(str(life.get("duplicates", 0)))
        self.life_sessions.value.setText(str(life.get("sessions", 0)))
        self.life_rng_avoided.value.setText(str(life.get("rng_repeats_avoided", 0)))

    def reset_lifetime_stats(self):
        answer = QMessageBox.question(
            self,
            "Reset Lifetime Stats",
            "Reset all Gen 3 lifetime totals stored in AppData?\n\n"
            "This does not delete your game save or current bot files.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.stats_store.reset_lifetime()
            self.refresh_lifetime_stats()
            self.status_label.setText("Lifetime stats reset.")

    def reset_rng_history(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(
                self, "RNG History", "Stop the hunt before resetting RNG history."
            )
            return
        answer = QMessageBox.question(
            self,
            "Reset RNG History",
            "Clear all previously used starter RNG states?\n\n"
            "This can allow previously-seen PID/IV sequences to occur again.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.rng_history_store.reset()
            self.rng_history_tile.value.setText("0")
            self.status_label.setText("Persistent RNG history reset.")

    def _finish_persisted_session(self, status):
        if self._session_active:
            self.stats_store.finish_session(status)
            self._session_active = False
            self.refresh_lifetime_stats()

    def set_controls_running(self, running):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.ip_edit.setEnabled(not running)
        self.starter_combo.setEnabled(not running)
        self.ff_check.setEnabled(not running)
        self.battle_combo.setEnabled(not running)
        self.rng_guard_check.setEnabled(not running)
        self.reset_rng_btn.setEnabled(not running)

    def start_hunt(self):
        if self.worker and self.worker.isRunning():
            return

        ip = self.ip_edit.text().strip()
        starter = self.starter_combo.currentText()
        fast = self.ff_check.isChecked()
        battle = self.battle_combo.currentText()
        unique_rng = self.rng_guard_check.isChecked()

        phase = self.stats_store.prepare_phase_for_hunt()
        self.refresh_phase_records(phase)

        self.stats_store.begin_session({
            "ip": ip,
            "starter_mode": starter,
            "fast_forward": fast,
            "battle_mode": battle,
            "unique_rng_guard": unique_rng,
            "game": "Auto-detect Ruby/Sapphire",
            "game_code": "AXVE/AXPE",
            "revision": "00/01",
        })
        self._session_active = True
        self.refresh_lifetime_stats()

        self.table.setRowCount(0)
        self.rng_decision_table.setRowCount(0)
        self.recent.clear()
        self._last_rng_event = {}
        self._last_stats_elapsed = 0.0
        self.stat_elapsed.value.setText("00:00:00")
        self.generation_repeat_tile.value.setText("0")
        self.cia_session_used_tile.value.setText("0")
        self.cia_seed_used_tile.value.setText("0")
        self.cia_seed_changes_tile.value.setText("0")
        self.cia_blocked_total_tile.value.setText("0")
        self.set_connection_state("Connecting…", "connecting")
        self.status_label.setText("Connecting to mGBA bridge…")
        self.set_controls_running(True)

        self.worker = HuntWorker(ip, starter, fast, battle, unique_rng=unique_rng)
        self.worker.connected.connect(self.on_connected)
        self.worker.encounter.connect(self.on_encounter)
        self.worker.stats.connect(self.on_stats)
        self.worker.rtc.connect(self.on_rtc)
        self.worker.rng.connect(self.on_rng)
        self.worker.log.connect(self.on_log)
        self.worker.stopped.connect(self.on_stopped)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def stop_hunt(self):
        if self.worker and self.worker.isRunning():
            self.status_label.setText("Stopping…")
            self.worker.request_stop()

    def on_connected(self, info):
        self._support_event("connected", info)
        self.set_connection_state("Connected", "connected")
        self.game_label.setText(
            f"Game: {info.get('game_name', 'Pokémon Gen 3')} "
            f"{info.get('game_code', '—')} v{info.get('game_version', '?')} "
            f"rev {info['revision']:02X}  •  "
            f"Route {info['map_group']}/{info['map_num']}  •  {info['status']}"
        )
        cia_gate = bool(info.get("cia_rng_gate"))
        self.rng_gate_tile.value.setText(
            "CIA generation-aware" if cia_gate else "PC compatibility"
        )
        self.rng_gate_tile.value.setStyleSheet(
            "font-size: 16px; font-weight: 800; color: "
            + ("#6d8;" if cia_gate else "#dbb45c;")
        )
        self.status_label.setText(
            "Hunting with CIA generation-aware RNG guard."
            if cia_gate else
            "Hunting with PC compatibility RNG gate — install v0p21 CIA."
        )

    def on_encounter(self, mon):
        self._support_event("encounter", mon)
        self.stats_store.record_encounter(mon)
        phase_records = self.stats_store.update_phase_records(mon)
        self.refresh_phase_records(phase_records)
        self.refresh_lifetime_stats()
        self.update_sprite(mon)
        if mon.get("shiny"):
            self.refresh_recent_shinies()

        self.species.setText(mon["species"])
        self.chosen.setText(
            f"Selected: {mon['chosen']}"
            + ("  •  Random mode" if mon["starter_mode"] == "Random" else "")
        )

        iv = mon["ivs"]
        iv_values = [
            iv["hp"], iv["atk"], iv["def"], iv["spa"], iv["spd"], iv["spe"]
        ]
        iv_labels = [
            self.iv_hp, self.iv_atk, self.iv_def,
            self.iv_spa, self.iv_spd, self.iv_spe
        ]

        self.pid.setText(f"{mon['pid']:08X}")
        self.nature.setText(mon["nature"])
        self.gender.setText(f"Gender: {mon['gender']}")
        self.ability.setText(
            f"Ability: {mon['ability']} (slot {mon['ability_slot']})"
        )
        self.hp.setText(
            f"Hidden Power: {mon['hidden_power']} {mon['hidden_power_power']}"
        )
        self.sv.setText(str(mon["sv"]))
        self.iv_sum.setText(str(sum(iv_values)))

        # Overlay-style IV colouring: perfect IV green, zero IV red.
        for label, value in zip(iv_labels, iv_values):
            label.setText(str(value))
            if value == 31:
                label.setStyleSheet("color: #63d297; font-size: 13px; font-weight: 800;")
            elif value == 0:
                label.setStyleSheet("color: #ff746d; font-size: 13px; font-weight: 800;")
            else:
                label.setStyleSheet("color: #e7eef4; font-size: 13px; font-weight: 700;")

        seed_text = (
            f"0x{mon['rtc_seed']:04X}"
            if mon.get("rtc_seed") is not None else "—"
        )
        rng_text = (
            f"0x{mon['confirm_rng']:08X}"
            if mon.get("confirm_rng") is not None else "—"
        )
        delay = mon.get("rng_delay_frames", 0)
        gate_mode = mon.get("rng_gate_mode", "—")
        self.seed_rng.setText(
            f"RTC initial seed {seed_text}   •   Starter RNG {rng_text}   •   "
            f"CIA delay {delay} frames   •   {gate_mode}"
        )

        if mon["shiny"]:
            self.shiny_badge.setText("SHINY")
            self.shiny_badge.setStyleSheet(
                "padding: 5px 10px; border-radius: 8px; "
                "background: #443811; border: 1px solid #a98a2e; color: #ffe58a; font-weight: 900;"
            )
            self.sv.setStyleSheet(
                "color: #63d297; font-size: 13px; font-weight: 800;"
            )
        else:
            # PokéBot Gen3's overlay uses purple for the fun anti-shiny SV band.
            if mon["sv"] >= 65528:
                self.shiny_badge.setText("ANTI-SHINY")
                self.shiny_badge.setStyleSheet(
                    "padding: 5px 10px; border-radius: 8px; "
                    "background: #3d2444; border: 1px solid #81528d; color: #efb4fa; font-weight: 900;"
                )
                self.sv.setStyleSheet(
                    "color: #e19af0; font-size: 13px; font-weight: 800;"
                )
            else:
                self.shiny_badge.setText("NORMAL")
                self.shiny_badge.setStyleSheet(
                    "padding: 5px 10px; border-radius: 8px; "
                    "background: #243140; border: 1px solid #3d5062; color: #dce7f2; font-weight: 800;"
                )
                self.sv.setStyleSheet(
                    "color: #e7eef4; font-size: 13px; font-weight: 700;"
                )

        if mon.get("repeated_generation"):
            self.duplicate.setText(
                "Repeated PID + IV generation — excluded from Effective Rolls"
            )
            self.duplicate.setStyleSheet("color: #ff746d; font-size: 10px;")
        elif mon["duplicate"]:
            self.duplicate.setText(
                "PID repeated with different IVs — retained as an Effective Roll"
            )
            self.duplicate.setStyleSheet("color: #f1c84b; font-size: 10px;")
        else:
            self.duplicate.setText("")
            self.duplicate.setStyleSheet("color: #a9adb4; font-size: 10px;")

        # IV/SV-focused encounter log like PokéBot Gen3's overlay.
        row = self.table.rowCount()
        self.table.insertRow(row)

        result = (
            "SHINY" if mon["shiny"]
            else ("ANTI-SHINY" if int(mon.get("sv", 0)) >= 65528
                  else ("REPEATED" if mon.get("repeated_generation")
                        else ("PID REPEAT" if mon["duplicate"] else "Normal")))
        )

        values = [
            str(mon["encounter_no"]),
            None,
            mon["species"],
            str(iv["hp"]),
            str(iv["atk"]),
            str(iv["def"]),
            str(iv["spa"]),
            str(iv["spd"]),
            str(iv["spe"]),
            str(sum(iv_values)),
            str(mon["sv"]),
            mon["nature"],
            result,
        ]

        for col, value in enumerate(values):
            item = self.sprite_item(mon) if col == 1 else QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignCenter)

            # Same visual language as the overlay: green/highlights, red lows,
            # purple anti-shiny SV, yellow shiny/result accents.
            if col in (3, 4, 5, 6, 7, 8):
                number = int(value)
                if number == 31:
                    item.setForeground(QColor("#63d297"))
                elif number == 0:
                    item.setForeground(QColor("#ff746d"))
            elif col == 10:
                if mon["shiny"]:
                    item.setForeground(QColor("#63d297"))
                elif mon["sv"] >= 65528:
                    item.setForeground(QColor("#e19af0"))
            elif col == 12 and mon["shiny"]:
                item.setForeground(QColor("#f1c84b"))

            self.table.setItem(row, col, item)

        # Keep every encounter seen in the current hunt session. QTableWidget
        # handles thousands of compact rows comfortably on the desktop client.
        self.table.scrollToBottom()

        self.generation_repeat_tile.value.setText(str(
            int(self.generation_repeat_tile.value.text() or "0")
            + (1 if mon.get("repeated_generation") else 0)
        ))

        self.pid_call_index_tile.value.setText(
            str(mon.get("pid_call_index")) if mon.get("pid_call_index") is not None else "—"
        )
        self.confirm_to_pid_tile.value.setText(
            f"{int(mon.get('confirm_to_pid_calls')):+d} calls"
            if mon.get("confirm_to_pid_calls") is not None else "—"
        )
        self.generation_pre_rng_tile.value.setText(
            f"0x{int(mon.get('generation_pre_rng')):08X}"
            if mon.get("generation_pre_rng") is not None else "—"
        )
        trace_ok = int(mon.get("trace_successes", 0))
        trace_fail = int(mon.get("trace_failures", 0))
        self.trace_coverage_tile.value.setText(
            f"{trace_ok} / {trace_ok + trace_fail}"
        )

        # Pair the final generated PID/IV result with the exact RNG decision.
        rng_row = self.rng_decision_table.rowCount()
        self.rng_decision_table.insertRow(rng_row)
        seed_attempt = int((self._last_rng_event or {}).get("cia_seed_arms", 0))
        blocked = int((self._last_rng_event or {}).get("blocked_values", 0))
        session_used = int((self._last_rng_event or {}).get("cia_session_used", mon.get("cia_session_used", 0)))
        window_min = int((self._last_rng_event or {}).get("cia_window_min", mon.get("cia_window_min", 48)))
        window_max = int((self._last_rng_event or {}).get("cia_window_max", mon.get("cia_window_max", 72)))
        predicted_blocked = int(
            (self._last_rng_event or {}).get(
                "cia_generation_blocked", mon.get("cia_generation_blocked", 0)
            )
        )
        recorded_gen = int(
            mon.get("cia_generation_used",
                    (self._last_rng_event or {}).get("cia_generation_used", 0))
        )
        pid_call_index = mon.get("pid_call_index")
        confirm_to_pid = mon.get("confirm_to_pid_calls")
        generation_pre = mon.get("generation_pre_rng")
        rng_values = [
            str(mon["encounter_no"]),
            f"0x{int(mon.get('rtc_seed') or 0):04X}",
            str(seed_attempt or self.seed_count_tile.value.text()),
            f"0x{int(mon.get('confirm_rng') or 0):08X}" if mon.get("confirm_rng") is not None else "—",
            f"{int(mon.get('rng_delay_frames', 0))}f",
            f"{window_min}–{window_max}",
            str(predicted_blocked),
            str(pid_call_index) if pid_call_index is not None else "—",
            (f"{int(confirm_to_pid):+d}" if confirm_to_pid is not None else "—"),
            f"0x{int(generation_pre):08X}" if generation_pre is not None else "—",
            str(recorded_gen),
            f"{mon['pid']:08X} / {sum(iv_values)}",
            result,
        ]
        for col, value in enumerate(rng_values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(Qt.AlignCenter)
            if col == 12 and mon.get("repeated_generation"):
                item.setForeground(QColor("#ff746d"))
            elif col == 12 and mon.get("shiny"):
                item.setForeground(QColor("#f1c84b"))
            self.rng_decision_table.setItem(rng_row, col, item)
        while self.rng_decision_table.rowCount() > 100:
            self.rng_decision_table.removeRow(0)
        self.rng_decision_table.scrollToBottom()

    def on_stats(self, s):
        self.stats_store.update_session_stats(s)

        self.stat_enc.value.setText(str(s["encounters"]))
        self.stat_unique.value.setText(str(s["unique"]))
        self.stat_effective.value.setText(str(s.get("effective_rolls", s["unique"])))
        self.stat_dup.value.setText(str(s["duplicates"]))
        self.stat_rate.value.setText(f"{s['rate']:.1f}")
        self.stat_unique_rate.value.setText(f"{s['unique_rate']:.1f}")
        self._last_stats_elapsed = float(s["elapsed"])
        self.stat_elapsed.value.setText(fmt_elapsed(self._last_stats_elapsed))
        effective = int(s.get("effective_rolls", s["encounters"]))
        effective_chance = (1.0 - ((ODDS - 1) / ODDS) ** effective) * 100.0
        self.odds_bar.setValue(min(ODDS, effective))
        self.odds_bar.setFormat(f"{effective} effective / {ODDS}")
        self.chance_label.setText(
            f"Estimated shiny chance: {effective_chance:.2f}%"
        )
        self.eta_label.setText(
            f"ETA to 8192: {fmt_eta(s['eta_hours'])}"
        )
        self.rng_avoided_tile.value.setText(str(s.get("rng_repeats_avoided", 0)))
        self.rng_history_tile.value.setText(str(s.get("rng_history_size", 0)))

    def on_rtc(self, rtc):
        # One RTC event is emitted at the start of every reset/attempt. Do not
        # let an RNG decision from the previous attempt leak into the log.
        self._last_rng_event = {}
        self._support_event("rtc", rtc)
        self.stats_store.record_rtc(rtc)
        self.rtc_mode_tile.value.setText(rtc.get("mode", "—"))
        self.rtc_time_tile.value.setText(rtc.get("timestamp", "—"))
        self.rtc_seed_tile.value.setText(f"0x{rtc.get('seed', 0):04X}")
        seed_resets = int(rtc.get("resets_on_seed", 0))
        self.seed_count_tile.value.setText(str(seed_resets))
        if rtc.get("dead_seed"):
            self.rtc_mode_tile.value.setStyleSheet(
                "font-size: 16px; font-weight: 800; color: #d66;"
            )
        elif rtc.get("rtc_ok"):
            self.rtc_mode_tile.value.setStyleSheet(
                "font-size: 16px; font-weight: 800; color: #6d8;"
            )

    def on_rng(self, rng):
        self._support_event("rng", rng)
        self.confirm_rng_tile.value.setText(f"0x{rng.get('value', 0):08X}")
        self.rng_history_tile.value.setText(str(rng.get("history_size", 0)))
        self.rng_avoided_tile.value.setText(str(rng.get("repeats_avoided", 0)))
        cia_gate = bool(rng.get("cia_gate"))
        self.rng_gate_tile.value.setText(
            "CIA generation-aware" if cia_gate else "PC compatibility"
        )
        self.rng_delay_tile.value.setText(
            f"{rng.get('delay_frames', 0)} frames" if cia_gate
            else f"{rng.get('jitter_ms', 0)} ms"
        )
        self._last_rng_event = dict(rng)
        if cia_gate:
            self.cia_session_used_tile.value.setText(str(rng.get("cia_session_used", 0)))
            self.cia_generation_used_tile.value.setText(str(rng.get("cia_generation_used", 0)))
            self.cia_seed_used_tile.value.setText(str(rng.get("cia_seed_arms", 0)))
            self.cia_seed_changes_tile.value.setText(str(rng.get("cia_seed_changes", 0)))
            self.cia_blocked_total_tile.value.setText(str(rng.get("blocked_total", 0)))
            self.cia_generation_blocked_tile.value.setText(
                str(rng.get("cia_generation_blocked_total", 0))
            )
            self.cia_exact_blocked_tile.value.setText(
                str(rng.get("cia_exact_blocked_total", 0))
            )
            win_min = int(rng.get("cia_window_min", 48))
            win_max = int(rng.get("cia_window_max", 72))
            self.cia_window_tile.value.setText(f"PID calls {win_min}–{win_max}")
            self.cia_capacity_tile.value.setText(str(rng.get("cia_capacity", 32768)))
        self.rng_strategy_label.setText(
            (
                "RNG strategy: Retail RTC + CIA generation-aware pre-RNG prediction + "
                "one-emulated-frame A injection + exact PID/IV generation tracing"
                if cia_gate else
                "RNG strategy: Retail RTC + PC compatibility unique-state gate"
            )
            + f"  •  history {rng.get('history_size', 0)}"
        )
        if rng.get("waited"):
            self.stats_store.add_rng_repeat_avoided(1)
            self.refresh_lifetime_stats()
            self.status_label.setText(
                (
                    f"CIA skipped {rng.get('blocked_values', 0)} unsafe RNG frame(s) "
                    f"({rng.get('cia_generation_blocked', 0)} predicted generation repeat(s)); "
                    if rng.get("cia_gate") else
                    f"Avoided reused RNG state; waited {rng.get('wait_seconds', 0):.3f}s "
                )
                + f"confirmed at 0x{rng.get('value', 0):08X}."
            )

    def on_log(self, message):
        self._support_event("log", message)
        self.status_label.setText(message)

    def on_stopped(self, reason):
        self.refresh_live_phase_timer()
        self._support_event("stopped", reason)
        self._finish_persisted_session(reason)
        self.set_controls_running(False)
        self.set_connection_state("Connected / stopped", "stopped")
        self.status_label.setText(reason)
        if reason.startswith("SHINY"):
            QMessageBox.information(self, "Shiny found", reason)

    def on_error(self, message):
        self.refresh_live_phase_timer()
        self._support_event("error", message)
        self._finish_persisted_session("Safety stop: " + message)
        self.set_controls_running(False)
        self.set_connection_state("Safety stop", "error")
        self.status_label.setText(message)
        QMessageBox.critical(self, "Pokebot3DS-CFW", message)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(2500)
        self._finish_persisted_session("Application closed")
        event.accept()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Pokebot3DS-CFW")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    window = MainWindow()

    screen = app.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()

        # The user's 1280x800-class desktop exposes roughly 777 px of usable
        # vertical work area. Keep enough room for the native Windows frame.
        target_w = min(1220, max(900, available.width() - 24))
        target_h = min(700, max(560, available.height() - 28))
        window.resize(target_w, target_h)

        x = available.x() + max(0, (available.width() - target_w) // 2)
        y = available.y() + max(0, (available.height() - target_h) // 2)
        window.move(x, y)
    else:
        window.resize(1100, 680)

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
