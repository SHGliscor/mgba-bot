#!/usr/bin/env python3
"""Pokebot3DS-CFW modular Gen 3 application.

v0p24 keeps the verified v0p21 HF1 starter files frozen and retains the
HF8 Wild Spin battle path while adding the complete Ruby/Sapphire world/Hunt
layer, live encounter modifiers, Party Viewer, sprites, and Wurmple branches.
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMessageBox,
    QTableWidgetItem,
    QHeaderView,
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QPushButton,
    QTableWidget, QCheckBox, QSizePolicy, QFrame,
)

from ruby_starter_ui import MainWindow as FrozenStarterMainWindow
from support_export import export_support_bundle
from gen3bot.modes.registry import MODES as WILD_MODES
from gen3bot.modes.wild_spin import WildSpinWorker
from gen3bot.data.hunt_policy import HuntPolicy, STATES as HUNT_STATES
from gen3bot.data.rs_world import wurmple_evolution
from gen3bot.data.sprite_assets import (
    path_for as sprite_asset_path, background_sync_all, asset_counts,
    EXPECTED_PER_VARIANT, pack_complete,
)
from gen3bot.ui.live_state import WorldStatePoller
from gen3bot.ui.shiny_sound import play_shiny_sound

APP_TITLE = "Pokebot3DS-CFW — Gen 3 Bot v0p24 RS World Hunt"
STARTER_CHOICES = ("Torchic", "Treecko", "Mudkip", "Random")


class Gen3MainWindow(FrozenStarterMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self._starter_choice = "Torchic"
        self._wild_choice = "Spin"
        self._hunt_mode = "Starters"
        self._wild_row_by_encounter = {}
        self.hunt_policy = HuntPolicy()
        self._live_state = None
        self._hunt_seen = {}
        self._live_poller = None
        self._world_payload = None
        self._world_by_key = {}

        # Preserve the frozen starter log columns 0..12 and append wild-only
        # metadata at the end, so starter rendering remains byte-for-byte logic.
        self.table.setColumnCount(16)
        self.table.setHorizontalHeaderLabels([
            "#", "Sprite", "Pokémon", "HP", "ATK", "DEF", "SPA", "SPD", "SPE",
            "Σ", "SV", "Nature", "Result", "Encounter %", "Hunt", "Evolution"
        ])
        self.shiny_table.setColumnCount(18)
        self.shiny_table.setHorizontalHeaderLabels([
            "Seen", "Sprite", "#", "Pokémon", "PID", "HP", "ATK", "DEF",
            "SPA", "SPD", "SPE", "Σ", "SV", "Nature", "Ability", "Hidden Power",
            "Encounter %", "Evolution"
        ])

        # Result is an outcome field, not a classification field.  Give it
        # enough room to show values such as "Ran Away" without ellipsis.
        self.table.horizontalHeader().setSectionResizeMode(12, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(12, 105)

        self._install_mode_selector()
        self._install_hunt_tab()
        self._install_party_tab()
        self._apply_mode_ui("Starters")
        self.ip_edit.editingFinished.connect(self._restart_live_reader)
        self._restart_live_reader()
        # Populate the complete 40Cakes normal/shiny/anti-shiny sprite pack in
        # the background. Hunting never waits on this UI-only download.
        background_sync_all()
        self._sprite_status_timer = QTimer(self)
        self._sprite_status_timer.setInterval(5000)
        self._sprite_status_timer.timeout.connect(self._refresh_sprite_status)
        self._sprite_status_timer.start()
        self._refresh_sprite_status()

        subtitle = self.findChild(QLabel, "appSubtitle")
        if subtitle is not None:
            subtitle.setText(
                "GEN 3 BOT   •   RUBY / SAPPHIRE v1.0–v1.1   •   STARTERS FROZEN   •   MODULAR WILD"
            )

    # ------------------------------------------------------------------
    # Dashboard mode selector
    # ------------------------------------------------------------------
    def _install_mode_selector(self):
        controls_body = self.starter_combo.parentWidget()
        grid = controls_body.layout()

        starter_label = None
        battle_label = None
        for label in controls_body.findChildren(QLabel):
            if label.text() == "Starter":
                starter_label = label
            elif label.text() == "Battle":
                battle_label = label

        if starter_label is None or battle_label is None:
            raise RuntimeError("Could not locate frozen Dashboard hunt-control labels.")

        self.mode_label = starter_label
        self.battle_label = battle_label
        self.mode_label.setText("Mode")

        # Insert a real top-level Mode selector where the old Starter selector
        # lived, then keep the frozen starter_combo as the dynamic second field.
        grid.removeWidget(self.starter_combo)
        grid.removeWidget(self.battle_label)
        grid.removeWidget(self.battle_combo)
        grid.removeWidget(self.ff_check)
        grid.removeWidget(self.rng_guard_check)
        grid.removeWidget(self.start_btn)
        grid.removeWidget(self.stop_btn)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Starters", "Wild Encounters"])
        self.selection_label = QLabel("Starter")

        grid.addWidget(self.mode_combo, 1, 1)
        grid.addWidget(self.selection_label, 2, 0)
        grid.addWidget(self.starter_combo, 2, 1)
        grid.addWidget(self.battle_label, 3, 0)
        grid.addWidget(self.battle_combo, 3, 1)
        grid.addWidget(self.ff_check, 4, 0, 1, 2)
        grid.addWidget(self.rng_guard_check, 5, 0, 1, 2)
        grid.addWidget(self.start_btn, 6, 0)
        grid.addWidget(self.stop_btn, 6, 1)

        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.starter_combo.currentTextChanged.connect(self._remember_selection)

    def _remember_selection(self, text):
        if self._hunt_mode == "Starters":
            self._starter_choice = text
        else:
            self._wild_choice = text

    def _set_combo_items(self, combo, values, selected):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(list(values))
        if selected in values:
            combo.setCurrentText(selected)
        combo.blockSignals(False)

    def _on_mode_changed(self, mode):
        if self.worker and self.worker.isRunning():
            # Mode is disabled during a hunt, but keep this defensive guard.
            return
        self._apply_mode_ui(mode)

    def _apply_mode_ui(self, mode):
        self._hunt_mode = mode
        wild = mode == "Wild Encounters"

        if wild:
            self.selection_label.setText("Method")
            method_names = tuple(WILD_MODES.keys())
            self._set_combo_items(self.starter_combo, method_names, self._wild_choice)
            self.battle_label.setText("Non-shiny")
            self._set_combo_items(self.battle_combo, ("Run Away",), "Run Away")
            self.battle_combo.setEnabled(False)
            self.rng_guard_check.setVisible(False)
            self.stat_unique.label.setText("Unique Generations")
            self.stat_dup.label.setText("Repeated Generations")
            self.stat_unique_rate.label.setText("Effective / hr")
            self.seed_rng.setText(
                "Continuous wild RNG   •   starter generation gate not armed"
            )
            self.status_label.setText(
                "Wild Encounters selected — choose a method from the Dashboard."
            )
        else:
            self.selection_label.setText("Starter")
            self._set_combo_items(self.starter_combo, STARTER_CHOICES, self._starter_choice)
            self.battle_label.setText("Battle")
            self._set_combo_items(self.battle_combo, ("Skip battle", "View battle"), "Skip battle")
            self.battle_combo.setEnabled(True)
            self.rng_guard_check.setVisible(True)
            self.stat_unique.label.setText("Unique PIDs")
            self.stat_dup.label.setText("Duplicate PIDs")
            self.stat_unique_rate.label.setText("Unique / hr")
            self.status_label.setText(
                "Starters selected — frozen v0p21 HF1 starter path."
            )

    def set_controls_running(self, running):
        super().set_controls_running(running)
        self.mode_combo.setEnabled(not running)
        if self._hunt_mode == "Wild Encounters":
            self.battle_combo.setEnabled(False)
            self.rng_guard_check.setEnabled(False)

    # ------------------------------------------------------------------
    # Unified start/stop dispatch
    # ------------------------------------------------------------------
    def start_hunt(self):
        if self.worker and self.worker.isRunning():
            return
        if self._hunt_mode == "Wild Encounters":
            self._start_wild_hunt()
        else:
            # The frozen starter UI/backend remains the authority for starters.
            super().start_hunt()

    def stop_hunt(self):
        if self.worker and self.worker.isRunning():
            self.status_label.setText("Stopping…")
            self.worker.request_stop()

    def _reset_dashboard_for_new_hunt(self):
        self.table.setRowCount(0)
        self._wild_row_by_encounter.clear()
        self._hunt_seen.clear()
        self.rng_decision_table.setRowCount(0)
        self.recent.clear()
        self._last_rng_event = {}
        self._last_stats_elapsed = 0.0
        self.stat_elapsed.value.setText("00:00:00")
        self.stat_enc.value.setText("0")
        self.stat_effective.value.setText("0")
        self.stat_unique.value.setText("0")
        self.stat_dup.value.setText("0")
        self.stat_rate.value.setText("0")
        self.stat_unique_rate.value.setText("0")
        self.odds_bar.setValue(0)
        self.odds_bar.setFormat("0 effective / 8192")
        self.chance_label.setText("Estimated shiny chance: 0.00%")
        self.eta_label.setText("ETA to 8192: —")
        self.sprite_label.clear()
        self.sprite_variant.setText("—")
        self.shiny_badge.setText("NORMAL")
        self.duplicate.setText("")
        for label in (
            self.iv_hp, self.iv_atk, self.iv_def,
            self.iv_spa, self.iv_spd, self.iv_spe,
            self.iv_sum, self.nature, self.sv,
        ):
            label.setText("—")
        self.gender.setText("Gender —")
        self.ability.setText("Ability —")
        self.hp.setText("Hidden Power —")

    def _start_wild_hunt(self):
        method = self.starter_combo.currentText()
        self._wild_choice = method
        method_info = WILD_MODES.get(method) or {}
        if not method_info.get("implemented"):
            QMessageBox.information(
                self,
                "Wild Encounters",
                f"{method} is exposed in the modular Mode selector but is not implemented yet.\n\n"
                "Spin is the current hardware-test method.",
            )
            return

        ip = self.ip_edit.text().strip()
        fast = self.ff_check.isChecked()

        phase = self.stats_store.prepare_phase_for_hunt()
        self.refresh_phase_records(phase)
        self.stats_store.begin_session({
            "ip": ip,
            "mode": "Wild Encounters",
            "method": method,
            "fast_forward": fast,
            "non_shiny_action": "Run Away",
            "shiny_action": "Hold",
            "game": "Auto-detect Ruby/Sapphire",
            "game_code": "AXVE/AXPE",
            "revision": "00/01",
        })
        self._session_active = True
        self.refresh_lifetime_stats()
        self._reset_dashboard_for_new_hunt()

        self.set_connection_state("Connecting…", "connecting")
        self.status_label.setText(f"Wild {method}: connecting to mGBA bridge…")
        self.set_controls_running(True)

        if method == "Spin":
            self.worker = WildSpinWorker(ip, fast_forward=fast, shiny_sound=self.shiny_sound_check.isChecked())
        else:  # Registry currently marks only Spin implemented.
            raise RuntimeError(f"No worker registered for wild method: {method}")

        self.worker.connected.connect(self._on_wild_connected)
        self.worker.encounter.connect(self._on_wild_encounter)
        self.worker.outcome.connect(self._on_wild_outcome)
        self.worker.stats.connect(self._on_wild_stats)
        self.worker.log.connect(self._on_wild_log)
        self.worker.stopped.connect(self._on_wild_stopped)
        self.worker.error.connect(self._on_wild_error)
        self.worker.start()

    # ------------------------------------------------------------------
    # Wild mode -> existing Dashboard adapters
    # ------------------------------------------------------------------
    def sprite_info(self, mon):
        species = mon.get("species") or mon.get("starter") or "Unknown"
        if mon.get("shiny"):
            variant, label = "shiny", "SHINY"
        elif int(mon.get("sv", 0) or 0) >= 65528:
            variant, label = "anti-shiny", "ANTI-SHINY"
        else:
            variant, label = "normal", "NORMAL"
        return sprite_asset_path(species, variant), variant, label

    def _on_wild_connected(self, info):
        self._support_event("wild_connected", info)
        game_text = (
            f"{info.get('name', 'Pokémon')} {info.get('code', '')} "
            f"v{info.get('version', '?')} rev {int(info.get('revision', 0)):02X}"
        ).strip()
        map_text = f"{info.get('map_name', 'map')} {info.get('map_group')}/{info.get('map_num')}"
        self.set_connection_state("Connected", "connected")
        self.game_label.setText(f"Game: {game_text}  •  {map_text}")
        self.status_label.setText(
            f"Wild {self.starter_combo.currentText()} running — {game_text} — {map_text}"
        )
        self.rng_gate_tile.value.setText("Not armed (wild)")
        self.rng_delay_tile.value.setText("Continuous RNG")
        self.seed_rng.setText(
            "Continuous wild RNG   •   starter generation gate not armed   •   non-shiny RUN"
        )

    def _set_wild_shiny_badge(self, mon):
        if mon.get("shiny"):
            self.shiny_badge.setText("SHINY")
            self.shiny_badge.setStyleSheet(
                "padding: 5px 10px; border-radius: 8px; "
                "background: #443811; border: 1px solid #a98a2e; color: #ffe58a; font-weight: 900;"
            )
            self.sv.setStyleSheet("color: #63d297; font-size: 13px; font-weight: 800;")
        elif int(mon.get("sv", 0)) >= 65528:
            self.shiny_badge.setText("ANTI-SHINY")
            self.shiny_badge.setStyleSheet(
                "padding: 5px 10px; border-radius: 8px; "
                "background: #3d2444; border: 1px solid #81528d; color: #efb4fa; font-weight: 900;"
            )
            self.sv.setStyleSheet("color: #e19af0; font-size: 13px; font-weight: 800;")
        else:
            self.shiny_badge.setText("NORMAL")
            self.shiny_badge.setStyleSheet(
                "padding: 5px 10px; border-radius: 8px; "
                "background: #243140; border: 1px solid #3d5062; color: #dce7f2; font-weight: 800;"
            )
            self.sv.setStyleSheet("color: #e7eef4; font-size: 13px; font-weight: 700;")

    def _on_wild_encounter(self, mon):
        self._support_event("wild_encounter", mon)
        self.stats_store.record_encounter(mon)
        phase = self.stats_store.update_phase_records(mon)
        self.refresh_phase_records(phase)
        self.refresh_lifetime_stats()
        self.update_sprite(mon)
        if mon.get("shiny"):
            self.refresh_recent_shinies()

        self.species.setText(mon.get("species", "—"))
        encounter_pct = mon.get("encounter_percent")
        pct_text = "—" if encounter_pct is None else f"{encounter_pct}%"
        self.chosen.setText(
            f"Wild method: {mon.get('method', self._wild_choice)}  •  Species rate: {pct_text}"
        )
        self.pid.setText(f"{int(mon.get('pid', 0)):08X}")

        iv = mon.get("ivs") or {}
        iv_values = [int(iv.get(k, 0)) for k in ("hp", "atk", "def", "spa", "spd", "spe")]
        iv_labels = [self.iv_hp, self.iv_atk, self.iv_def, self.iv_spa, self.iv_spd, self.iv_spe]
        for label, value in zip(iv_labels, iv_values):
            label.setText(str(value))
            if value == 31:
                label.setStyleSheet("color: #63d297; font-size: 13px; font-weight: 800;")
            elif value == 0:
                label.setStyleSheet("color: #ff746d; font-size: 13px; font-weight: 800;")
            else:
                label.setStyleSheet("color: #e7eef4; font-size: 13px; font-weight: 700;")

        self.iv_sum.setText(str(sum(iv_values)))
        self.nature.setText(mon.get("nature", "—"))
        self.sv.setText(str(mon.get("sv", "—")))
        self.gender.setText(f"Gender: {mon.get('gender', '—')}")
        self.ability.setText(
            f"Ability: {mon.get('ability', '—')} (slot {mon.get('ability_slot', '—')})"
        )
        self.hp.setText(
            f"Hidden Power: {mon.get('hidden_power', '—')} {mon.get('hidden_power_power', '')}".strip()
        )
        self._set_wild_shiny_badge(mon)

        if mon.get("repeated_generation"):
            self.duplicate.setText(
                "Repeated species + PID + IV generation — excluded from Effective Rolls"
            )
            self.duplicate.setStyleSheet("color: #ff746d; font-size: 10px;")
        else:
            pct = mon.get("encounter_percent")
            evo = mon.get("wurmple_evolution")
            bits = [f"{mon.get('method', 'Wild')}", f"Lv {mon.get('level', '—')}"]
            if pct is not None: bits.append(f"Encounter {pct}%")
            if evo: bits.append(f"Evolution: {evo}")
            bits.append("non-shiny action: RUN")
            self.duplicate.setText(" • ".join(bits))
            self.duplicate.setStyleSheet("color: #a9adb4; font-size: 10px;")

        row = self.table.rowCount()
        self.table.insertRow(row)
        self._wild_row_by_encounter[int(mon.get("encounter_no", row + 1))] = row
        # Result reports what actually happened to the battle.  Non-shinies
        # remain pending until run_away() and the RAM-confirmed overworld return
        # have both completed.
        result = "Held — Shiny" if mon.get("shiny") else "Run pending…"
        values = [
            str(mon.get("encounter_no", row + 1)), None, mon.get("species", "—"),
            *[str(v) for v in iv_values], str(sum(iv_values)), str(mon.get("sv", "—")),
            mon.get("nature", "—"), result,
        ]
        for col, value in enumerate(values):
            item = self.sprite_item(mon) if col == 1 else QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignCenter)
            if col in (3, 4, 5, 6, 7, 8):
                number = int(value)
                if number == 31:
                    item.setForeground(QColor("#63d297"))
                elif number == 0:
                    item.setForeground(QColor("#ff746d"))
            elif col == 10:
                if mon.get("shiny"):
                    item.setForeground(QColor("#63d297"))
                elif int(mon.get("sv", 0)) >= 65528:
                    item.setForeground(QColor("#e19af0"))
            elif col == 12 and mon.get("shiny"):
                item.setForeground(QColor("#f1c84b"))
            self.table.setItem(row, col, item)
        # Keep classification details available without overloading Result.
        result_item = self.table.item(row, 12)
        if result_item is not None:
            classification = (
                "Shiny" if mon.get("shiny")
                else "Anti-shiny" if int(mon.get("sv", 0)) >= 65528
                else "Repeated generation" if mon.get("repeated_generation")
                else "Normal"
            )
            result_item.setToolTip(f"{classification} encounter • battle outcome")
        pct = mon.get("encounter_percent")
        extra = ["—" if pct is None else f"{pct}%", mon.get("hunt_state", "Allowed"), mon.get("wurmple_evolution") or "—"]
        for col, value in zip((13,14,15), extra):
            item = QTableWidgetItem(str(value)); item.setTextAlignment(Qt.AlignCenter); self.table.setItem(row,col,item)
        encounter_method = mon.get("encounter_method") or "land"
        key=(int(mon.get("map_group",-1)),int(mon.get("map_num",-1)),encounter_method,int(mon.get("species_id",0)))
        self._hunt_seen[key]=self._hunt_seen.get(key,0)+1
        self._refresh_hunt_table()
        self.table.scrollToBottom()

    def _on_wild_outcome(self, payload):
        encounter_no = int(payload.get("encounter_no", 0) or 0)
        row = self._wild_row_by_encounter.get(encounter_no)
        if row is None or row < 0 or row >= self.table.rowCount():
            return
        item = self.table.item(row, 12)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 12, item)
        result = str(payload.get("result") or "—")
        item.setText(result)
        if payload.get("outcome") == "held_shiny":
            item.setForeground(QColor("#f1c84b"))
            item.setToolTip("Shiny encounter • battle held for the user")
        elif payload.get("outcome") == "ran_away":
            item.setForeground(QColor("#a9d7ff"))
            item.setToolTip("Non-shiny encounter • Run succeeded • overworld return confirmed")
        self.table.resizeColumnToContents(12)
        if self.table.columnWidth(12) < 105:
            self.table.setColumnWidth(12, 105)
        self._support_event("wild_outcome", payload)

    def _on_wild_stats(self, stats):
        encounters = int(stats.get("encounters", 0))
        duplicates = int(stats.get("duplicates", 0))
        effective = int(stats.get("effective_rolls", encounters - duplicates))
        payload = dict(stats)
        payload.update({
            "attempts": encounters,
            "unique": effective,
            "unique_rate": float(stats.get("effective_rate", stats.get("rate", 0.0))),
            "rng_repeats_avoided": 0,
            "rng_history_size": 0,
        })
        super().on_stats(payload)

    def _on_wild_log(self, message):
        self._support_event("wild_log", message)
        self.status_label.setText(message)

    def _on_wild_stopped(self, reason):
        self.refresh_live_phase_timer()
        self._support_event("wild_stopped", reason)
        self._finish_persisted_session(reason)
        self.set_controls_running(False)
        self.set_connection_state("Connected / stopped", "stopped")
        self.status_label.setText(reason)
        if reason.startswith("SHINY"):
            QMessageBox.information(self, "Shiny found", reason)

    def _on_wild_error(self, message):
        self.refresh_live_phase_timer()
        self._support_event("wild_error", message)
        self._finish_persisted_session("Safety stop: " + message)
        self.set_controls_running(False)
        self.set_connection_state("Safety stop", "error")
        self.status_label.setText(message)
        QMessageBox.critical(self, "Wild Encounter Safety Stop", message)

    # ------------------------------------------------------------------
    # RS World / Hunt tab + live Party viewer
    # ------------------------------------------------------------------
    @staticmethod
    def _method_key(label):
        return {
            "Land / Grass": "land", "Surf": "water", "Rock Smash": "rock_smash",
            "Old Rod": "old_rod", "Good Rod": "good_rod", "Super Rod": "super_rod",
        }.get(label, "land")

    def _install_hunt_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); layout.setContentsMargins(10,10,10,10); layout.setSpacing(8)
        summary = QGroupBox("Ruby / Sapphire world database")
        sg = QGridLayout(summary)
        self.hunt_route = QLabel("Route: waiting for RAM…")
        self.hunt_game = QLabel("Game: —")
        self.hunt_base_rate = QLabel("Base encounter: —")
        self.hunt_effective_rate = QLabel("Effective encounter: —")
        self.hunt_illuminate = QLabel("Illuminate: —")
        self.hunt_flute = QLabel("White Flute: —")
        self.hunt_bike = QLabel("Bike: —")
        self.hunt_world_status = QLabel("World DB: waiting…")
        self.hunt_boundary = QLabel("Terrain boundary: —")
        self.hunt_sprite_status = QLabel("Sprite assets: checking…")
        for i,w in enumerate((self.hunt_game,self.hunt_route,self.hunt_base_rate,self.hunt_effective_rate,self.hunt_illuminate,self.hunt_flute,self.hunt_bike,self.hunt_boundary,self.hunt_world_status,self.hunt_sprite_status)):
            sg.addWidget(w, i//2, i%2)
        layout.addWidget(summary)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Map"))
        self.hunt_map_combo = QComboBox()
        self.hunt_map_combo.addItem("Current map (auto)", None)
        self.hunt_map_combo.setMinimumWidth(210)
        self.hunt_map_combo.currentIndexChanged.connect(lambda _index: self._refresh_hunt_table())
        controls.addWidget(self.hunt_map_combo)
        controls.addWidget(QLabel("Encounter method"))
        self.hunt_method_combo = QComboBox(); self.hunt_method_combo.addItems(["Land / Grass","Surf","Rock Smash","Old Rod","Good Rod","Super Rod"])
        self.hunt_method_combo.currentTextChanged.connect(self._on_hunt_method_changed); controls.addWidget(self.hunt_method_combo)
        self.shiny_sound_check = QCheckBox("Shiny sound"); self.shiny_sound_check.setChecked(True); controls.addWidget(self.shiny_sound_check)
        test_sound = QPushButton("Test sound"); test_sound.clicked.connect(play_shiny_sound); controls.addWidget(test_sound)
        sync_sprites = QPushButton("Sync sprites")
        sync_sprites.clicked.connect(lambda: background_sync_all())
        controls.addWidget(sync_sprites)
        rebuild = QPushButton("Rebuild world DB"); rebuild.clicked.connect(self._request_world_rebuild); controls.addWidget(rebuild)
        controls.addStretch(1); layout.addLayout(controls)

        self.hunt_table = QTableWidget(0, 6)
        self.hunt_table.setHorizontalHeaderLabels(["Sprite","Pokémon","Encounter %","Levels","Seen","State"])
        self.hunt_table.setSelectionBehavior(QTableWidget.SelectRows); self.hunt_table.setAlternatingRowColors(True)
        self.hunt_table.verticalHeader().setVisible(False); self.hunt_table.setIconSize(self.table.iconSize())
        self.hunt_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for c in (0,2,3,4,5): self.hunt_table.horizontalHeader().setSectionResizeMode(c,QHeaderView.ResizeToContents)
        layout.addWidget(self.hunt_table,1)
        hint=QLabel("Multiple Pokémon can be Target at once. Blocked species never override shiny safety: every shiny still HOLDs.")
        hint.setWordWrap(True); layout.addWidget(hint)
        self.tabs.insertTab(1, tab, "Hunt")

    def _install_party_tab(self):
        tab=QWidget(); outer=QVBoxLayout(tab); outer.setContentsMargins(10,10,10,10); outer.setSpacing(8)
        title=QLabel("Live Party — RAM refreshes while idle and during hunts"); title.setStyleSheet("font-weight: 800; font-size: 14px;"); outer.addWidget(title)
        grid=QGridLayout(); grid.setSpacing(8); self.party_cards=[]
        for i in range(6):
            box=QGroupBox(f"Slot {i+1}"); v=QVBoxLayout(box)
            sprite=QLabel("—"); sprite.setAlignment(Qt.AlignCenter); sprite.setMinimumHeight(72)
            name=QLabel("Empty"); name.setAlignment(Qt.AlignCenter); name.setStyleSheet("font-weight: 800;")
            detail=QLabel("—"); detail.setWordWrap(True); detail.setAlignment(Qt.AlignTop)
            v.addWidget(sprite); v.addWidget(name); v.addWidget(detail,1)
            grid.addWidget(box,i//3,i%3); self.party_cards.append((sprite,name,detail))
        outer.addLayout(grid,1); self.tabs.insertTab(2,tab,"Party")

    def _refresh_sprite_status(self):
        if not hasattr(self, "hunt_sprite_status"):
            return
        counts = asset_counts()
        text = " • ".join(f"{name} {counts.get(name,0)}/{EXPECTED_PER_VARIANT}" for name in ("normal","shiny","anti-shiny"))
        prefix = "Sprite assets: COMPLETE" if pack_complete() else "Sprite assets: syncing"
        self.hunt_sprite_status.setText(f"{prefix} • {text}")

    def _restart_live_reader(self):
        old=self._live_poller
        if old is not None and old.isRunning(): old.request_stop(); old.wait(800)
        ip=self.ip_edit.text().strip()
        self._live_poller=WorldStatePoller(ip,self._method_key(getattr(self,'hunt_method_combo',QComboBox()).currentText() if hasattr(self,'hunt_method_combo') else 'Land / Grass'))
        self._live_poller.state.connect(self._on_live_state)
        self._live_poller.world_ready.connect(self._on_world_ready)
        self._live_poller.log.connect(lambda m: None)
        self._live_poller.start()

    def _request_world_rebuild(self):
        if self._live_poller: self._live_poller.request_world_rebuild(); self.hunt_world_status.setText("World DB: rebuilding…")

    def _on_hunt_method_changed(self, _text):
        if self._live_poller: self._live_poller.set_method(self._method_key(self.hunt_method_combo.currentText()))
        self._refresh_hunt_table()

    def _on_world_ready(self, info):
        self.hunt_world_status.setText(f"World DB: {info.get('maps',0)} wild maps cached • {info.get('path','')}")
        payload = info.get("world") or {}
        self._world_payload = payload
        self._world_by_key = {
            (int(row.get("map_group", -1)), int(row.get("map_num", -1))): row
            for row in (payload.get("maps") or [])
        }
        current = self.hunt_map_combo.currentData() if hasattr(self, "hunt_map_combo") else None
        if hasattr(self, "hunt_map_combo"):
            self.hunt_map_combo.blockSignals(True)
            self.hunt_map_combo.clear()
            self.hunt_map_combo.addItem("Current map (auto)", None)
            for key, row in sorted(self._world_by_key.items(), key=lambda item: (item[0][0], item[0][1])):
                label = f"{row.get('map_name', f'Map {key[0]}/{key[1]}')}  [{key[0]}/{key[1]}]"
                self.hunt_map_combo.addItem(label, key)
            if current is not None:
                for index in range(self.hunt_map_combo.count()):
                    if self.hunt_map_combo.itemData(index) == current:
                        self.hunt_map_combo.setCurrentIndex(index)
                        break
            self.hunt_map_combo.blockSignals(False)
        self._refresh_hunt_table()

    def _on_live_state(self, state):
        self._live_state=state
        prof=state.get("profile") or {}; ps=state.get("player") or {}
        self.hunt_game.setText(f"Game: {prof.get('name','—')} v{prof.get('version','—')} rev {int(prof.get('revision',0)):02X}")
        self.hunt_route.setText(f"Route: {ps.get('map_name','—')} ({ps.get('map_group','—')}/{ps.get('map_num','—')})")
        bp=state.get("base_percent"); ep=state.get("effective_percent")
        self.hunt_base_rate.setText("Base encounter: —" if bp is None else f"Base encounter: {bp:.2f}% per check")
        self.hunt_effective_rate.setText("Effective encounter: —" if ep is None else f"Effective encounter: {ep:.2f}% per check")
        self.hunt_illuminate.setText("Illuminate: ACTIVE ×2" if state.get("illuminate") else "Illuminate: off")
        self.hunt_flute.setText("White Flute: ACTIVE ×1.5" if state.get("white_flute") else "White Flute: off")
        self.hunt_bike.setText("Bike: ACTIVE ×0.8" if state.get("bike") else "Bike: off")
        terrain=state.get("terrain") or {}; cur=terrain.get("current") or {}; edges=terrain.get("edges") or {}
        if cur:
            valid=[d for d,v in edges.items() if v]
            self.hunt_boundary.setText(f"Terrain boundary: {'encounter tile' if cur.get('encounter') else 'NON-encounter tile'} • safe edges: {', '.join(valid) if valid else 'none'}")
        else:
            self.hunt_boundary.setText("Terrain boundary: —")
        self._refresh_hunt_table(); self._refresh_party_cards(state.get("party") or [])

    def _hunt_table_context(self):
        state = self._live_state or {}
        profile = state.get("profile") or {}
        live_player = state.get("player") or {}
        method = self._method_key(self.hunt_method_combo.currentText())
        selected = self.hunt_map_combo.currentData() if hasattr(self, "hunt_map_combo") else None
        if selected is None:
            if state.get("method") != method:
                if self._live_poller:
                    self._live_poller.set_method(method)
                return profile, live_player, method, []
            return profile, live_player, method, list(state.get("species") or [])

        group, num = int(selected[0]), int(selected[1])
        row = self._world_by_key.get((group, num)) or {}
        map_context = {
            "map_group": group,
            "map_num": num,
            "map_name": row.get("map_name", f"Map {group}/{num}"),
        }
        methods = row.get("methods") or {}
        if method in ("old_rod", "good_rod", "super_rod"):
            species = ((methods.get("fishing") or {}).get("groups") or {}).get(method, [])
        else:
            species = (methods.get(method) or {}).get("species", [])
        return profile, map_context, method, list(species or [])

    def _refresh_hunt_table(self):
        if not self._live_state or not hasattr(self, "hunt_table"):
            return
        prof, ps, method, species = self._hunt_table_context()
        selected = self.hunt_map_combo.currentData() if hasattr(self, "hunt_map_combo") else None
        if selected is not None:
            self.hunt_route.setText(
                f"Browsing: {ps.get('map_name','—')} ({ps.get('map_group','—')}/{ps.get('map_num','—')})"
            )
        else:
            live_ps = (self._live_state or {}).get("player") or {}
            self.hunt_route.setText(
                f"Route: {live_ps.get('map_name','—')} ({live_ps.get('map_group','—')}/{live_ps.get('map_num','—')})"
            )

        self.hunt_table.blockSignals(True)
        self.hunt_table.setRowCount(0)
        for rec in sorted(species, key=lambda r: (-int(r.get("percent", 0)), str(r.get("species", "")))):
            row = self.hunt_table.rowCount()
            self.hunt_table.insertRow(row)
            sid = int(rec["species_id"])
            name = rec.get("species", f"Species {sid}")
            icon = self.sprite_item({"species": name, "sv": 0, "shiny": False})
            self.hunt_table.setItem(row, 0, icon)
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, sid)
            self.hunt_table.setItem(row, 1, name_item)
            self.hunt_table.setItem(row, 2, QTableWidgetItem(f"{int(rec.get('percent', 0))}%"))
            minimum, maximum = int(rec.get("min_level", 0)), int(rec.get("max_level", 0))
            levels = f"Lv {minimum}" if minimum == maximum else f"Lv {minimum}–{maximum}"
            self.hunt_table.setItem(row, 3, QTableWidgetItem(levels))
            seen = self._hunt_seen.get(
                (int(ps.get("map_group", -1)), int(ps.get("map_num", -1)), method, sid), 0
            )
            self.hunt_table.setItem(row, 4, QTableWidgetItem(str(seen)))
            combo = QComboBox()
            combo.addItems(list(HUNT_STATES))
            current = self.hunt_policy.state(
                prof.get("code", "RS"), ps.get("map_group", 0), ps.get("map_num", 0), method, sid
            )
            combo.setCurrentText(current)
            combo.currentTextChanged.connect(
                lambda value, sid=sid, prof=dict(prof), ps=dict(ps), method=method:
                self.hunt_policy.set_state(
                    prof.get("code", "RS"), ps.get("map_group", 0), ps.get("map_num", 0), method, sid, value
                )
            )
            self.hunt_table.setCellWidget(row, 5, combo)
        self.hunt_table.blockSignals(False)

    def _refresh_party_cards(self, party):
        for i,(sprite,name,detail) in enumerate(self.party_cards):
            if i>=len(party): sprite.clear(); sprite.setText("—"); name.setText("Empty"); detail.setText("—"); continue
            mon=party[i]; name.setText(f"{mon.get('species','—')}  Lv {mon.get('level','—')}")
            path=sprite_asset_path(mon.get('species','Unknown'),'shiny' if mon.get('shiny') else ('anti-shiny' if int(mon.get('sv',0))>=65528 else 'normal'))
            pix=QPixmap(str(path));
            if not pix.isNull(): sprite.setPixmap(pix.scaled(72,72,Qt.KeepAspectRatio,Qt.FastTransformation))
            else: sprite.clear(); sprite.setText("?")
            iv=mon.get('ivs') or {}; ev=mon.get('evs') or {}; st=mon.get('stats') or {}; evo=mon.get('wurmple_evolution')
            lines=[f"HP {mon.get('hp','—')}/{mon.get('max_hp','—')} • {mon.get('status','Healthy')}",f"{mon.get('gender','—')} • {mon.get('nature','—')} • {mon.get('ability','—')}",f"Held: {mon.get('held_item','None')}",f"IVs {iv.get('hp','—')}/{iv.get('atk','—')}/{iv.get('def','—')}/{iv.get('spa','—')}/{iv.get('spd','—')}/{iv.get('spe','—')}",f"Stats A{st.get('atk','—')} D{st.get('def','—')} SA{st.get('spa','—')} SD{st.get('spd','—')} Sp{st.get('spe','—')}",f"Pokérus: {mon.get('pokerus','None')} • SV {mon.get('sv','—')}"]
            if evo: lines.append(f"Evolution: {evo}")
            detail.setText("\n".join(lines))

    def refresh_recent_shinies(self):
        super().refresh_recent_shinies()
        if not hasattr(self, "shiny_table"):
            return
        rows = self.stats_store.recent_shinies()
        for row, rec in enumerate(rows):
            if row >= self.shiny_table.rowCount():
                break
            pct = rec.get("encounter_percent")
            evo = rec.get("wurmple_evolution")
            if not evo and int(rec.get("species_id", 0) or 0) == 290:
                pid_value = rec.get("pid", 0)
                try:
                    pid_value = int(pid_value) if isinstance(pid_value, int) else int(str(pid_value).replace("0x", ""), 16)
                except Exception:
                    pid_value = 0
                evo = wurmple_evolution(pid_value, 290)
            self.shiny_table.setItem(row, 16, QTableWidgetItem("—" if pct is None else f"{pct}%"))
            self.shiny_table.setItem(row, 17, QTableWidgetItem(evo or "—"))

    # ------------------------------------------------------------------
    # Support export now records the active Dashboard mode.
    # ------------------------------------------------------------------
    def export_support(self):
        settings = {
            "ip": self.ip_edit.text().strip(),
            "mode": self.mode_combo.currentText(),
            "selection": self.starter_combo.currentText(),
            "fast_forward": self.ff_check.isChecked(),
            "battle_or_non_shiny": self.battle_combo.currentText(),
            "avoid_reused_rng": self.rng_guard_check.isChecked(),
            "connection_badge": self.connection_badge.text(),
            "status": self.status_label.text(),
            "game": self.game_label.text(),
            "starter_frozen_base": "v0p21 HF1 / v0p19 generation-aware RNG core",
            "hunt_method": self.hunt_method_combo.currentText() if hasattr(self, "hunt_method_combo") else None,
            "hunt_browse_map": self.hunt_map_combo.currentText() if hasattr(self, "hunt_map_combo") else None,
            "shiny_sound": self.shiny_sound_check.isChecked() if hasattr(self, "shiny_sound_check") else True,
            "live_world_state": self._live_state,
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
                f"Support package created:\n\n{path}\n\nSend this ZIP with the issue report.",
            )
        except Exception as exc:
            self.status_label.setText(f"Support export failed: {exc}")
            QMessageBox.critical(self, "Export Support", str(exc))


    def closeEvent(self, event):
        poller = getattr(self, "_live_poller", None)
        if poller is not None and poller.isRunning():
            poller.request_stop()
            poller.wait(1500)
        parent_close = getattr(super(), "closeEvent", None)
        if callable(parent_close):
            parent_close(event)
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Pokebot3DS-CFW")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    window = Gen3MainWindow()

    screen = app.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
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
