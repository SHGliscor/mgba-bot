#!/usr/bin/env python3
"""Wild Encounters tab plug-in for the current Pokebot3DS-CFW UI."""
import math
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QProgressBar, QPlainTextEdit,
)

from gen3bot.core.bridge import DEFAULT_IP
from gen3bot.modes.wild_spin import WildSpinWorker, ODDS


def fmt_elapsed(seconds):
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def fmt_eta(hours):
    if not math.isfinite(hours):
        return "—"
    if hours <= 0:
        return "Reached"
    total = int(hours * 3600)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"


class WildEncountersTab(QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.worker = None
        self.session_active = False
        self._last_elapsed = 0.0
        self._last_profile = None
        self.root_dir = Path(__file__).resolve().parents[2]

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._refresh_timer)
        self.timer.start()

    def is_running(self):
        return bool(self.worker and self.worker.isRunning())

    def support_snapshot(self):
        return {
            "running": self.is_running(),
            "method": self.method.currentText(),
            "target": self.target.currentText(),
            "ip": self.ip_edit.currentText().strip(),
            "fast_forward": self.fast.isChecked(),
            "connection": self.connection.text(),
            "game": self.game_label.text(),
            "map": self.map_label.text(),
            "position": self.position.text(),
            "last_species": self.current_species.text(),
            "last_pid": self.current_pid.text(),
            "last_sv": self.current_sv.text(),
            "last_result": self.current_result.text(),
            "encounter_rows": self.table.rowCount(),
        }

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(6, 6, 6, 6)
        main.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(7)

        controls = QGroupBox("Wild Hunt")
        grid = QGridLayout(controls)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.method = QComboBox()
        self.method.addItem("Spin")
        self.target = QComboBox()
        self.target.addItem("Any Shiny")
        self.ip_edit = QComboBox()
        self.ip_edit.setEditable(True)
        self.ip_edit.addItem(DEFAULT_IP)
        self.fast = QCheckBox("mGBA fast-forward")
        self.fast.setChecked(True)
        self.start = QPushButton("Start Wild Hunt")
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)

        grid.addWidget(QLabel("Method"), 0, 0)
        grid.addWidget(self.method, 0, 1)
        grid.addWidget(QLabel("Target"), 1, 0)
        grid.addWidget(self.target, 1, 1)
        grid.addWidget(QLabel("3DS IP"), 2, 0)
        grid.addWidget(self.ip_edit, 2, 1)
        grid.addWidget(self.fast, 3, 0, 1, 2)
        action_note = QLabel("Non-shiny: RUN  •  Shiny: HOLD battle + FF off")
        action_note.setObjectName("sectionNote")
        grid.addWidget(action_note, 4, 0, 1, 2)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        grid.addLayout(buttons, 5, 0, 1, 2)
        top.addWidget(controls, 2)

        state = QGroupBox("Live State")
        sgrid = QGridLayout(state)
        sgrid.setVerticalSpacing(4)
        self.connection = QLabel("Disconnected")
        self.game_label = QLabel("—")
        self.map_label = QLabel("—")
        self.position = QLabel("—")
        self.current_species = QLabel("—")
        self.current_pid = QLabel("—")
        self.current_sv = QLabel("—")
        self.current_result = QLabel("—")
        for row, (name, widget) in enumerate([
            ("Bridge", self.connection),
            ("Game", self.game_label),
            ("Map", self.map_label),
            ("Position", self.position),
            ("Last Pokémon", self.current_species),
            ("PID", self.current_pid),
            ("SV", self.current_sv),
            ("Result", self.current_result),
        ]):
            label = QLabel(name)
            label.setObjectName("sectionNote")
            sgrid.addWidget(label, row, 0)
            sgrid.addWidget(widget, row, 1)
        top.addWidget(state, 3)

        module = QGroupBox("Module Status")
        ml = QVBoxLayout(module)
        frozen = QLabel("STARTERS FROZEN")
        frozen.setObjectName("smallChip")
        frozen.setAlignment(Qt.AlignCenter)
        ml.addWidget(frozen)
        module_text = QLabel(
            "Spin: hardware test build\n"
            "Ruby/Sapphire v1.0 + v1.1\n\n"
            "Next module after Spin passes:\n"
            "Acro Bike Bunny Hop"
        )
        module_text.setWordWrap(True)
        ml.addWidget(module_text)
        ml.addStretch()
        top.addWidget(module, 2)

        main.addLayout(top)

        stats_box = QGroupBox("Session / Shiny Phase")
        stats = QGridLayout(stats_box)
        self.elapsed = QLabel("00:00:00")
        self.encounters = QLabel("0")
        self.effective = QLabel("0")
        self.duplicates = QLabel("0")
        self.rate = QLabel("0/hr")
        self.chance = QLabel("0.000%")
        self.eta = QLabel("—")
        self.spin_inputs = QLabel("0")
        values = [
            ("Phase timer", self.elapsed),
            ("Encounters", self.encounters),
            ("Effective rolls", self.effective),
            ("Exact repeats", self.duplicates),
            ("Rate", self.rate),
            ("Cumulative shiny chance", self.chance),
            ("ETA to 8192 effective", self.eta),
            ("Spin inputs", self.spin_inputs),
        ]
        for i, (name, widget) in enumerate(values):
            row, col = divmod(i, 4)
            note = QLabel(name)
            note.setObjectName("sectionNote")
            stats.addWidget(note, row * 2, col)
            widget.setStyleSheet("font-size: 16px; font-weight: 800;")
            stats.addWidget(widget, row * 2 + 1, col)

        self.odds = QProgressBar()
        self.odds.setRange(0, ODDS)
        self.odds.setValue(0)
        self.odds.setFormat("0 effective / 8192")
        stats.addWidget(self.odds, 4, 0, 1, 4)
        main.addWidget(stats_box)

        table_box = QGroupBox("Wild Encounter Log — full current session")
        table_layout = QVBoxLayout(table_box)
        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels([
            "#", "Sprite", "Pokémon", "Lv", "PID", "IVs H/A/D/SA/SD/S",
            "Σ", "SV", "Nature", "Ability", "Hidden Power", "Result",
        ])
        self.table.setIconSize(QSize(40, 40))
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        for col in (2, 5, 11):
            header.setSectionResizeMode(col, QHeaderView.Stretch)
        table_layout.addWidget(self.table)
        main.addWidget(table_box, 1)

        log_box = QGroupBox("Wild Mode Log")
        log_layout = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setMaximumHeight(92)
        log_layout.addWidget(self.log)
        main.addWidget(log_box)

        self.start.clicked.connect(self.start_hunt)
        self.stop.clicked.connect(self.stop_hunt)

    def _sprite_info(self, mon):
        species = mon.get("species") or "Unknown"
        if mon.get("shiny"):
            variant, label = "shiny", "SHINY"
        elif int(mon.get("sv", 0) or 0) >= 65528:
            variant, label = "anti-shiny", "ANTI-SHINY"
        else:
            variant, label = "normal", "NORMAL"
        return self.root_dir / "assets" / "pokemon" / variant / f"{species}.png", label

    def _sprite_item(self, mon):
        path, label = self._sprite_info(mon)
        item = QTableWidgetItem()
        if path.exists():
            item.setIcon(QIcon(str(path)))
        else:
            item.setText("?")
        item.setToolTip(label)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def _set_running(self, running):
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.method.setEnabled(not running)
        self.target.setEnabled(not running)
        self.ip_edit.setEnabled(not running)
        self.fast.setEnabled(not running)

    def _reset_display(self):
        self.table.setRowCount(0)
        self._last_elapsed = 0.0
        self.elapsed.setText("00:00:00")
        self.encounters.setText("0")
        self.effective.setText("0")
        self.duplicates.setText("0")
        self.rate.setText("0/hr")
        self.chance.setText("0.000%")
        self.eta.setText("—")
        self.spin_inputs.setText("0")
        self.odds.setValue(0)
        self.odds.setFormat("0 effective / 8192")
        self.current_species.setText("—")
        self.current_pid.setText("—")
        self.current_sv.setText("—")
        self.current_result.setText("—")
        self.log.clear()

    def start_hunt(self):
        if self.is_running():
            return
        if self.main_window.worker and self.main_window.worker.isRunning():
            QMessageBox.warning(
                self, "Wild Encounters",
                "Stop the starter hunt before starting a wild hunt."
            )
            return

        self._reset_display()
        ip = self.ip_edit.currentText().strip()
        settings = {
            "ip": ip,
            "hunt_type": "Wild Encounters",
            "method": "Spin",
            "target": "Any Shiny",
            "fast_forward": self.fast.isChecked(),
            "non_shiny_action": "Run Away",
            "shiny_action": "Hold",
        }

        phase = self.main_window.stats_store.prepare_phase_for_hunt()
        self.main_window.refresh_phase_records(phase)
        self.main_window.stats_store.begin_session(settings)
        self.session_active = True
        self.main_window.refresh_lifetime_stats()

        self.connection.setText("Connecting…")
        self.main_window.set_connection_state("Connecting…", "connecting")
        self.main_window.status_label.setText("Wild Spin: connecting to mGBA bridge…")
        self._set_running(True)

        self.worker = WildSpinWorker(ip, fast_forward=settings["fast_forward"])
        self.worker.connected.connect(self._on_connected)
        self.worker.encounter.connect(self._on_encounter)
        self.worker.stats.connect(self._on_stats)
        self.worker.log.connect(self._on_log)
        self.worker.stopped.connect(self._on_stopped)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def stop_hunt(self):
        if self.is_running():
            self.stop.setEnabled(False)
            self.worker.request_stop()

    def _finish_session(self, status):
        if self.session_active:
            self.main_window.stats_store.finish_session(status)
            self.session_active = False
            self.main_window.refresh_lifetime_stats()
            self.main_window.refresh_recent_shinies()

    def _on_connected(self, info):
        self._last_profile = dict(info)
        self.main_window._support_event("wild_connected", info)
        self.connection.setText("Connected")
        game_text = (
            f"{info.get('name', 'Pokémon')} {info.get('code', '')} "
            f"v{info.get('version', '?')} rev {int(info.get('revision', 0)):02X}"
        ).strip()
        self.game_label.setText(game_text)
        self.map_label.setText(
            f"{info.get('map_name', '—')} ({info.get('map_group')}/{info.get('map_num')})"
        )
        self.position.setText(
            f"{info.get('x', '—')}, {info.get('y', '—')} • facing {info.get('facing', '—')}"
        )
        self.main_window.game_label.setText(game_text)
        self.main_window.set_connection_state("Connected", "connected")
        self.main_window.status_label.setText(
            f"Wild Spin running — {game_text} — {info.get('map_name', 'map')}"
        )
        self._on_log(
            f"Connected: {game_text}. Spin uses one-frame direction inputs, "
            "RAM battle detection and stationary drift checks."
        )

    def _on_encounter(self, mon):
        self.main_window._support_event("wild_encounter", mon)
        self.main_window.stats_store.record_encounter(mon)
        phase = self.main_window.stats_store.update_phase_records(mon)
        self.main_window.refresh_phase_records(phase)
        self.main_window.refresh_lifetime_stats()
        if mon.get("shiny"):
            self.main_window.refresh_recent_shinies()

        self.current_species.setText(mon.get("species", "—"))
        self.current_pid.setText(f"{int(mon.get('pid', 0)):08X}")
        self.current_sv.setText(str(mon.get("sv", "—")))

        if mon.get("shiny"):
            result = "SHINY — HOLD"
        elif int(mon.get("sv", 0)) >= 65528:
            result = "ANTI-SHINY — RUN"
        elif mon.get("duplicate"):
            result = "REPEAT — RUN"
        else:
            result = "NORMAL — RUN"
        self.current_result.setText(result)

        row = self.table.rowCount()
        self.table.insertRow(row)
        iv = mon.get("ivs") or {}
        ivs = [int(iv.get(k, 0)) for k in ("hp", "atk", "def", "spa", "spd", "spe")]
        hp_text = f"{mon.get('hidden_power', '—')} {mon.get('hidden_power_power', '')}".strip()
        values = [
            str(mon.get("encounter_no", row + 1)),
            None,
            mon.get("species", "—"),
            str(mon.get("level", "—")),
            f"{int(mon.get('pid', 0)):08X}",
            "/".join(str(v) for v in ivs),
            str(sum(ivs)),
            str(mon.get("sv", "—")),
            mon.get("nature", "—"),
            mon.get("ability", "—"),
            hp_text,
            result,
        ]
        for col, value in enumerate(values):
            item = self._sprite_item(mon) if col == 1 else QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, col, item)
        self.table.scrollToBottom()

    def _on_stats(self, stats):
        self.main_window._support_event("wild_stats", stats)
        self._last_elapsed = float(stats.get("elapsed", self._last_elapsed))
        self.elapsed.setText(fmt_elapsed(self._last_elapsed))
        self.encounters.setText(str(stats.get("encounters", 0)))
        self.effective.setText(str(stats.get("effective_rolls", 0)))
        self.duplicates.setText(str(stats.get("duplicates", 0)))
        self.rate.setText(f"{float(stats.get('rate', 0.0)):.1f}/hr")
        self.chance.setText(f"{float(stats.get('cumulative', 0.0)):.3f}%")
        self.eta.setText(fmt_eta(float(stats.get("eta_hours", math.inf))))
        self.spin_inputs.setText(str(stats.get("spin_inputs", 0)))
        effective = int(stats.get("effective_rolls", 0))
        self.odds.setValue(min(ODDS, effective))
        self.odds.setFormat(f"{effective} effective / 8192")

        payload = dict(stats)
        payload["attempts"] = int(stats.get("encounters", 0))
        payload["unique"] = max(
            0, int(stats.get("encounters", 0)) - int(stats.get("duplicates", 0))
        )
        payload["unique_rate"] = float(stats.get("effective_rate", 0.0))
        payload["rng_repeats_avoided"] = 0
        payload["rng_history_size"] = 0
        self.main_window.stats_store.update_session_stats(payload)

    def _on_log(self, message):
        self.main_window._support_event("wild_log", message)
        stamp = time.strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{stamp}] {message}")

    def _on_stopped(self, message):
        self._on_log(message)
        self._set_running(False)
        self.connection.setText("Stopped")
        self.main_window.set_connection_state("Stopped", "stopped")
        self.main_window.status_label.setText(message)
        self._finish_session(message)
        if "SHINY" in message:
            QMessageBox.information(self, "Wild Encounters", message)

    def _on_error(self, message):
        self._on_log("ERROR: " + message)
        self._set_running(False)
        self.connection.setText("Safety stop")
        self.main_window.set_connection_state("Safety stop", "error")
        self.main_window.status_label.setText("Wild safety stop: " + message)
        self._finish_session("Safety stop: " + message)
        QMessageBox.critical(self, "Wild Encounter Safety Stop", message)

    def _refresh_timer(self):
        if self.is_running() and self.worker.started_at is not None:
            self._last_elapsed = max(0.0, time.monotonic() - self.worker.started_at)
            self.elapsed.setText(fmt_elapsed(self._last_elapsed))
