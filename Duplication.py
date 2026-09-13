#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# actor cloning tools

import io
import os
import json
import re
import shutil
import subprocess
from datetime import datetime

import sarc

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QRegExp, QTimer, QModelIndex
from PyQt5.QtGui import (
    QFont, QRegExpValidator, QStandardItemModel, QStandardItem,
    QKeySequence, QDragEnterEvent, QDropEvent, QTextCursor, QColor
)

from FileHandler import ( # type: ignore
    parse_byml, dump_byml, decompress_zs, compress_and_write_zs,
    byml_copy, byml_set_field, byml_set_field_computed, byml_find_field,
)
from SettingsManager import BFRES_RENAMER_EXE, DEFAULT_SETTINGS, save_settings # type: ignore


# Implementation details.

def adjust_actor_param_modelinfo_ref(data: bytes, old_basename: str, new_basename: str) -> bytes:
    obj = parse_byml(data)
    def compute_new_ref(old_value: str) -> str:
        if "/" in old_value:
            directory = old_value.rsplit("/", 1)[0]
            old_file = old_value.split("/")[-1]
            new_file = old_file.replace(old_basename, new_basename, 1)
            return f"{directory}/{new_file}"
        else:
            return old_value.replace(old_basename, new_basename, 1)
    byml_set_field_computed(obj, "ModelInfoRef", compute_new_ref)
    return dump_byml(obj)

def adjust_model_info(data: bytes, change_fmdb: bool, change_modelproject: bool, new_name: str) -> bytes:
    obj = parse_byml(data)
    if change_fmdb:
        byml_set_field(obj, "FmdbName", new_name)
    if change_modelproject:
        byml_set_field(obj, "ModelProjectName", new_name)
    return dump_byml(obj)

def get_model_info_from_pack(pack_path, actor_name):
    data = decompress_zs(pack_path)
    archive = sarc.SARC(data=data)
    mi_basename = None
    ap_path = f"Actor/{actor_name}.engine__actor__ActorParam.bgyml"
    if ap_path in archive.list_files():
        ap_data = bytes(archive.get_file_data(ap_path))
        ap_obj = parse_byml(ap_data)
        model_info_ref = byml_find_field(ap_obj, "ModelInfoRef")
        if model_info_ref:
            mi_basename = model_info_ref.split("/")[-1] if "/" in model_info_ref else model_info_ref
            if mi_basename.endswith(".gyml") and not mi_basename.endswith(".bgyml"):
                mi_basename = mi_basename[:-len(".gyml")] + ".bgyml"
    if not mi_basename:
        mi_basename = f"{actor_name}.engine__component__ModelInfo.bgyml"
    mi_path = f"Component/ModelInfo/{mi_basename}"
    if mi_path not in archive.list_files():
        for f in archive.list_files():
            if f.startswith("Component/ModelInfo/") and f.endswith(".engine__component__ModelInfo.bgyml"):
                mi_path = f
                mi_basename = f.split("/")[-1]
                break
    if mi_path not in archive.list_files():
        return None
    content = bytes(archive.get_file_data(mi_path))
    obj = parse_byml(content)
    model_project_name = byml_find_field(obj, "ModelProjectName")
    fmdb_name = byml_find_field(obj, "FmdbName")
    if not model_project_name or not fmdb_name:
        return None
    return model_project_name, fmdb_name

def rsdb_clone_entry(data: bytes, id_field: str, old_id: str, new_id: str,
                      change_fmdb=False, change_modelproject=False, new_name=""):
    root = parse_byml(data)
    if not isinstance(root, list):
        raise ValueError("RSDB-Root ist keine Liste")
    for entry in root:
        if isinstance(entry, dict) and entry.get(id_field) == new_id:
            return None, "exists"
    target = None
    for entry in root:
        if isinstance(entry, dict) and entry.get(id_field) == old_id:
            target = entry
            break
    if target is None:
        return None, "not_found"
    new_entry = byml_copy(target)
    new_entry[id_field] = new_id
    if change_fmdb:
        byml_set_field(new_entry, "FmdbName", new_name)
    if change_modelproject:
        byml_set_field(new_entry, "ModelProjectName", new_name)
    new_root = root + [new_entry]
    return dump_byml(new_root), "ok"

def clone_actorinfo_entry(path, old_name, new_name, change_fmdb=False, change_modelproject=False):
    if not os.path.isfile(path):
        return {"status": "file_missing"}
    data = decompress_zs(path)
    new_data, status = rsdb_clone_entry(data, "__RowId", old_name, new_name,
                                        change_fmdb, change_modelproject, new_name)
    if status == "ok":
        compress_and_write_zs(path, new_data)
    return {"status": status}

def clone_gameactorinfo_entry(path, old_name, new_name):
    if not os.path.isfile(path):
        return {"status": "file_missing"}
    data = decompress_zs(path)
    old_rowid = f"Work/Actor/{old_name}.engine__actor__ActorParam.gyml"
    new_rowid = f"Work/Actor/{new_name}.engine__actor__ActorParam.gyml"
    new_data, status = rsdb_clone_entry(data, "__RowId", old_rowid, new_rowid)
    if status == "ok":
        compress_and_write_zs(path, new_data)
    return {"status": status}

def remove_actorinfo_entry(path, row_id):
    if not os.path.isfile(path):
        return False
    data = decompress_zs(path)
    root = parse_byml(data)
    if not isinstance(root, list):
        return False
    new_root = [e for e in root if not (isinstance(e, dict) and e.get("__RowId") == row_id)]
    if len(new_root) == len(root):
        return False
    compress_and_write_zs(path, dump_byml(new_root))
    return True

def remove_gameactorinfo_entry(path, row_id):
    if not os.path.isfile(path):
        return False
    data = decompress_zs(path)
    root = parse_byml(data)
    if not isinstance(root, list):
        return False
    new_root = [e for e in root if not (isinstance(e, dict) and e.get("__RowId") == row_id)]
    if len(new_root) == len(root):
        return False
    compress_and_write_zs(path, dump_byml(new_root))
    return True

# Implementation details.
PROGRAM_DIR = os.path.dirname(os.path.abspath(__file__))
UNDO_DIR = os.path.join(PROGRAM_DIR, "undo")
LEGACY_CLONE_LOG_FILE = "clone_history.json"  # alter Speicherort (in dest_dir), fuer Migration

def _mod_name_from_dest(dest_dir):
    norm = os.path.normpath(dest_dir)
    name = os.path.basename(norm)
    if name.lower() == "romfs":
        parent = os.path.basename(os.path.dirname(norm))
        if parent:
            name = parent
    return name or "mod"

def get_clone_history_path(dest_dir):
    mod_name = _mod_name_from_dest(dest_dir)
    return os.path.join(UNDO_DIR, f"{mod_name}_history.json")

def load_clone_history(dest_dir):
    path = get_clone_history_path(dest_dir)
    if not os.path.isfile(path):
        # Migrate the legacy history file once.
        legacy_path = os.path.join(dest_dir, LEGACY_CLONE_LOG_FILE)
        if os.path.isfile(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                save_clone_history(dest_dir, entries)
                return entries
            except Exception:
                return []
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_clone_history(dest_dir, entries):
    path = get_clone_history_path(dest_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

def append_clone_entry(dest_dir, entry):
    history = load_clone_history(dest_dir)
    history.append(entry)
    save_clone_history(dest_dir, history)

# Implementation details.
class UndoDialog(QDialog):
    def __init__(self, history, dest_dir, settings, texts, parent=None):
        super().__init__(parent)
        self.dest_dir = dest_dir
        self.history = history
        self.settings = settings
        self.texts = texts
        self.setWindowTitle(texts.get("undo_dialog_title", "Undo Clones"))
        self.resize(600, 400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(texts.get("undo_select", "Select clones to undo:")))

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.MultiSelection)
        for entry in history:
            label = f"{entry['original_actor']} -> {entry['new_actor']}  ({entry['timestamp']})"
            self.list.addItem(label)
        layout.addWidget(self.list)

        self.remove_rsdb_cb = QCheckBox(texts.get("undo_remove_rsdb", "Remove RSDB entries"))
        self.remove_rsdb_cb.setToolTip(texts.get("undo_remove_rsdb_tooltip", "Also delete the corresponding entries from ActorInfo/GameActorInfo"))
        layout.addWidget(self.remove_rsdb_cb)

        btn_layout = QHBoxLayout()
        del_btn = QPushButton(texts.get("undo_delete_btn", "Delete Selected"))
        del_btn.clicked.connect(self.delete_selected)
        cancel_btn = QPushButton(texts.get("cancel", "Abbrechen"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(del_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def delete_selected(self):
        selected = [i.row() for i in self.list.selectedIndexes()]
        if not selected:
            QMessageBox.information(self, self.texts.get("info_dialog_title", "Info"),
                                    self.texts.get("undo_info_none", "No entries selected."))
            return

        for idx in sorted(selected, reverse=True):
            entry = self.history[idx]
            pack_full = os.path.join(self.dest_dir, entry["pack_file"])
            model_full = os.path.join(self.dest_dir, entry["model_file"])
            for p in (pack_full, model_full):
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except Exception as e:
                        QMessageBox.warning(self, self.texts.get("error_dialog_title", "Fehler"),
                                            self.texts.get("msg_delete_error", "Konnte {0} nicht löschen: {1}").format(p, e))
            if self.remove_rsdb_cb.isChecked():
                if entry.get("actorinfo_rowid"):
                    remove_actorinfo_entry(self.settings.get("actorinfo_path"), entry["actorinfo_rowid"])
                if entry.get("gameactorinfo_rowid"):
                    remove_gameactorinfo_entry(self.settings.get("gameactorinfo_path"), entry["gameactorinfo_rowid"])
            del self.history[idx]

        save_clone_history(self.dest_dir, self.history)
        QMessageBox.information(self, self.texts.get("info_dialog_title", "Info"),
                                self.texts.get("undo_success", "Selected clones have been deleted."))
        self.accept()

# Implementation details.
class CloneTab(QWidget):
    def __init__(self, settings, texts, log_func, progress_callback, editor_tab=None, main_window=None):
        super().__init__()
        self.settings = settings
        self.texts = texts
        self.log = log_func
        self.progress = progress_callback
        self.editor_tab = editor_tab
        self.main_window = main_window
        self.actors = []

        main_layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Vertical)

        actor_widget = QWidget()
        actor_layout = QVBoxLayout(actor_widget)
        actor_layout.setContentsMargins(0,0,0,0)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(self.tr("source_switch") + ":"))
        self.source_combo = QComboBox()
        self.source_combo.addItem(self.texts.get("clone_source_original", "Original"))
        self.source_combo.addItem(self.texts.get("clone_source_mod", "Mod"))
        self.source_combo.setToolTip(self.tr("switch_source_tooltip"))
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        source_row.addWidget(self.source_combo)
        actor_layout.addLayout(source_row)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(self.tr("search_placeholder")))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("search_placeholder"))
        self.search_edit.textChanged.connect(self.filter_actors)
        search_row.addWidget(self.search_edit)
        actor_layout.addLayout(search_row)

        self.actor_list = QListWidget()
        self.actor_list.currentItemChanged.connect(self.on_select)
        actor_layout.addWidget(self.actor_list)
        self.splitter.addWidget(actor_widget)

        lower_widget = QWidget()
        lower_layout = QVBoxLayout(lower_widget)
        lower_layout.setContentsMargins(0,0,0,0)

        self.name_stacked = QStackedWidget()
        self.single_name_edit = QLineEdit()
        self.single_name_edit.setPlaceholderText(self.tr("new_name_label"))
        self.single_name_edit.textChanged.connect(self.on_name_changed)
        self.name_stacked.addWidget(self.single_name_edit)

        self.batch_edit = QPlainTextEdit()
        self.batch_edit.setPlaceholderText(self.tr("clone_batch_placeholder"))
        self.batch_edit.textChanged.connect(self.on_batch_name_changed)
        self.name_stacked.addWidget(self.batch_edit)
        lower_layout.addWidget(QLabel(self.tr("new_name_label")))
        lower_layout.addWidget(self.name_stacked)

        saved_boxes = settings.get("clone_checkboxes", DEFAULT_SETTINGS["clone_checkboxes"])
        self.cb_copy_pack = QCheckBox(self.tr("clone_copy_pack"))
        self.cb_copy_pack.setChecked(saved_boxes.get("copy_pack", True))
        self.cb_copy_pack.setToolTip(self.tr("clone_copy_pack_tooltip"))
        lower_layout.addWidget(self.cb_copy_pack)

        self.cb_copy_bfres = QCheckBox(self.tr("clone_copy_bfres"))
        self.cb_copy_bfres.setChecked(saved_boxes.get("copy_bfres", True))
        self.cb_copy_bfres.setToolTip(self.tr("clone_copy_bfres_tooltip"))
        lower_layout.addWidget(self.cb_copy_bfres)

        self.cb_rename_bfres_internals = QCheckBox(self.tr("clone_rename_bfres_internals"))
        self.cb_rename_bfres_internals.setChecked(saved_boxes.get("rename_bfres_internals", False))
        self.cb_rename_bfres_internals.setToolTip(self.tr("clone_rename_bfres_internals_tooltip"))
        lower_layout.addWidget(self.cb_rename_bfres_internals)

        self.cb_copy_animation = QCheckBox(self.tr("clone_copy_animation"))
        self.cb_copy_animation.setChecked(saved_boxes.get("copy_animation", False))
        self.cb_copy_animation.setToolTip(self.tr("clone_copy_animation_tooltip"))
        lower_layout.addWidget(self.cb_copy_animation)

        self.cb_actor_engine = QCheckBox(self.tr("check_actor_engine"))
        self.cb_actor_engine.setChecked(saved_boxes.get("adjust_actor_engine", True))
        self.cb_actor_engine.setToolTip(self.tr("tooltip_actor_engine"))
        lower_layout.addWidget(self.cb_actor_engine)

        self.cb_modelinfo_refs = QCheckBox(self.tr("check_modelinfo_refs"))
        self.cb_modelinfo_refs.setChecked(saved_boxes.get("adjust_modelinfo_refs", True))
        self.cb_modelinfo_refs.setToolTip(self.tr("tooltip_modelinfo_refs"))
        lower_layout.addWidget(self.cb_modelinfo_refs)

        self.cb_rsdb_entries = QCheckBox(self.tr("check_rsdb_entries"))
        self.cb_rsdb_entries.setChecked(saved_boxes.get("adjust_rsdb_entries", True))
        self.cb_rsdb_entries.setToolTip(self.tr("tooltip_rsdb_entries"))
        lower_layout.addWidget(self.cb_rsdb_entries)

        for key, checkbox in (
            ("copy_pack", self.cb_copy_pack), ("copy_bfres", self.cb_copy_bfres),
            ("rename_bfres_internals", self.cb_rename_bfres_internals),
            ("copy_animation", self.cb_copy_animation), ("adjust_actor_engine", self.cb_actor_engine),
            ("adjust_modelinfo_refs", self.cb_modelinfo_refs), ("adjust_rsdb_entries", self.cb_rsdb_entries),
        ):
            checkbox.toggled.connect(lambda checked, setting=key: self.save_checkbox(setting, checked))
            checkbox.toggled.connect(lambda: self.on_name_changed())

        btn_row = QHBoxLayout()
        self.clone_btn = QPushButton(self.tr("clone_btn"))
        self.clone_btn.setEnabled(False)
        self.clone_btn.clicked.connect(self.clone_actor)
        btn_row.addWidget(self.clone_btn)

        self.rstb_btn = QPushButton(self.tr("rstb_now"))
        self.rstb_btn.clicked.connect(self.generate_rstb)
        self.update_rstb_button_state()
        btn_row.addWidget(self.rstb_btn)

        self.undo_btn = QPushButton(self.tr("undo_btn"))
        self.undo_btn.clicked.connect(self.show_undo_dialog)
        self.undo_btn.setVisible(self.settings.get("enable_clone_log", False))
        btn_row.addWidget(self.undo_btn)
        lower_layout.addLayout(btn_row)

        self.splitter.addWidget(lower_widget)
        main_layout.addWidget(self.splitter)

        self.restore_splitter()
        self.splitter.splitterMoved.connect(self.save_splitter)
        self.on_source_changed()

    def tr(self, key, *args):
        return self.texts.get(key, key).format(*args)

    def save_checkbox(self, key, value):
        boxes = self.settings.setdefault("clone_checkboxes", {})
        boxes[key] = value
        save_settings(self.settings)

    def restore_splitter(self):
        if hasattr(self.window(), 'settings'):
            self.splitter.setSizes(self.window().settings.get("clone_splitter", [300, 200]))

    def save_splitter(self):
        if hasattr(self.window(), 'settings'):
            self.window().settings["clone_splitter"] = self.splitter.sizes()
            save_settings(self.window().settings)

    def on_source_changed(self):
        idx = self.source_combo.currentIndex()
        if idx == 0:
            src = self.settings.get("last_source", "")
        else:
            src = self.settings.get("last_dest", "")
        pack_dir = os.path.join(src, "Pack", "Actor") if src else ""
        valid = bool(src) and os.path.isdir(pack_dir)

        if not valid:
            self.actor_list.clear()
            self.actor_list.setEnabled(False)
            self.search_edit.setEnabled(False)
            self.single_name_edit.setEnabled(False)
            self.batch_edit.setEnabled(False)
            self.clone_btn.setEnabled(False)
            self.source_combo.setToolTip(self.texts.get("switch_source_tooltip", "") + "\n" + self.tr("no_valid_folder"))
            return

        self.actor_list.setEnabled(True)
        self.search_edit.setEnabled(True)
        self.single_name_edit.setEnabled(True)
        self.batch_edit.setEnabled(True)
        self.source_combo.setToolTip(self.texts.get("switch_source_tooltip", ""))
        self.load_actors(src)

    def load_actors(self, directory=None):
        if directory is None:
            idx = self.source_combo.currentIndex()
            if idx == 0:
                directory = self.settings.get("last_source", "")
            else:
                directory = self.settings.get("last_dest", "")
        if not directory:
            self.actors = []
            self.filter_actors()
            return
        pack_dir = os.path.join(directory, "Pack", "Actor")
        if os.path.isdir(pack_dir):
            self.actors = sorted([f[:-8] for f in os.listdir(pack_dir) if f.endswith(".pack.zs")])
        else:
            self.actors = []
        self.filter_actors()

    def filter_actors(self):
        s = self.search_edit.text().lower()
        self.actor_list.clear()
        for a in self.actors:
            if s in a.lower():
                self.actor_list.addItem(a)

    def on_select(self, curr, prev):
        if curr:
            if self.settings.get("batch_mode"):
                pass
            else:
                self.single_name_edit.setText(curr.text())
            self.clone_btn.setEnabled(True)
            self.update_clone_btn_tooltip()
        else:
            self.clone_btn.setEnabled(False)

    def on_name_changed(self):
        name = self.single_name_edit.text().strip()
        self.validate_name(name)
        self.update_clone_btn_tooltip()

    def on_batch_name_changed(self):
        names = self.get_batch_names()
        all_valid = all(self.is_name_valid(n) for n in names) if names else False
        if names and all_valid:
            self.single_name_edit.setStyleSheet("")
            self.clone_btn.setEnabled(True)
        else:
            self.single_name_edit.setStyleSheet("background-color: #b71c1c;")
            self.clone_btn.setEnabled(False)
        self.update_clone_btn_tooltip()

    def get_batch_names(self):
        text = self.batch_edit.toPlainText().strip()
        if not text:
            return []
        parts = re.split(r'[;\n]', text)
        return [p.strip() for p in parts if p.strip()]

    def is_name_valid(self, name):
        if not name:
            return False
        dest = self.settings.get("last_dest", "")
        if not dest:
            return False
        pack_path = os.path.join(dest, "Pack", "Actor", f"{name}.pack.zs")
        if not os.path.exists(pack_path):
            return True
        return not self.cb_copy_pack.isChecked() and (
            self.cb_actor_engine.isChecked() or self.cb_modelinfo_refs.isChecked()
        )

    def validate_name(self, name):
        valid = self.is_name_valid(name)
        if valid:
            self.single_name_edit.setStyleSheet("")
        else:
            self.single_name_edit.setStyleSheet("background-color: #b71c1c;")
        self.clone_btn.setEnabled(valid)
        self.update_clone_btn_tooltip()

    def update_clone_btn_tooltip(self):
        if self.clone_btn.isEnabled():
            self.clone_btn.setToolTip("")
        else:
            reasons = []
            if not self.actor_list.currentItem():
                reasons.append(self.tr("tooltip_reason_no_actor_selected"))
            else:
                name = self.single_name_edit.text().strip() if not self.settings.get("batch_mode") else self.get_batch_names()
                if not name:
                    reasons.append(self.tr("tooltip_reason_no_name"))
                elif isinstance(name, str) and not self.is_name_valid(name):
                    reasons.append(self.tr("tooltip_reason_name_invalid"))
                elif isinstance(name, list) and not all(self.is_name_valid(n) for n in name):
                    reasons.append(self.tr("tooltip_reason_batch_name_invalid"))
                if not self.settings.get("last_dest"):
                    reasons.append(self.tr("tooltip_reason_no_dest"))
            self.clone_btn.setToolTip("; ".join(reasons) if reasons else self.tr("tooltip_clone_possible"))

    def update_rstb_button_state(self):
        exe = self.settings.get("rstb_exe_path", "")
        ok = bool(exe and os.path.isfile(exe))
        self.rstb_btn.setEnabled(ok)
        if ok:
            self.rstb_btn.setToolTip(self.texts.get("rstb_exe_tooltip", ""))
        else:
            self.rstb_btn.setToolTip(self.texts.get("rstb_missing_tooltip", "RSTB Exe Pfad in Einstellungen angeben"))

    def generate_rstb(self):
        exe = self.settings.get("rstb_exe_path", "")
        if not exe or not os.path.isfile(exe):
            self.log("Failed to start RSTB generator: executable not found", "error")
            return
        dest = self.settings.get("last_dest", "")
        if not dest:
            self.log("Failed to start RSTB generator: mod RomFS path is missing", "error")
            return
        self.log("Starting RSTB generator")
        try:
            subprocess.Popen([exe], cwd=dest)
            self.log("RSTB generator started", "success")
        except Exception as error:
            self.log(f"Failed to start RSTB generator: {error}", "error")

    def update_batch_mode(self):
        batch = self.settings.get("batch_mode", False)
        self.name_stacked.setCurrentIndex(1 if batch else 0)
        if batch:
            self.single_name_edit.clear()
            self.clone_btn.setEnabled(False)
        else:
            self.batch_edit.clear()
            self.on_name_changed()

    def adjust_bfres_model(self, model_path, old_names, new_name):
        if not os.path.isfile(BFRES_RENAMER_EXE):
            return {"status": "error", "message": self.tr("log_bfres_renamer_missing", BFRES_RENAMER_EXE)}
        if isinstance(old_names, (list, tuple, set)):
            names = [n for n in old_names if n]
        else:
            names = [old_names] if old_names else []
        seen = set()
        unique_names = []
        for n in names:
            if n not in seen:
                seen.add(n)
                unique_names.append(n)
        old_names_arg = ",".join(unique_names)
        try:
            result = subprocess.run(
                [BFRES_RENAMER_EXE, model_path, old_names_arg, new_name],
                capture_output=True, text=True, encoding="utf-8-sig", timeout=30
            )
            stdout = result.stdout.strip()
            if not stdout:
                stderr = (result.stderr or "").strip()
                return {"status": "error", "message": f"Keine Ausgabe. Exit-Code {result.returncode}. Stderr: {stderr or '(leer)'}"}
            json_line = None
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    json_line = line
                    break
            if json_line is None:
                return {"status": "error", "message": f"Keine JSON-Zeile gefunden: {stdout!r}"}
            return json.loads(json_line)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def clone_actor(self):
        if self.settings.get("batch_mode"):
            names = self.get_batch_names()
            if not names:
                self.log(self.tr("msg_no_valid_batch_names"))
                return
            self.log(self.tr("log_batch_start", len(names)))
            self.progress(0)
            for i, new_name in enumerate(names):
                if not self.is_name_valid(new_name):
                    self.log(self.tr("msg_skipping_existing").format(new_name))
                    continue
                self.log(self.tr("log_batch_progress", i+1, len(names), self.actor_list.currentItem().text(), new_name))
                self._clone_single(new_name)
                self.progress(int((i+1)/len(names)*100))
            self.progress(100)
            self.log(self.tr("msg_batch_done"))
        else:
            new_name = self.single_name_edit.text().strip()
            if not new_name or not self.is_name_valid(new_name):
                self.log(self.tr("msg_invalid_name"))
                return
            self._clone_single(new_name)

    def _clone_single(self, new_name):
        src_type = self.source_combo.currentIndex()
        if src_type == 0:
            src = self.settings.get("last_source", "")
        else:
            src = self.settings.get("last_dest", "")
        dest = self.settings.get("last_dest", "")
        if not src or not dest:
            self.log("Failed to clone actor: source or mod RomFS path is missing", "error")
            return

        old_name = self.actor_list.currentItem().text()
        src_pack = os.path.join(src, "Pack", "Actor", f"{old_name}.pack.zs")
        if not os.path.isfile(src_pack):
            self.log(f"Failed to clone actor: source pack not found: {src_pack}", "error")
            return

        dest_pack = os.path.join(dest, "Pack", "Actor", f"{new_name}.pack.zs")
        dest_model = os.path.join(dest, "Model", f"{new_name}.bfres.zs")
        old_fmdb_name = old_model_project_name = old_name
        try:
            names = get_model_info_from_pack(src_pack, old_name)
            if names:
                old_model_project_name, old_fmdb_name = names
            else:
                self.log("ModelInfo could not be read; using the actor name for model files", "warning")
        except Exception as error:
            self.log(f"Failed to read ModelInfo: {error}", "warning")

        src_model = os.path.join(src, "Model", f"{old_model_project_name}.bfres.zs")
        src_animation = os.path.join(src, "Model", f"{old_model_project_name}Anm.bfres.zs")
        dest_animation = os.path.join(dest, "Model", f"{new_name}Anm.bfres.zs")
        needs_pack = self.cb_copy_pack.isChecked()
        needs_pack_changes = self.cb_actor_engine.isChecked() or self.cb_modelinfo_refs.isChecked()

        if needs_pack and os.path.exists(dest_pack):
            self.log(f"Failed to copy actor pack: destination already exists: {dest_pack}", "error")
            return
        if not needs_pack and needs_pack_changes and not os.path.isfile(dest_pack):
            self.log(f"Failed to update actor pack: target pack does not exist: {dest_pack}", "error")
            return
        for enabled, source, target, label in (
            (self.cb_copy_bfres.isChecked(), src_model, dest_model, "BFRES file"),
            (self.cb_copy_animation.isChecked(), src_animation, dest_animation, "animation BFRES"),
        ):
            if enabled and not os.path.isfile(source):
                self.log(f"Failed to copy {label}: source file not found: {source}", "error")
                return
            if enabled and os.path.exists(target):
                self.log(f"Failed to copy {label}: destination already exists: {target}", "error")
                return
        if self.cb_rename_bfres_internals.isChecked() and not self.cb_copy_bfres.isChecked() and not os.path.isfile(dest_model):
            self.log(f"Failed to rename internal BFRES names: target file not found: {dest_model}", "error")
            return

        os.makedirs(os.path.dirname(dest_pack), exist_ok=True)
        os.makedirs(os.path.dirname(dest_model), exist_ok=True)
        self.log(f"Starting clone: {old_name} -> {new_name}")
        self.progress(10)
        if needs_pack:
            shutil.copy2(src_pack, dest_pack)
            self.log(f"Copied actor pack to {dest_pack}", "success")
        if self.cb_copy_bfres.isChecked():
            shutil.copy2(src_model, dest_model)
            self.log(f"Copied BFRES file to {dest_model}", "success")
        if self.cb_copy_animation.isChecked():
            shutil.copy2(src_animation, dest_animation)
            self.log(f"Copied animation BFRES to {dest_animation}", "success")
        if self.cb_rename_bfres_internals.isChecked():
            result = self.adjust_bfres_model(dest_model, [old_model_project_name, old_fmdb_name], new_name)
            if result.get("status") == "ok":
                self.log(f"Renamed internal BFRES model names in {dest_model}", "success")
            else:
                self.log(f"Failed to rename internal BFRES model names: {result.get('message', 'unknown error')}", "error")
                return
        self.progress(45)

        if needs_pack_changes:
            try:
                self.adjust_internal_pack(dest_pack, old_name, new_name, old_fmdb_name, old_model_project_name)
                self.log(f"Updated actor pack references in {dest_pack}", "success")
            except Exception as error:
                self.log(f"Failed to update actor pack references: {error}", "error")
                return
        self.progress(75)

        if self.cb_rsdb_entries.isChecked():
            rsdb_msg = self.update_rsdb(old_name, new_name)
            if rsdb_msg:
                for message in rsdb_msg.splitlines():
                    self.log(message, "success" if "success" in message.lower() or "added" in message.lower() else "warning")

        self.progress(100)

        if self.settings.get("enable_clone_log"):
            entry = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "original_actor": old_name,
                "new_actor": new_name,
                "pack_file": f"Pack/Actor/{new_name}.pack.zs",
                "model_file": f"Model/{new_name}.bfres.zs",
                "actorinfo_rowid": new_name if self.cb_rsdb_entries.isChecked() else None,
                "gameactorinfo_rowid": f"Work/Actor/{new_name}.engine__actor__ActorParam.gyml" if self.cb_rsdb_entries.isChecked() else None
            }
            append_clone_entry(dest, entry)

        self.log(f"Clone completed: {new_name}", "success")
        if not self.settings.get("batch_mode"):
            self.single_name_edit.clear()
        if self.source_combo.currentIndex() == 1:
            self.load_actors()
        if self.main_window is not None and hasattr(self.main_window, "refresh_all_directories"):
            self.main_window.refresh_all_directories()

    def adjust_internal_pack(self, pack_path, old_name, new_name, old_fmdb_name=None, old_model_project_name=None):
        do_actor_rename = self.cb_actor_engine.isChecked()
        do_model_refs = self.cb_modelinfo_refs.isChecked()
        if not do_actor_rename and not do_model_refs:
            return

        data = decompress_zs(pack_path)
        archive = sarc.SARC(data=data)
        files = {n: bytes(archive.get_file_data(n)) for n in archive.list_files()}

        old_ap = f"Actor/{old_name}.engine__actor__ActorParam.bgyml"
        mi_basename_old = None
        if old_ap in archive.list_files():
            ap_data_raw = bytes(archive.get_file_data(old_ap))
            ap_obj = parse_byml(ap_data_raw)
            model_info_ref = byml_find_field(ap_obj, "ModelInfoRef")
            if model_info_ref:
                mi_basename_old = model_info_ref.split("/")[-1] if "/" in model_info_ref else model_info_ref
                if mi_basename_old.endswith(".gyml") and not mi_basename_old.endswith(".bgyml"):
                    mi_basename_old = mi_basename_old[:-len(".gyml")] + ".bgyml"
        if not mi_basename_old:
            mi_basename_old = f"{old_name}.engine__component__ModelInfo.bgyml"

        if f"Component/ModelInfo/{mi_basename_old}" not in files:
            for f in files:
                if f.startswith("Component/ModelInfo/") and f.endswith(".engine__component__ModelInfo.bgyml"):
                    mi_basename_old = f.split("/")[-1]
                    break

        if old_name in mi_basename_old:
            mi_basename_new = mi_basename_old.replace(old_name, new_name, 1)
        elif old_model_project_name and old_model_project_name in mi_basename_old:
            mi_basename_new = mi_basename_old.replace(old_model_project_name, new_name, 1)
        elif old_fmdb_name and old_fmdb_name in mi_basename_old:
            mi_basename_new = mi_basename_old.replace(old_fmdb_name, new_name, 1)
        else:
            mi_basename_new = f"{new_name}.engine__component__ModelInfo.bgyml"

        new_ap = f"Actor/{new_name}.engine__actor__ActorParam.bgyml"
        if old_ap in files:
            content = files.pop(old_ap)
            if do_model_refs:
                ref_basename_old = mi_basename_old.replace(".bgyml", ".gyml")
                ref_basename_new = mi_basename_new.replace(".bgyml", ".gyml")
                content = adjust_actor_param_modelinfo_ref(content, ref_basename_old, ref_basename_new)
            files[new_ap if do_actor_rename else old_ap] = content

        old_mi = f"Component/ModelInfo/{mi_basename_old}"
        new_mi = f"Component/ModelInfo/{mi_basename_new}"
        if do_model_refs:
            if old_mi in files:
                content = files.pop(old_mi)
                content = adjust_model_info(content, change_fmdb=True, change_modelproject=True, new_name=new_name)
                files[new_mi] = content
            else:
                self.log(self.tr("log_modelinfo_rename_skipped", old_mi))

        writer = sarc.SARCWriter(be=False)
        for n, d in files.items():
            writer.add_file(n, d)
        out = io.BytesIO()
        writer.write(out)
        compress_and_write_zs(pack_path, out.getvalue())

    def update_rsdb(self, old_name, new_name):
        msgs = []
        change_model = self.cb_modelinfo_refs.isChecked()
        actorinfo_path = self.settings.get("actorinfo_path")
        if actorinfo_path:
            res = clone_actorinfo_entry(actorinfo_path, old_name, new_name,
                                        change_fmdb=change_model, change_modelproject=change_model)
            if res['status'] == 'ok':
                msgs.append(self.tr("log_rsdb_actorinfo_ok"))
            elif res['status'] == 'exists':
                msgs.append(self.tr("log_rsdb_exists", "ActorInfo"))
            elif res['status'] == 'not_found':
                msgs.append(self.tr("log_rsdb_not_found", "ActorInfo"))
            else:
                msgs.append(self.tr("log_rsdb_error", "ActorInfo", res.get('message', '')))
        gameactorinfo_path = self.settings.get("gameactorinfo_path")
        if gameactorinfo_path:
            res = clone_gameactorinfo_entry(gameactorinfo_path, old_name, new_name)
            if res['status'] == 'ok':
                msgs.append(self.tr("log_rsdb_gameactorinfo_ok"))
            elif res['status'] == 'exists':
                msgs.append(self.tr("log_rsdb_exists", "GameActorInfo"))
            elif res['status'] == 'not_found':
                msgs.append(self.tr("log_rsdb_not_found", "GameActorInfo"))
            else:
                msgs.append(self.tr("log_rsdb_error", "GameActorInfo", res.get('message', '')))
        return "\n".join(msgs) if msgs else None

    def update_rstbl(self, rstbl_path, new_name):
        self.log(self.tr("rstbl_update_not_implemented").format(new_name))

    def show_undo_dialog(self):
        dest = self.settings.get("last_dest")
        if not dest:
            return
        history = load_clone_history(dest)
        if not history:
            QMessageBox.information(self, self.tr("info_dialog_title"),
                                    self.tr("undo_no_history"))
            return
        dlg = UndoDialog(history, dest, self.settings, self.texts, self)
        dlg.exec_()
        if self.source_combo.currentIndex() == 1:
            self.load_actors()
        if self.main_window is not None and hasattr(self.main_window, "refresh_all_directories"):
            self.main_window.refresh_all_directories()

    def refresh_texts(self, texts):
        self.texts = texts
        self.single_name_edit.setPlaceholderText(self.tr("new_name_label"))
        self.batch_edit.setPlaceholderText(self.tr("clone_batch_placeholder"))
        self.clone_btn.setText(self.tr("clone_btn"))
        self.rstb_btn.setText(self.tr("rstb_now"))
        self.undo_btn.setText(self.tr("undo_btn"))
        self.cb_actor_engine.setText(self.tr("check_actor_engine"))
        self.cb_modelinfo_refs.setText(self.tr("check_modelinfo_refs"))
        self.cb_rsdb_entries.setText(self.tr("check_rsdb_entries"))
        self.cb_copy_pack.setText(self.tr("clone_copy_pack"))
        self.cb_copy_bfres.setText(self.tr("clone_copy_bfres"))
        self.cb_rename_bfres_internals.setText(self.tr("clone_rename_bfres_internals"))
        self.cb_copy_animation.setText(self.tr("clone_copy_animation"))
        self.cb_copy_pack.setToolTip(self.tr("clone_copy_pack_tooltip"))
        self.cb_copy_bfres.setToolTip(self.tr("clone_copy_bfres_tooltip"))
        self.cb_rename_bfres_internals.setToolTip(self.tr("clone_rename_bfres_internals_tooltip"))
        self.cb_copy_animation.setToolTip(self.tr("clone_copy_animation_tooltip"))
        self.source_combo.setToolTip(self.tr("switch_source_tooltip"))
        self.search_edit.setPlaceholderText(self.tr("search_placeholder"))
        # Source-Combo-Einträge aktualisieren (nur wenn nötig)
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem(self.texts.get("clone_source_original", "Original"))
        self.source_combo.addItem(self.texts.get("clone_source_mod", "Mod"))
        self.source_combo.setCurrentIndex(self.source_combo.currentIndex())  # behält Auswahl
        self.source_combo.blockSignals(False)
        self.update_clone_btn_tooltip()
        self.update_rstb_button_state()
