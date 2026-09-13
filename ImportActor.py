#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# import external actor tab

import io
import os
import json
import shutil

import sarc

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QRegExp, QTimer, QModelIndex
from PyQt5.QtGui import (
    QFont, QRegExpValidator, QStandardItemModel, QStandardItem,
    QKeySequence, QDragEnterEvent, QDropEvent, QTextCursor, QColor
)

from FileHandler import ( # type: ignore
    parse_byml, dump_byml, decompress_zs, compress_and_write_zs,
    byml_find_field, _native_to_byml,
)
from Duplication import ( # type: ignore
    adjust_actor_param_modelinfo_ref, adjust_model_info,
    clone_actorinfo_entry, clone_gameactorinfo_entry,
)
from SettingsManager import save_settings # type: ignore


# Implementation details.

DEFAULT_IMPORT_CHECKBOXES = {
    "adjust_actor_engine": True,
    "adjust_modelinfo_refs": True,
}

RSDB_LABELS = {"actorinfo": "ActorInfo", "gameactorinfo": "GameActorInfo"}


def get_internal_actor_name_from_pack(pack_path):
    """Findet den internen Actor-Namen (ActorParam-Dateiname) im Pack."""
    try:
        data = decompress_zs(pack_path)
        archive = sarc.SARC(data=data)
    except Exception:
        return None
    for f in archive.list_files():
        if f.startswith("Actor/") and f.endswith(".engine__actor__ActorParam.bgyml"):
            base = f[len("Actor/"):]
            return base[:-len(".engine__actor__ActorParam.bgyml")]
    return None


def rsdb_row_exists(path, id_field, row_id):
    """Prueft, ob ein RowId in einer RSDB-Datei bereits existiert. None = Datei unlesbar/fehlt."""
    if not path or not os.path.isfile(path):
        return None
    try:
        data = decompress_zs(path)
        root = parse_byml(data)
    except Exception:
        return None
    if not isinstance(root, list):
        return None
    return any(isinstance(e, dict) and e.get(id_field) == row_id for e in root)


def add_native_rsdb_entry(path, id_field, native_entry):
    """Fuegt einen kompletten, vom Nutzer eingegebenen Eintrag (JSON/native Form) einer RSDB-Datei hinzu."""
    if not path or not os.path.isfile(path):
        return {"status": "file_missing"}
    if not isinstance(native_entry, dict):
        return {"status": "invalid", "message": "Eintrag ist kein Objekt."}
    row_id = native_entry.get(id_field)
    if not row_id:
        return {"status": "invalid", "message": f"'{id_field}' fehlt im eingefügten Text."}
    try:
        data = decompress_zs(path)
        root = parse_byml(data)
    except Exception as e:
        return {"status": "invalid", "message": str(e)}
    if not isinstance(root, list):
        return {"status": "invalid", "message": "RSDB-Root ist keine Liste."}
    for entry in root:
        if isinstance(entry, dict) and entry.get(id_field) == row_id:
            return {"status": "exists"}
    try:
        new_entry = _native_to_byml(native_entry)
    except Exception as e:
        return {"status": "invalid", "message": str(e)}
    new_root = root + [new_entry]
    try:
        compress_and_write_zs(path, dump_byml(new_root))
    except Exception as e:
        return {"status": "invalid", "message": str(e)}
    return {"status": "ok"}


# Implementation details.
class ActorPickerDialog(QDialog):
    """Durchsuchbare Actor-Auswahlliste (Original- und Mod-RomFS)."""

    def __init__(self, texts, actors, parent=None):
        super().__init__(parent)
        self.texts = texts
        self.selected_name = None
        self.setWindowTitle(self._tr("import_pick_actor_dialog_title", "Actor auswählen"))
        self.resize(420, 520)

        layout = QVBoxLayout(self)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self._tr("search_placeholder", "Suchen"))
        self.search_edit.textChanged.connect(self.filter_list)
        layout.addWidget(self.search_edit)

        self._all_actors = actors
        self.list_widget = QListWidget()
        self.list_widget.addItems(self._all_actors)
        self.list_widget.itemDoubleClicked.connect(self._accept_item)
        self.list_widget.itemSelectionChanged.connect(self._update_ok_state)
        layout.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.ok_btn = QPushButton(self._tr("import_pick_actor_ok", "Übernehmen"))
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._accept_selected)
        cancel_btn = QPushButton(self._tr("cancel", "Abbrechen"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.search_edit.setFocus()

    def _tr(self, key, default):
        return self.texts.get(key, default)

    def filter_list(self, text):
        text = text.lower().strip()
        self.list_widget.clear()
        self.list_widget.addItems([a for a in self._all_actors if text in a.lower()])

    def _update_ok_state(self):
        self.ok_btn.setEnabled(bool(self.list_widget.selectedItems()))

    def _accept_item(self, item):
        self.selected_name = item.text()
        self.accept()

    def _accept_selected(self):
        items = self.list_widget.selectedItems()
        if items:
            self.selected_name = items[0].text()
            self.accept()


class ImportActorTab(QWidget):
    def __init__(self, settings, texts, log_func, main_window=None):
        super().__init__()
        self.settings = settings
        self.texts = texts
        self.log = log_func
        self.main_window = main_window

        main_layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Vertical)

        # --- Dateien ---
        files_widget = QWidget()
        files_layout = QVBoxLayout(files_widget)
        files_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()

        self.pack_edit = QLineEdit()
        self.pack_edit.textChanged.connect(self.validate_all)
        pack_row = QHBoxLayout()
        pack_row.addWidget(self.pack_edit)
        self.pack_browse_btn = QPushButton(self.tr("browse_btn"))
        self.pack_browse_btn.clicked.connect(self.browse_pack)
        pack_row.addWidget(self.pack_browse_btn)
        self.pack_label = QLabel(self.tr("import_pack_label"))
        form.addRow(self.pack_label, pack_row)

        self.bfres_edit = QLineEdit()
        self.bfres_edit.textChanged.connect(self.validate_all)
        bfres_row = QHBoxLayout()
        bfres_row.addWidget(self.bfres_edit)
        self.bfres_browse_btn = QPushButton(self.tr("browse_btn"))
        self.bfres_browse_btn.clicked.connect(self.browse_bfres)
        bfres_row.addWidget(self.bfres_browse_btn)
        self.bfres_label = QLabel(self.tr("import_bfres_label"))
        form.addRow(self.bfres_label, bfres_row)

        self.anm_edit = QLineEdit()
        self.anm_edit.textChanged.connect(self.validate_all)
        anm_row = QHBoxLayout()
        anm_row.addWidget(self.anm_edit)
        self.anm_browse_btn = QPushButton(self.tr("browse_btn"))
        self.anm_browse_btn.clicked.connect(self.browse_anm)
        anm_row.addWidget(self.anm_browse_btn)
        self.anm_label = QLabel(self.tr("import_anm_label"))
        form.addRow(self.anm_label, anm_row)

        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self.validate_all)
        self.name_label = QLabel(self.tr("import_name_label"))
        form.addRow(self.name_label, self.name_edit)

        files_layout.addLayout(form)

        saved_boxes = settings.get("import_actor_checkboxes", DEFAULT_IMPORT_CHECKBOXES)
        self.cb_actor_engine = QCheckBox(self.tr("check_actor_engine"))
        self.cb_actor_engine.setChecked(saved_boxes.get("adjust_actor_engine", True))
        self.cb_actor_engine.setToolTip(self.tr("tooltip_actor_engine"))
        self.cb_actor_engine.toggled.connect(lambda v: self.save_checkbox("adjust_actor_engine", v))
        files_layout.addWidget(self.cb_actor_engine)

        self.cb_modelinfo_refs = QCheckBox(self.tr("check_modelinfo_refs"))
        self.cb_modelinfo_refs.setChecked(saved_boxes.get("adjust_modelinfo_refs", True))
        self.cb_modelinfo_refs.setToolTip(self.tr("tooltip_modelinfo_refs"))
        self.cb_modelinfo_refs.toggled.connect(lambda v: self.save_checkbox("adjust_modelinfo_refs", v))
        files_layout.addWidget(self.cb_modelinfo_refs)

        self.splitter.addWidget(files_widget)

        # --- ActorInfo / GameActorInfo ---
        rsdb_widget = QWidget()
        rsdb_layout = QHBoxLayout(rsdb_widget)
        rsdb_layout.setContentsMargins(0, 0, 0, 0)

        (self.actorinfo_group, self.actorinfo_mode_combo, self.actorinfo_source_edit,
         self.actorinfo_source_label, self.actorinfo_pick_btn, self.actorinfo_text_edit,
         self.actorinfo_adjust_cb) = self._build_rsdb_group(
            self.tr("import_group_actorinfo"), with_adjust_checkbox=True)
        rsdb_layout.addWidget(self.actorinfo_group)

        (self.gameactorinfo_group, self.gameactorinfo_mode_combo, self.gameactorinfo_source_edit,
         self.gameactorinfo_source_label, self.gameactorinfo_pick_btn, self.gameactorinfo_text_edit,
         _unused) = self._build_rsdb_group(
            self.tr("import_group_gameactorinfo"), with_adjust_checkbox=False)
        rsdb_layout.addWidget(self.gameactorinfo_group)

        self.splitter.addWidget(rsdb_widget)
        main_layout.addWidget(self.splitter)

        btn_row = QHBoxLayout()
        self.import_btn = QPushButton(self.tr("import_actor_btn"))
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self.do_import)
        btn_row.addWidget(self.import_btn)
        main_layout.addLayout(btn_row)

        self.restore_splitter()
        self.splitter.splitterMoved.connect(self.save_splitter)

        self.validate_all()

    def tr(self, key, *args):
        return self.texts.get(key, key).format(*args)

    # --- UI-Aufbau ---
    def _build_rsdb_group(self, title, with_adjust_checkbox):
        group = QGroupBox(title)
        layout = QVBoxLayout(group)

        mode_combo = QComboBox()
        mode_combo.addItem(self.tr("import_mode_none"))
        mode_combo.addItem(self.tr("import_mode_clone"))
        mode_combo.addItem(self.tr("import_mode_text"))
        layout.addWidget(mode_combo)

        stacked = QStackedWidget()

        empty_page = QWidget()
        stacked.addWidget(empty_page)

        clone_page = QWidget()
        clone_layout = QFormLayout(clone_page)
        source_edit = QLineEdit()
        source_edit.setPlaceholderText(self.tr("import_source_actor_placeholder"))
        source_row = QHBoxLayout()
        source_row.addWidget(source_edit)
        pick_btn = QPushButton(self.tr("import_pick_actor_btn"))
        pick_btn.clicked.connect(lambda: self._pick_source_actor(source_edit))
        source_row.addWidget(pick_btn)
        source_label = QLabel(self.tr("import_source_actor_label"))
        clone_layout.addRow(source_label, source_row)
        adjust_cb = None
        if with_adjust_checkbox:
            adjust_cb = QCheckBox(self.tr("import_check_adjust_fmdb_modelproject"))
            adjust_cb.setChecked(True)
            clone_layout.addRow(adjust_cb)
        stacked.addWidget(clone_page)

        text_page = QWidget()
        text_layout = QVBoxLayout(text_page)
        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText(self.tr("import_json_placeholder"))
        text_layout.addWidget(text_edit)
        stacked.addWidget(text_page)

        layout.addWidget(stacked)

        mode_combo.currentIndexChanged.connect(stacked.setCurrentIndex)
        mode_combo.currentIndexChanged.connect(self.validate_all)
        source_edit.textChanged.connect(self.validate_all)
        text_edit.textChanged.connect(self.validate_all)
        if adjust_cb is not None:
            adjust_cb.toggled.connect(self.validate_all)

        group._mode_combo = mode_combo
        group._stacked = stacked
        group._source_label = source_label
        group._adjust_cb = adjust_cb

        return group, mode_combo, source_edit, source_label, pick_btn, text_edit, adjust_cb

    # --- Datei-Browser ---
    def browse_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("select_pack_dialog_title"), "",
                                              self.tr("file_filter_pack_zs"))
        if path:
            self.pack_edit.setText(path)
            base = os.path.basename(path)
            if base.lower().endswith(".pack.zs"):
                base = base[:-len(".pack.zs")]
            self.name_edit.setText(base)

    def browse_bfres(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("select_bfres_dialog_title"), "",
                                              self.tr("file_filter_bfres"))
        if path:
            self.bfres_edit.setText(path)

    def browse_anm(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("select_anm_dialog_title"), "",
                                              self.tr("file_filter_bfres"))
        if path:
            self.anm_edit.setText(path)

    # --- Quell-Actor-Auswahl ---
    def _list_known_actors(self):
        names = set()
        for key in ("last_source", "last_dest"):
            directory = self.settings.get(key, "")
            if not directory:
                continue
            pack_dir = os.path.join(directory, "Pack", "Actor")
            if os.path.isdir(pack_dir):
                for f in os.listdir(pack_dir):
                    if f.endswith(".pack.zs"):
                        names.add(f[:-len(".pack.zs")])
        return sorted(names)

    def _pick_source_actor(self, target_edit):
        actors = self._list_known_actors()
        if not actors:
            QMessageBox.information(self, self.tr("info_dialog_title"), self.tr("import_pick_actor_none"))
            return
        dlg = ActorPickerDialog(self.texts, actors, self)
        if dlg.exec_() == QDialog.Accepted and dlg.selected_name:
            target_edit.setText(dlg.selected_name)

    # --- Validierung ---
    def _set_red(self, widget, invalid):
        widget.setStyleSheet("background-color: #b71c1c;" if invalid else "")

    def validate_all(self):
        reasons = []
        dest = self.settings.get("last_dest", "")

        pack_path = self.pack_edit.text().strip()
        pack_ok = bool(pack_path) and os.path.isfile(pack_path) and pack_path.lower().endswith(".pack.zs")
        self._set_red(self.pack_edit, not pack_ok)
        if not pack_path:
            reasons.append(self.tr("tooltip_reason_no_pack"))
        elif not pack_ok:
            reasons.append(self.tr("tooltip_reason_pack_missing"))

        name = self.name_edit.text().strip()
        name_ok = bool(name) and bool(dest)
        if not name:
            reasons.append(self.tr("tooltip_reason_no_name"))
        elif not dest:
            reasons.append(self.tr("tooltip_reason_no_dest"))
        elif os.path.exists(os.path.join(dest, "Pack", "Actor", f"{name}.pack.zs")):
            name_ok = False
            reasons.append(self.tr("tooltip_reason_name_invalid"))
        self._set_red(self.name_edit, not name_ok)

        bfres_path = self.bfres_edit.text().strip()
        bfres_ok = True
        if bfres_path:
            bfres_ok = os.path.isfile(bfres_path)
            if bfres_ok and dest and name:
                if os.path.exists(os.path.join(dest, "Model", f"{name}.bfres.zs")):
                    bfres_ok = False
            if not bfres_ok:
                reasons.append(self.tr("tooltip_reason_bfres_invalid"))
        self._set_red(self.bfres_edit, bool(bfres_path) and not bfres_ok)

        anm_path = self.anm_edit.text().strip()
        anm_ok = True
        if anm_path:
            anm_ok = os.path.isfile(anm_path)
            if anm_ok and dest and name:
                if os.path.exists(os.path.join(dest, "Model", f"{name}Anm.bfres.zs")):
                    anm_ok = False
            if not anm_ok:
                reasons.append(self.tr("tooltip_reason_anm_invalid"))
        self._set_red(self.anm_edit, bool(anm_path) and not anm_ok)

        actorinfo_ok = self._validate_rsdb_section(
            kind="actorinfo", mode_combo=self.actorinfo_mode_combo,
            source_edit=self.actorinfo_source_edit, text_edit=self.actorinfo_text_edit,
            reasons=reasons,
            reason_source_missing="tooltip_reason_actorinfo_source_missing",
            reason_source_not_found="tooltip_reason_actorinfo_source_not_found",
            reason_json_invalid="tooltip_reason_actorinfo_json_invalid",
            reason_rowid_exists="tooltip_reason_actorinfo_rowid_exists",
        )
        gameactorinfo_ok = self._validate_rsdb_section(
            kind="gameactorinfo", mode_combo=self.gameactorinfo_mode_combo,
            source_edit=self.gameactorinfo_source_edit, text_edit=self.gameactorinfo_text_edit,
            reasons=reasons,
            reason_source_missing="tooltip_reason_gameactorinfo_source_missing",
            reason_source_not_found="tooltip_reason_gameactorinfo_source_not_found",
            reason_json_invalid="tooltip_reason_gameactorinfo_json_invalid",
            reason_rowid_exists="tooltip_reason_gameactorinfo_rowid_exists",
        )

        all_ok = pack_ok and name_ok and bfres_ok and anm_ok and actorinfo_ok and gameactorinfo_ok
        self.import_btn.setEnabled(all_ok)
        self.import_btn.setToolTip("; ".join(reasons) if reasons else self.tr("tooltip_clone_possible"))

    def _validate_rsdb_section(self, kind, mode_combo, source_edit, text_edit, reasons,
                                reason_source_missing, reason_source_not_found,
                                reason_json_invalid, reason_rowid_exists):
        mode = mode_combo.currentIndex()
        if mode == 0:
            self._set_red(source_edit, False)
            self._set_red(text_edit, False)
            return True

        path = self.settings.get("actorinfo_path" if kind == "actorinfo" else "gameactorinfo_path", "")

        if mode == 1:
            old_name = source_edit.text().strip()
            if not old_name:
                self._set_red(source_edit, True)
                reasons.append(self.tr(reason_source_missing))
                return False
            if path:
                old_row_id = old_name if kind == "actorinfo" else \
                    f"Work/Actor/{old_name}.engine__actor__ActorParam.gyml"
                exists = rsdb_row_exists(path, "__RowId", old_row_id)
                if exists is False:
                    self._set_red(source_edit, True)
                    reasons.append(self.tr(reason_source_not_found))
                    return False
            self._set_red(source_edit, False)
            return True

        # mode == 2: eigener Text
        text = text_edit.toPlainText().strip()
        if not text:
            self._set_red(text_edit, True)
            reasons.append(self.tr(reason_json_invalid))
            return False
        try:
            native = json.loads(text)
            _native_to_byml(native)
            row_id = native.get("__RowId") if isinstance(native, dict) else None
            if not row_id:
                raise ValueError("__RowId missing")
        except Exception:
            self._set_red(text_edit, True)
            reasons.append(self.tr(reason_json_invalid))
            return False
        if path:
            exists = rsdb_row_exists(path, "__RowId", row_id)
            if exists:
                self._set_red(text_edit, True)
                reasons.append(self.tr(reason_rowid_exists))
                return False
        self._set_red(text_edit, False)
        return True

    # --- Settings / Splitter ---
    def save_checkbox(self, key, value):
        boxes = self.settings.setdefault("import_actor_checkboxes", {})
        boxes[key] = value
        save_settings(self.settings)

    def restore_splitter(self):
        self.splitter.setSizes(self.settings.get("import_actor_splitter", [200, 400]))

    def save_splitter(self):
        self.settings["import_actor_splitter"] = self.splitter.sizes()
        save_settings(self.settings)

    def refresh_paths(self):
        """Wird aufgerufen, wenn last_dest/actorinfo_path/gameactorinfo_path sich aendern."""
        self.validate_all()

    # --- Interner Pack-Abgleich (Actor-Engine / ModelInfo), analog zum Klonen ---
    def adjust_internal_pack(self, pack_path, old_name, new_name):
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

    # --- ActorInfo / GameActorInfo anwenden ---
    def _apply_rsdb_section(self, kind, name):
        label = RSDB_LABELS[kind]
        if kind == "actorinfo":
            mode_combo = self.actorinfo_mode_combo
            source_edit = self.actorinfo_source_edit
            text_edit = self.actorinfo_text_edit
            adjust_cb = self.actorinfo_adjust_cb
            path = self.settings.get("actorinfo_path", "")
            row_id = name
        else:
            mode_combo = self.gameactorinfo_mode_combo
            source_edit = self.gameactorinfo_source_edit
            text_edit = self.gameactorinfo_text_edit
            adjust_cb = None
            path = self.settings.get("gameactorinfo_path", "")
            row_id = f"Work/Actor/{name}.engine__actor__ActorParam.gyml"

        mode = mode_combo.currentIndex()
        if mode == 0:
            return
        if not path:
            self.log(self.tr("log_rsdb_path_missing", label), "warning")
            return

        if mode == 1:
            old_name = source_edit.text().strip()
            if kind == "actorinfo":
                change = adjust_cb.isChecked() if adjust_cb is not None else False
                res = clone_actorinfo_entry(path, old_name, name, change_fmdb=change, change_modelproject=change)
            else:
                res = clone_gameactorinfo_entry(path, old_name, name)
            if res["status"] == "ok":
                self.log(self.tr("log_import_rsdb_cloned", label, old_name, name), "success")
            elif res["status"] == "exists":
                self.log(self.tr("log_rsdb_exists", label), "warning")
            elif res["status"] == "not_found":
                self.log(self.tr("log_rsdb_not_found", label), "warning")
            else:
                self.log(self.tr("log_rsdb_error", label, res.get("message", "")), "error")
        else:
            try:
                native = json.loads(text_edit.toPlainText())
            except Exception as e:
                self.log(self.tr("log_rsdb_error", label, str(e)), "error")
                return
            res = add_native_rsdb_entry(path, "__RowId", native)
            if res["status"] == "ok":
                self.log(self.tr("log_import_rsdb_text_added", label, row_id), "success")
            elif res["status"] == "exists":
                self.log(self.tr("log_rsdb_exists", label), "warning")
            else:
                self.log(self.tr("log_rsdb_error", label, res.get("message", "")), "error")

    # --- Import durchfuehren ---
    def do_import(self):
        name = self.name_edit.text().strip()
        dest = self.settings.get("last_dest", "")
        pack_src = self.pack_edit.text().strip()
        bfres_src = self.bfres_edit.text().strip()
        anm_src = self.anm_edit.text().strip()

        dest_pack = os.path.join(dest, "Pack", "Actor", f"{name}.pack.zs")
        dest_model = os.path.join(dest, "Model", f"{name}.bfres.zs")
        dest_anm = os.path.join(dest, "Model", f"{name}Anm.bfres.zs")

        self.log(self.tr("log_import_start", name))

        os.makedirs(os.path.dirname(dest_pack), exist_ok=True)
        try:
            shutil.copy2(pack_src, dest_pack)
            self.log(self.tr("log_import_pack_copied", dest_pack), "success")
        except Exception as e:
            self.log(self.tr("log_rsdb_error", "Pack", str(e)), "error")
            return

        if bfres_src:
            os.makedirs(os.path.dirname(dest_model), exist_ok=True)
            try:
                shutil.copy2(bfres_src, dest_model)
                self.log(self.tr("log_import_bfres_copied", dest_model), "success")
            except Exception as e:
                self.log(self.tr("log_rsdb_error", "BFRES", str(e)), "error")

        if anm_src:
            os.makedirs(os.path.dirname(dest_anm), exist_ok=True)
            try:
                shutil.copy2(anm_src, dest_anm)
                self.log(self.tr("log_import_anm_copied", dest_anm), "success")
            except Exception as e:
                self.log(self.tr("log_rsdb_error", "Anm BFRES", str(e)), "error")

        if self.cb_actor_engine.isChecked() or self.cb_modelinfo_refs.isChecked():
            try:
                internal_name = get_internal_actor_name_from_pack(dest_pack)
                if internal_name and internal_name != name:
                    self.adjust_internal_pack(dest_pack, internal_name, name)
                    self.log(self.tr("log_import_internal_adjusted", internal_name, name), "success")
                elif not internal_name:
                    self.log(self.tr("log_import_internal_name_not_found"), "warning")
            except Exception as e:
                self.log(self.tr("log_internal_error", str(e)), "error")

        self._apply_rsdb_section("actorinfo", name)
        self._apply_rsdb_section("gameactorinfo", name)

        self.log(self.tr("log_import_success", name), "success")

        self.pack_edit.clear()
        self.bfres_edit.clear()
        self.anm_edit.clear()
        self.name_edit.clear()
        self.actorinfo_mode_combo.setCurrentIndex(0)
        self.gameactorinfo_mode_combo.setCurrentIndex(0)
        self.actorinfo_source_edit.clear()
        self.gameactorinfo_source_edit.clear()
        self.actorinfo_text_edit.clear()
        self.gameactorinfo_text_edit.clear()

        if self.main_window is not None and hasattr(self.main_window, "refresh_all_directories"):
            self.main_window.refresh_all_directories()

    # --- Sprachwechsel ---
    def refresh_texts(self, texts):
        self.texts = texts
        self.pack_label.setText(self.tr("import_pack_label"))
        self.bfres_label.setText(self.tr("import_bfres_label"))
        self.anm_label.setText(self.tr("import_anm_label"))
        self.name_label.setText(self.tr("import_name_label"))
        self.pack_browse_btn.setText(self.tr("browse_btn"))
        self.bfres_browse_btn.setText(self.tr("browse_btn"))
        self.anm_browse_btn.setText(self.tr("browse_btn"))
        self.import_btn.setText(self.tr("import_actor_btn"))
        self.cb_actor_engine.setText(self.tr("check_actor_engine"))
        self.cb_actor_engine.setToolTip(self.tr("tooltip_actor_engine"))
        self.cb_modelinfo_refs.setText(self.tr("check_modelinfo_refs"))
        self.cb_modelinfo_refs.setToolTip(self.tr("tooltip_modelinfo_refs"))

        self.actorinfo_group.setTitle(self.tr("import_group_actorinfo"))
        self.gameactorinfo_group.setTitle(self.tr("import_group_gameactorinfo"))
        self.actorinfo_source_label.setText(self.tr("import_source_actor_label"))
        self.gameactorinfo_source_label.setText(self.tr("import_source_actor_label"))
        self.actorinfo_source_edit.setPlaceholderText(self.tr("import_source_actor_placeholder"))
        self.gameactorinfo_source_edit.setPlaceholderText(self.tr("import_source_actor_placeholder"))
        self.actorinfo_pick_btn.setText(self.tr("import_pick_actor_btn"))
        self.gameactorinfo_pick_btn.setText(self.tr("import_pick_actor_btn"))
        self.actorinfo_text_edit.setPlaceholderText(self.tr("import_json_placeholder"))
        self.gameactorinfo_text_edit.setPlaceholderText(self.tr("import_json_placeholder"))
        if self.actorinfo_adjust_cb is not None:
            self.actorinfo_adjust_cb.setText(self.tr("import_check_adjust_fmdb_modelproject"))

        for combo in (self.actorinfo_mode_combo, self.gameactorinfo_mode_combo):
            current = combo.currentIndex()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(self.tr("import_mode_none"))
            combo.addItem(self.tr("import_mode_clone"))
            combo.addItem(self.tr("import_mode_text"))
            combo.setCurrentIndex(current)
            combo.blockSignals(False)

        self.validate_all()
