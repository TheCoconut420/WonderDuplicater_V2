#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# settings and settings tab

import os
import json

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QRegExp, QTimer, QModelIndex
from PyQt5.QtGui import (
    QFont, QRegExpValidator, QStandardItemModel, QStandardItem,
    QKeySequence, QDragEnterEvent, QDropEvent, QTextCursor, QColor
)


# Implementation details.

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "actor_duplication_settings.json")
LANGUAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "language_stuff.json")
BFRES_RENAMER_EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "BfresRenamer.exe")

DEFAULT_SETTINGS = {
    "language": "en",
    "theme": "dark",
    "font_family": "Segoe UI",
    "font_size": 11,
    "window_geometry": "1100x750",
    "last_source": "",
    "last_dest": "",
    "actorinfo_path": "",
    "gameactorinfo_path": "",
    "rstb_exe_path": "",
    "rstbl_path": "",
    "tab_order": ["clone", "import_actor", "editor", "settings", "actor_wizard"],
    "editor_splitter": [300, 500],
    "clone_splitter": [300, 200],
    "import_actor_splitter": [200, 400],
    "main_splitter": [400, 100],
    "clone_checkboxes": {
        "copy_pack": True,
        "copy_bfres": True,
        "rename_bfres_internals": False,
        "copy_animation": False,
        "adjust_actor_engine": True,
        "adjust_modelinfo_refs": True,
        "adjust_rsdb_entries": True,
        "adjust_rstbl": False
    },
    "import_actor_checkboxes": {
        "adjust_actor_engine": True,
        "adjust_modelinfo_refs": True
    },
    "enable_clone_log": False,
    "batch_mode": False,
    "open_editor_tabs": []
}

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        for k, v in DEFAULT_SETTINGS.items():
            if k not in loaded:
                loaded[k] = v
        if "clone_checkboxes" in loaded:
            for ck, cv in DEFAULT_SETTINGS["clone_checkboxes"].items():
                loaded["clone_checkboxes"].setdefault(ck, cv)
        if "import_actor_checkboxes" in loaded:
            for ck, cv in DEFAULT_SETTINGS["import_actor_checkboxes"].items():
                loaded["import_actor_checkboxes"].setdefault(ck, cv)
        valid_tabs = DEFAULT_SETTINGS["tab_order"]
        saved_order = loaded.get("tab_order", [])
        loaded["tab_order"] = [tab for tab in saved_order if tab in valid_tabs]
        loaded["tab_order"] += [tab for tab in valid_tabs if tab not in loaded["tab_order"]]
        return loaded
    except:
        return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

def load_texts(language_code):
    try:
        with open(LANGUAGE_FILE, "r", encoding="utf-8") as f:
            all_texts = json.load(f)
        english_texts = all_texts.get("en", {})
        selected_texts = all_texts.get(language_code, english_texts)
        return {**english_texts, **selected_texts}
    except:
        return {}


# Implementation details.
class SettingsTab(QWidget):
    def __init__(self, settings, texts, main_window):
        super().__init__()
        self.settings = settings
        self.texts = texts
        self.main = main_window

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.src_edit = QLineEdit(settings.get("last_source", ""))
        self.src_edit.textChanged.connect(lambda t: self.update_path("last_source", t, self.src_edit, "source"))
        self.src_edit.setToolTip(texts.get("source_dir_tooltip", ""))
        src_row = QHBoxLayout()
        src_row.addWidget(self.src_edit)
        src_row.addWidget(QPushButton(texts.get("browse_btn", "..."), clicked=lambda: self.browse_folder("last_source", self.src_edit)))
        form.addRow(texts.get("source_label", "Source RomFS:"), src_row)

        self.dst_edit = QLineEdit(settings.get("last_dest", ""))
        self.dst_edit.textChanged.connect(lambda t: self.update_path("last_dest", t, self.dst_edit, "dest"))
        self.dst_edit.setToolTip(texts.get("dest_dir_tooltip", ""))
        dst_row = QHBoxLayout()
        dst_row.addWidget(self.dst_edit)
        dst_row.addWidget(QPushButton(texts.get("browse_btn", "..."), clicked=lambda: self.browse_folder("last_dest", self.dst_edit)))
        form.addRow(texts.get("dest_label", "Mod RomFS:"), dst_row)

        self.actorinfo_edit = QLineEdit(settings.get("actorinfo_path", ""))
        self.actorinfo_edit.textChanged.connect(lambda t: self.update_path("actorinfo_path", t, self.actorinfo_edit, "actorinfo"))
        self.actorinfo_edit.setToolTip(texts.get("actorinfo_tooltip", ""))
        row = QHBoxLayout()
        row.addWidget(self.actorinfo_edit)
        row.addWidget(QPushButton(texts.get("browse_btn", "..."), clicked=lambda: self.browse_rsdb("actorinfo")))
        self.actorinfo_editor_btn = QPushButton(texts.get("open_editor_btn", "Editor"))
        self.actorinfo_editor_btn.clicked.connect(lambda: self.main.open_path_in_editor(self.actorinfo_edit.text()))
        row.addWidget(self.actorinfo_editor_btn)
        form.addRow(texts.get("actorinfo_label", "ActorInfo...:"), row)

        self.gameactorinfo_edit = QLineEdit(settings.get("gameactorinfo_path", ""))
        self.gameactorinfo_edit.textChanged.connect(lambda t: self.update_path("gameactorinfo_path", t, self.gameactorinfo_edit, "gameactorinfo"))
        self.gameactorinfo_edit.setToolTip(texts.get("gameactorinfo_tooltip", ""))
        row = QHBoxLayout()
        row.addWidget(self.gameactorinfo_edit)
        row.addWidget(QPushButton(texts.get("browse_btn", "..."), clicked=lambda: self.browse_rsdb("gameactorinfo")))
        self.gameactorinfo_editor_btn = QPushButton(texts.get("open_editor_btn", "Editor"))
        self.gameactorinfo_editor_btn.clicked.connect(lambda: self.main.open_path_in_editor(self.gameactorinfo_edit.text()))
        row.addWidget(self.gameactorinfo_editor_btn)
        form.addRow(texts.get("gameactorinfo_label", "GameActorInfo...:"), row)

        self.rstb_edit = QLineEdit(settings.get("rstb_exe_path", ""))
        self.rstb_edit.textChanged.connect(lambda t: self.update_path("rstb_exe_path", t, self.rstb_edit, "rstb"))
        self.rstb_edit.setToolTip(texts.get("rstb_exe_tooltip", ""))
        row = QHBoxLayout()
        row.addWidget(self.rstb_edit)
        row.addWidget(QPushButton(texts.get("browse_btn", "..."), clicked=self.browse_rstb))
        form.addRow(texts.get("rstb_exe_label", "Wonder RSTB Gen.exe:"), row)

        self.rstbl_edit = QLineEdit(settings.get("rstbl_path", ""))
        self.rstbl_edit.textChanged.connect(lambda t: self.update_path("rstbl_path", t, self.rstbl_edit, "rstbl"))
        self.rstbl_edit.setToolTip(texts.get("rstbl_tooltip", ""))
        row = QHBoxLayout()
        row.addWidget(self.rstbl_edit)
        row.addWidget(QPushButton(texts.get("browse_btn", "..."), clicked=lambda: self.browse_rstbl()))
        self.rstbl_editor_btn = QPushButton(texts.get("open_editor_btn", "Editor"))
        self.rstbl_editor_btn.clicked.connect(lambda: self.main.open_path_in_editor(self.rstbl_edit.text()))
        row.addWidget(self.rstbl_editor_btn)
        form.addRow(texts.get("rstbl_label", "RSTBL...:"), row)

        layout.addLayout(form)

        group = QGroupBox(texts.get("settings_group", "General"))
        gen_form = QFormLayout()

        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Deutsch", "English"])
        self.lang_combo.setCurrentText("Deutsch" if settings["language"]=="de" else "English")
        self.lang_combo.currentIndexChanged.connect(self.on_language_changed)
        gen_form.addRow(texts.get("language", "Language"), self.lang_combo)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems([texts.get("dark", "Dark"), texts.get("light", "Light")])
        self.theme_combo.setCurrentText(texts.get(settings["theme"], "Dark"))
        self.theme_combo.currentIndexChanged.connect(self.on_theme_changed)
        gen_form.addRow(texts.get("theme", "Theme"), self.theme_combo)

        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)
        self.font_combo.addItems(["Segoe UI","Arial","Courier New","Times New Roman","Verdana"])
        self.font_combo.setCurrentText(settings["font_family"])
        self.font_combo.currentTextChanged.connect(self.on_font_changed)
        gen_form.addRow(texts.get("font", "Font"), self.font_combo)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(8,30)
        self.size_spin.setValue(settings["font_size"])
        self.size_spin.valueChanged.connect(self.on_font_size_changed)
        gen_form.addRow(texts.get("font_size", "Font Size"), self.size_spin)

        self.batch_cb = QCheckBox(texts.get("batch_mode", "Batch Mode"))
        self.batch_cb.setChecked(settings.get("batch_mode", False))
        self.batch_cb.toggled.connect(self.on_batch_toggled)
        self.batch_cb.setToolTip(texts.get("batch_mode_tooltip", ""))
        gen_form.addRow(self.batch_cb)

        self.log_cb = QCheckBox(texts.get("enable_clone_log", "Enable Clone Log"))
        self.log_cb.setChecked(settings.get("enable_clone_log", False))
        self.log_cb.toggled.connect(self.on_log_toggled)
        self.log_cb.setToolTip(texts.get("enable_clone_log_tooltip", ""))
        gen_form.addRow(self.log_cb)

        group.setLayout(gen_form)
        layout.addWidget(group)

        self.validate_all()
        self.update_editor_buttons_state()

    def tr(self, key, *args):
        return self.texts.get(key, key).format(*args)

    def update_editor_buttons_state(self):
        for edit, btn in [(self.actorinfo_edit, self.actorinfo_editor_btn),
                          (self.gameactorinfo_edit, self.gameactorinfo_editor_btn),
                          (self.rstbl_edit, self.rstbl_editor_btn)]:
            path = edit.text().strip()
            ok = os.path.isfile(path)
            btn.setEnabled(ok)
            btn.setToolTip(self.tr("open_editor_btn") if ok else self.tr("editor_btn_missing"))

    def browse_folder(self, key, edit):
        folder = QFileDialog.getExistingDirectory(self, self.tr("select_folder_dialog_title"))
        if folder:
            edit.setText(folder)

    def browse_rsdb(self, which):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("select_rsdb_dialog_title"), "",
                                              self.tr("file_filter_byml"))
        if path:
            if which == "actorinfo":
                self.actorinfo_edit.setText(path)
            else:
                self.gameactorinfo_edit.setText(path)

    def browse_rstb(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("select_rstb_exe_dialog_title"), "",
                                              self.tr("file_filter_exe"))
        if path:
            self.rstb_edit.setText(path)

    def browse_rstbl(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("select_rstbl_dialog_title"), "",
                                              self.tr("file_filter_byml"))
        if path:
            self.rstbl_edit.setText(path)

    def update_path(self, key, value, edit, context):
        self.settings[key] = value
        save_settings(self.settings)
        self.validate_path(edit, context)
        if key == "last_source":
            self.main.clone_tab.on_source_changed()
        if key == "last_dest":
            self.main.clone_tab.on_name_changed()
            self.main.clone_tab.on_source_changed()
            if getattr(self.main, "actor_wizard_tab", None) is not None:
                self.main.actor_wizard_tab.load_actors()
            if getattr(self.main, "import_actor_tab", None) is not None:
                self.main.import_actor_tab.refresh_paths()
        if key == "rstb_exe_path":
            self.main.clone_tab.update_rstb_button_state()
        if key in ("actorinfo_path", "gameactorinfo_path"):
            if getattr(self.main, "import_actor_tab", None) is not None:
                self.main.import_actor_tab.refresh_paths()
        if key in ("actorinfo_path", "gameactorinfo_path", "rstbl_path"):
            self.update_editor_buttons_state()

    def validate_path(self, edit, context):
        path = edit.text().strip()
        if not path:
            edit.setStyleSheet("")
            return
        if context == "source":
            valid = os.path.isdir(path) and os.path.isdir(os.path.join(path, "Pack", "Actor"))
        elif context == "dest":
            valid = os.path.isdir(path)
        elif context in ("actorinfo", "gameactorinfo", "rstbl"):
            valid = os.path.isfile(path) and path.lower().endswith(".byml.zs")
        elif context == "rstb":
            valid = os.path.isfile(path) and path.lower().endswith(".exe")
        else:
            valid = False
        color = "#2e7d32" if valid else "#b71c1c"
        edit.setStyleSheet(f"QLineEdit {{ background-color: {color}; }}")

    def validate_all(self):
        self.validate_path(self.src_edit, "source")
        self.validate_path(self.dst_edit, "dest")
        self.validate_path(self.actorinfo_edit, "actorinfo")
        self.validate_path(self.gameactorinfo_edit, "gameactorinfo")
        self.validate_path(self.rstb_edit, "rstb")
        self.validate_path(self.rstbl_edit, "rstbl")
        self.update_editor_buttons_state()

    def on_language_changed(self, idx):
        lang = "de" if self.lang_combo.currentText() == "Deutsch" else "en"
        self.settings["language"] = lang
        save_settings(self.settings)
        self.main.apply_theme_and_language()

    def on_theme_changed(self, idx):
        if idx < 0:
            return
        theme = "dark" if idx == 0 else "light"
        self.settings["theme"] = theme
        save_settings(self.settings)
        self.main.apply_theme_and_language()

    def on_font_changed(self, family):
        self.settings["font_family"] = family
        save_settings(self.settings)
        self.main.apply_font()

    def on_font_size_changed(self, size):
        self.settings["font_size"] = size
        save_settings(self.settings)
        self.main.apply_font()

    def on_batch_toggled(self, checked):
        self.settings["batch_mode"] = checked
        save_settings(self.settings)
        self.main.clone_tab.update_batch_mode()

    def on_log_toggled(self, checked):
        self.settings["enable_clone_log"] = checked
        save_settings(self.settings)
        self.main.clone_tab.undo_btn.setVisible(checked)

    def refresh_texts(self, texts):
        self.texts = texts
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        self.theme_combo.addItems([texts.get("dark", "Dark"), texts.get("light", "Light")])
        self.theme_combo.setCurrentText(texts.get(self.settings["theme"], "Dark"))
        self.theme_combo.blockSignals(False)
        self.batch_cb.setText(texts.get("batch_mode", "Batch Mode"))
        self.log_cb.setText(texts.get("enable_clone_log", "Enable Clone Log"))
        self.update_editor_buttons_state()
