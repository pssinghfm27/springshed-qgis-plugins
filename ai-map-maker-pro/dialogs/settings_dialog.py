"""Settings dialog: API keys, default model, and plugin behaviour toggles."""
from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QVBoxLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QDialogButtonBox, QPushButton,
    QSpinBox, QMessageBox, QWidget,
)

from ..compat import C
from .. import settings_store as S
from ..utils import cache


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Mapper — Settings")
        self.setMinimumWidth(560)

        form = QFormLayout()

        # ----- API key -----
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(C.ECHO_PASSWORD)
        self.api_key_edit.setPlaceholderText("sk-ant-…")
        self.api_key_edit.setText(S.get_api_key())
        reveal_btn = QPushButton("Show")
        reveal_btn.setCheckable(True)
        reveal_btn.toggled.connect(self._toggle_reveal)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(reveal_btn)
        key_wrap = _wrap_hbox(key_row)
        form.addRow("Anthropic API key:", key_wrap)
        form.addRow(QLabel(
            "<i>Get one free at "
            "<a href='https://console.anthropic.com/settings/keys'>"
            "console.anthropic.com/settings/keys</a></i>"
        ))

        # ----- OpenTopography key -----
        self.otopo_edit = QLineEdit()
        self.otopo_edit.setEchoMode(C.ECHO_PASSWORD)
        self.otopo_edit.setPlaceholderText("your OpenTopography API key (optional)")
        self.otopo_edit.setText(S.get_value("opentopography_key", "", type_=str) or "")
        form.addRow("OpenTopography key:", self.otopo_edit)
        form.addRow(QLabel(
            "<i>Needed for true DEM/SRTM/ALOS downloads. "
            "Free at "
            "<a href='https://portal.opentopography.org/myopentopo'>"
            "portal.opentopography.org/myopentopo</a></i>"
        ))

        # ----- Default model -----
        self.model_combo = QComboBox()
        for key, label in S.AVAILABLE_MODELS:
            self.model_combo.addItem(label, key)
        self._select_in_combo(self.model_combo, S.get_model())
        form.addRow("Planning model:", self.model_combo)

        # ----- Vision model -----
        self.vision_combo = QComboBox()
        for key, label in S.AVAILABLE_MODELS:
            self.vision_combo.addItem(label, key)
        self._select_in_combo(self.vision_combo, S.get_vision_model())
        form.addRow("Vision (map scan) model:", self.vision_combo)

        # ----- Behaviour toggles -----
        self.cache_cb = QCheckBox("Enable prompt caching (reduces API cost by ~90% after first call)")
        self.cache_cb.setChecked(S.get_bool(S.USE_PROMPT_CACHING, True))
        form.addRow(self.cache_cb)

        self.fallback_cb = QCheckBox(
            "Auto-expand bbox and retry when an OSM layer returns 0 features")
        self.fallback_cb.setChecked(S.get_bool(S.AUTO_FALLBACK, True))
        form.addRow(self.fallback_cb)

        self.cache_dl_cb = QCheckBox("Cache downloaded Natural Earth / tiles on disk")
        self.cache_dl_cb.setChecked(S.get_bool(S.CACHE_DOWNLOADS, True))
        form.addRow(self.cache_dl_cb)

        self.overpass_timeout = QSpinBox()
        self.overpass_timeout.setRange(30, 600)
        self.overpass_timeout.setSingleStep(30)
        self.overpass_timeout.setSuffix(" s")
        self.overpass_timeout.setValue(S.get_int(S.OVERPASS_TIMEOUT, 120))
        form.addRow("Overpass query timeout:", self.overpass_timeout)

        # ----- Clear cache button -----
        clear_btn = QPushButton("Clear download cache now")
        clear_btn.clicked.connect(self._clear_cache)
        form.addRow(clear_btn)

        # ----- OK / Cancel -----
        if hasattr(QDialogButtonBox, "StandardButton"):
            btn_flags = (QDialogButtonBox.StandardButton.Ok
                         | QDialogButtonBox.StandardButton.Cancel)
        else:
            btn_flags = QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        buttons = QDialogButtonBox(btn_flags)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

    # -------------------------------------------------------------------

    def _toggle_reveal(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(C.ECHO_NORMAL if checked else C.ECHO_PASSWORD)

    def _clear_cache(self) -> None:
        n = cache.clear()
        QMessageBox.information(self, "Cache cleared",
                                f"Removed {n} cached files.")

    def _on_save(self) -> None:
        S.set_api_key(self.api_key_edit.text())
        S.set_value("opentopography_key", self.otopo_edit.text().strip())
        S.set_model(self.model_combo.currentData())
        S.set_vision_model(self.vision_combo.currentData())
        S.set_value(S.USE_PROMPT_CACHING, self.cache_cb.isChecked())
        S.set_value(S.AUTO_FALLBACK, self.fallback_cb.isChecked())
        S.set_value(S.CACHE_DOWNLOADS, self.cache_dl_cb.isChecked())
        S.set_value(S.OVERPASS_TIMEOUT, self.overpass_timeout.value())
        self.accept()

    @staticmethod
    def _select_in_combo(combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return


def _wrap_hbox(layout):
    w = QWidget()
    w.setLayout(layout)
    return w
