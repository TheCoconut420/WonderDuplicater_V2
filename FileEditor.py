#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pack and file editor

import os
import io
import json
import struct
import re
import tempfile

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QRegExp, QTimer, QModelIndex
from PyQt5.QtGui import (
    QFont, QRegExpValidator, QStandardItemModel, QStandardItem,
    QKeySequence, QDragEnterEvent, QDropEvent, QTextCursor, QColor, QFontMetrics
)

import zstandard as zstd
import sarc
import oead
import ainb
import asb

from SettingsManager import save_settings

from FileHandler import (
    parse_byml, dump_byml, decompress_zs, compress_and_write_zs,
    _byml_to_native, _native_to_byml, try_extract_bfres_embeds,
)


class SingleFileEditor(QWidget):
    AINB_MAX_INLINE_SIZE = 200_000
    AINB_MAGIC = b'AINB'
    ASB_MAGIC = b'ASB '

    @staticmethod
    def _asb_parse(data):
        """Implementation details."""
        return asb.ASB.from_binary(data).asdict()

    @staticmethod
    def _asb_build(asb_dict):
        """Implementation details."""
        file_obj = asb.ASB.from_dict(asb_dict)
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_obj.to_binary(tmp_dir)
            out_path = os.path.join(tmp_dir, file_obj.filename + ".asb")
            with open(out_path, "rb") as f:
                return f.read()


    def __init__(self, texts, path=None, parent=None):
        super().__init__(parent)
        self.texts = texts
        self.files = {}
        self.file_sizes = {}
        self.file_meta = {}
        self.empty_folders = set()
        self.original_path = None
        self.mode = None
        self.current_edit_path = None
        self.modified = False
        self.current_modified = False
        self.current_edit_mode = None
        self._full_tree_expanded_paths = []
        self.export_json_btn = None
        self.import_json_btn = None
        self.init_ui()
        if path:
            self.load_file(path)

    def tr(self, key, *args):
        return self.texts.get(key, key).format(*args)

    def _update_save_button_state(self):
        self.save_btn.setEnabled(self.modified)
        if self.modified:
            self.save_btn.setStyleSheet(
                "QPushButton { background-color: #4caf50; color: white; border: 1px solid #5a5a5a; padding: 5px; }"
                "QPushButton:disabled { background-color: #555; color: #aaa; }"
            )
        else:
            self.save_btn.setStyleSheet("")
        parent = self.parent()
        if parent and hasattr(parent, "update_tab_title"):
            parent.update_tab_title(self, self.modified)

    def _update_save_current_button_state(self):
        self.save_current_btn.setEnabled(self.current_edit_path is not None)
        if self.current_modified and self.current_edit_path is not None:
            self.save_current_btn.setStyleSheet(
                "QPushButton { background-color: #4caf50; color: white; border: 1px solid #5a5a5a; padding: 5px; }"
                "QPushButton:disabled { background-color: #555; color: #aaa; }"
            )
        else:
            self.save_current_btn.setStyleSheet("")
        self._update_export_import_buttons()

    def _update_export_import_buttons(self):
        enabled = self.current_edit_path is not None and self.current_edit_mode in ("byml", "ainb", "asb")
        if self.export_json_btn:
            self.export_json_btn.setEnabled(enabled)
        if self.import_json_btn:
            self.import_json_btn.setEnabled(enabled)

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.save_btn = QPushButton(self.tr("editor_save"))
        self.save_btn.clicked.connect(self.save)
        self.save_btn.setShortcut(QKeySequence.Save)
        toolbar.addWidget(self.save_btn)

        self.save_as_btn = QPushButton(self.tr("editor_save_as"))
        self.save_as_btn.clicked.connect(self.save_as)
        self.save_as_btn.setShortcut(QKeySequence("Ctrl+Shift+S"))
        toolbar.addWidget(self.save_as_btn)

        export_byml_text = self.texts.get("export_all_byml") or "Export all BYML to TXT"
        self.export_byml_btn = QPushButton(export_byml_text)
        self.export_byml_btn.clicked.connect(self.export_all_byml)
        toolbar.addWidget(self.export_byml_btn)

        export_ainb_text = self.texts.get("export_all_ainb") or "Export all AINB as JSON"
        self.export_ainb_btn = QPushButton(export_ainb_text)
        self.export_ainb_btn.clicked.connect(self.export_all_ainb)
        toolbar.addWidget(self.export_ainb_btn)

        toolbar.addStretch()
        toolbar.addWidget(QLabel(self.tr("editor_search")))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("search_placeholder"))
        self.search_edit.textChanged.connect(self.populate_tree)
        toolbar.addWidget(self.search_edit)

        self.search_content_cb = QCheckBox(self.tr("console_search_content"))
        self.search_content_cb.setChecked(True)
        self.search_content_cb.toggled.connect(self.populate_tree)
        toolbar.addWidget(self.search_content_cb)
        main_layout.addLayout(toolbar)

        self.splitter = QSplitter(Qt.Horizontal)

        self.tree = QTreeView()
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels([self.tr("editor_name_col")])
        self.tree.setModel(self.model)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.on_context_menu)
        self.tree.doubleClicked.connect(self.on_double_click)
        self.splitter.addWidget(self.tree)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0,0,0,0)

        edit_toolbar = QHBoxLayout()
        self.edit_path_label = QLabel("")
        edit_toolbar.addWidget(self.edit_path_label)
        edit_toolbar.addStretch()

        self.export_json_btn = QPushButton(self.tr("export_json_btn", "Export JSON"))
        self.export_json_btn.clicked.connect(self.export_current_json)
        self.export_json_btn.setEnabled(False)
        edit_toolbar.addWidget(self.export_json_btn)

        self.import_json_btn = QPushButton(self.tr("import_json_btn", "Import JSON"))
        self.import_json_btn.clicked.connect(self.import_current_json)
        self.import_json_btn.setEnabled(False)
        edit_toolbar.addWidget(self.import_json_btn)

        self.save_current_btn = QPushButton(self.tr("editor_save_current"))
        self.save_current_btn.clicked.connect(self.save_current_file)
        self.save_current_btn.setEnabled(False)
        self.save_current_btn.setShortcut(QKeySequence("Ctrl+Return"))
        edit_toolbar.addWidget(self.save_current_btn)
        right_layout.addLayout(edit_toolbar)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(self.tr("search_byml")))
        self.byml_search_edit = QLineEdit()
        self.byml_search_edit.setPlaceholderText(self.tr("search_byml"))
        self.byml_search_edit.textChanged.connect(self.highlight_byml)
        search_row.addWidget(self.byml_search_edit)
        right_layout.addLayout(search_row)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setObjectName("monoEditor")
        self.text_edit.setFont(QFont("Consolas", 10))
        self.text_edit.setReadOnly(True)
        self.text_edit.textChanged.connect(self._on_text_changed)
        right_layout.addWidget(self.text_edit)

        self.splitter.addWidget(right_widget)
        main_layout.addWidget(self.splitter)

        self.restore_splitter()
        self.splitter.splitterMoved.connect(self.save_splitter)
        self.update_io_buttons_state()
        self._update_save_button_state()
        self._update_save_current_button_state()

    def _on_text_changed(self):
        if not self.text_edit.isReadOnly() and self.current_edit_path is not None:
            self.current_modified = True
            self._update_save_current_button_state()

    def restore_splitter(self):
        main_win = self.window()
        if hasattr(main_win, 'settings'):
            self.splitter.setSizes(main_win.settings.get("editor_splitter", [300, 500]))

    def save_splitter(self):
        main_win = self.window()
        if hasattr(main_win, 'settings'):
            main_win.settings["editor_splitter"] = self.splitter.sizes()
            save_settings(main_win.settings)

    def _is_ainb_data(self, data: bytes) -> bool:
        return data[:4] == self.AINB_MAGIC

    def _is_asb_data(self, data: bytes) -> bool:
        return data[:4] == self.ASB_MAGIC

    def load_file(self, path):
        if not os.path.isfile(path): return
        try:
            data = decompress_zs(path)
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))
            return
        fn = os.path.basename(path).lower()
        self.close_inline_editor()

        if data[:4] == self.ASB_MAGIC or fn.endswith('.asb.zs') or fn.endswith('.asb'):
            self.mode = "asb_single"
            entry = fn[:-3] if fn.endswith('.zs') else fn
            self.files = {entry: data}
            self.file_sizes = {entry: len(data)}
            self.original_path = path
            self.modified = False
            self._update_save_button_state()
            self.populate_tree()
            self.update_io_buttons_state()
            return

        if fn.endswith(".bfres.zs") or data[:4] == b"FRES":
            self.mode = "bfres_raw"
            self.files.clear()
            self.file_sizes.clear()
            self.original_path = path
            embeds = try_extract_bfres_embeds(data)
            if embeds:
                for name, d in embeds.items():
                    full = f"Embedded/{name}"
                    self.files[full] = d
                    self.file_sizes[full] = len(d)
            else:
                name = os.path.basename(path)
                if name.lower().endswith(".zs"):
                    name = name[:-3]
                self.files[name] = data
                self.file_sizes[name] = len(data)
            self.modified = False
            self._update_save_button_state()
            self.populate_tree()
            self.update_io_buttons_state()
            return
        if fn.endswith((".byml.zs", ".bgyml.zs")):
            self.mode = "byml_single"
            entry = os.path.basename(path)[:-3]
            self.files = {entry: data}
            self.file_sizes = {entry: len(data)}
            self.original_path = path
            self.modified = False
            self._update_save_button_state()
            self.populate_tree()
            self.update_io_buttons_state()
            return
        if data[:4] == b"SARC":
            try:
                archive = sarc.SARC(data=data)
                names = archive.list_files()
                self.mode = "sarc"
                self.files = {n: bytes(archive.get_file_data(n)) for n in names}
                self.file_sizes = {p: len(d) for p, d in self.files.items()}
                self.original_path = path
                self.modified = False
                self._update_save_button_state()
                self.populate_tree()
                self.update_io_buttons_state()
            except Exception as e:
                QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))
            return

    def _get_expanded_paths(self):
        paths = []
        def walk(parent_item, path_parts):
            for row in range(parent_item.rowCount()):
                item = parent_item.child(row)
                current_path = "/".join(path_parts + [item.text()])
                index = self.model.indexFromItem(item)
                if item.hasChildren() and self.tree.isExpanded(index):
                    paths.append(current_path)
                if item.hasChildren():
                    walk(item, path_parts + [item.text()])
        walk(self.model.invisibleRootItem(), [])
        return paths

    def _restore_expanded_paths(self, paths):
        if not paths:
            return
        for path_str in paths:
            index = self._find_index_by_path(path_str)
            if index.isValid():
                self.tree.expand(index)

    def _find_index_by_path(self, path_str):
        parts = path_str.split("/")
        def find(parent_index, parts):
            for row in range(self.model.rowCount(parent_index)):
                index = self.model.index(row, 0, parent_index)
                item = self.model.itemFromIndex(index)
                if item.text() == parts[0]:
                    if len(parts) == 1:
                        return index
                    else:
                        result = find(index, parts[1:])
                        if result.isValid():
                            return result
            return QModelIndex()
        return find(QModelIndex(), parts)

    def populate_tree(self):
        filter_text = self.search_edit.text().strip().lower()
        search_content = self.search_content_cb.isChecked()
        is_filter_active = bool(filter_text)

        if not is_filter_active and self.model.rowCount() > 0:
            self._full_tree_expanded_paths = self._get_expanded_paths()

        self.model.clear()
        self.model.setHorizontalHeaderLabels([self.tr("editor_name_col")])

        for path in sorted(self.files):
            name_match = filter_text in path.lower() if filter_text else True
            content_match = False
            if search_content and filter_text:
                try:
                    content_match = filter_text in self.files[path].decode("utf-8", errors="ignore").lower()
                except:
                    pass
            if not name_match and not content_match:
                continue
            parts = path.split("/")
            parent = self.model.invisibleRootItem()
            for part in parts[:-1]:
                found = None
                for i in range(parent.rowCount()):
                    if parent.child(i).text() == part:
                        found = parent.child(i)
                        break
                if found is None:
                    found = QStandardItem(part)
                    found.setEditable(False)
                    parent.appendRow(found)
                parent = found
            size_str = f"{self.file_sizes[path]:,} Bytes"
            display_name = f"{parts[-1]} ({size_str})"
            item = QStandardItem(display_name)
            item.setEditable(False)
            item.setData(path, Qt.UserRole)
            if content_match and not name_match:
                item.setForeground(QColor(255, 165, 0))
                item.setToolTip(self.tr("content_match_tooltip", "Contains search term"))
            parent.appendRow(item)

        for folder_path in sorted(self.empty_folders):
            if is_filter_active and filter_text not in folder_path.lower():
                continue
            parts = folder_path.split("/")
            parent = self.model.invisibleRootItem()
            for part in parts:
                found = None
                for i in range(parent.rowCount()):
                    if parent.child(i).text() == part:
                        found = parent.child(i)
                        break
                if found is None:
                    found = QStandardItem(part)
                    found.setEditable(False)
                    parent.appendRow(found)
                parent = found

        if is_filter_active:
            self.tree.expandAll()
        else:
            self._restore_expanded_paths(self._full_tree_expanded_paths)

        self.update_io_buttons_state()

    def on_double_click(self, index):
        item = self.model.itemFromIndex(index)
        if not item or item.data(Qt.UserRole) is None:
            return
        full_path = self._get_full_path(index)
        search_term = self.search_edit.text().strip() if self.search_content_cb.isChecked() else ""
        self.open_inline_editor(full_path, search_term)

    def open_inline_editor(self, full_path, auto_search=""):
        data = self.files.get(full_path)
        if data is None:
            return
        self.current_edit_path = full_path
        filename = os.path.basename(full_path)
        self.edit_path_label.setText(self.tr("editor_bearbeite", filename))

        if data[:4] == self.ASB_MAGIC:
            self.current_edit_mode = "asb"
            self.save_current_btn.setEnabled(True)
            try:
                asb_obj = self._asb_parse(data)
                json_str = json.dumps(asb_obj, indent=2, ensure_ascii=False, default=str)
                self.text_edit.setPlainText(json_str)
                self.text_edit.setReadOnly(False)
                if auto_search:
                    self.byml_search_edit.setText(auto_search)
            except Exception as e:
                QMessageBox.warning(self, self.tr("warning_dialog_title"), str(e))
                self.close_inline_editor()
                return
            self.current_modified = False
            self._update_save_current_button_state()
            return

        if filename.lower().endswith((".byml", ".bgyml")):
            self.current_edit_mode = "byml"
            self.save_current_btn.setEnabled(True)
            try:
                obj = parse_byml(data)
                native = _byml_to_native(obj)
                self.file_meta[full_path] = {"native_obj": native}
                self.text_edit.setPlainText(json.dumps(native, indent=2, ensure_ascii=False, default=str))
                self.text_edit.setReadOnly(False)
                if auto_search:
                    self.byml_search_edit.setText(auto_search)
            except Exception as e:
                QMessageBox.warning(self, self.tr("warning_dialog_title"), str(e))
                self.close_inline_editor()
                return

        elif filename.lower().endswith(".ainb") or data[:4] == self.AINB_MAGIC:
            self.current_edit_mode = "ainb"
            self.save_current_btn.setEnabled(True)
            if len(data) > self.AINB_MAX_INLINE_SIZE:
                self.text_edit.setPlainText(
                    self.tr("ainb_too_large",
                            "// This AINB file is too large for inline editing.\n"
                            "// Use the 'Export JSON' and 'Import JSON' buttons to edit externally.")
                )
                self.text_edit.setReadOnly(True)
            else:
                try:
                    ainb_obj = ainb.AINB.from_binary(data)
                    json_str = ainb_obj.to_json()
                    self.text_edit.setPlainText(json_str)
                    self.text_edit.setReadOnly(False)
                except Exception as e:
                    QMessageBox.warning(self, self.tr("warning_dialog_title"), str(e))
                    self.close_inline_editor()
                    return

        elif all(32 <= b < 127 or b in (9, 10, 13) for b in data[:500]):
            self.current_edit_mode = "text"
            self.save_current_btn.setEnabled(True)
            self.text_edit.setPlainText(data.decode("utf-8", errors="replace"))
            self.text_edit.setReadOnly(False)

        else:
            self.current_edit_mode = "hex_readonly"
            self.save_current_btn.setEnabled(False)
            lines = []
            for i in range(0, min(len(data), 4096), 16):
                chunk = data[i:i+16]
                hex_str = " ".join(f"{b:02x}" for b in chunk)
                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{i:08x}  {hex_str:<48}  {ascii_str}")
            self.text_edit.setPlainText("\n".join(lines))
            self.text_edit.setReadOnly(True)

        self.current_modified = False
        self._update_save_current_button_state()

    def save_current_file(self):
        if not self.current_edit_path:
            return
        full_path = self.current_edit_path
        try:
            if self.current_edit_mode == "byml":
                new_native = json.loads(self.text_edit.toPlainText())
                new_obj = _native_to_byml(new_native)
                new_data = dump_byml(new_obj)
            elif self.current_edit_mode == "ainb":
                new_dict = json.loads(self.text_edit.toPlainText())
                new_ainb_obj = ainb.AINB.from_dict(new_dict)
                new_data = new_ainb_obj.to_binary()
            elif self.current_edit_mode == "asb":
                new_dict = json.loads(self.text_edit.toPlainText())
                new_data = self._asb_build(new_dict)
            elif self.current_edit_mode == "text":
                new_data = self.text_edit.toPlainText().encode("utf-8")
            else:
                return
            self.files[full_path] = new_data
            self.file_sizes[full_path] = len(new_data)
            self.modified = True
            self.current_modified = False
            self.populate_tree()
            self._update_save_button_state()
            self._update_save_current_button_state()
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))

    def confirm_close(self):
        # confirms unsaved editor changes
        if self.current_modified:
            answer = QMessageBox.question(
                self, self.tr("unsaved_changes_title", "Unsaved changes"),
                self.tr("unsaved_current_changes"),
                QMessageBox.Discard | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Cancel:
                return False
        return True

    def close_inline_editor(self):
        self.current_edit_path = None
        self.current_edit_mode = None
        self.current_modified = False
        self.edit_path_label.setText("")
        self.text_edit.clear()
        self.text_edit.setReadOnly(True)
        self._update_save_current_button_state()

    def on_context_menu(self, pos):
        index = self.tree.indexAt(pos)
        menu = QMenu(self)

        if index.isValid():
            item = self.model.itemFromIndex(index)
            is_file = item.data(Qt.UserRole) is not None
            if is_file:
                menu.addAction(self.tr("editor_edit"), lambda: self.on_double_click(index))
                if self.mode != "bfres_raw":
                    menu.addAction(self.tr("editor_rename"), lambda: self.rename_item(index))
                    path_for_chain = self._get_full_path(index)
                    if path_for_chain.lower().endswith((".byml", ".bgyml")):
                        menu.addAction(
                    self.tr("editor_rename_chain"),
                            lambda: self.rename_with_chain(index)
                        )
                    menu.addAction(self.tr("editor_delete"), lambda: self.delete_item(index))
                    menu.addAction(self.tr("editor_duplicate"), lambda: self.duplicate_file(index))
                menu.addAction(self.tr("editor_export"), lambda: self.export_item(index))
            else:
                if self.mode != "bfres_raw":
                    menu.addAction(self.tr("editor_rename"), lambda: self.rename_item(index))
                    menu.addAction(self.tr("editor_delete"), lambda: self.delete_item(index))
                menu.addAction(self.tr("export_folder"), lambda: self.export_folder(index))
                if self.mode != "bfres_raw":
                    target_dir = self._get_full_path(index)
                    menu.addAction(self.tr("editor_add_file"), lambda: self.add_files(target_dir))
                    menu.addAction(self.tr("editor_add_folder"), lambda: self.create_folder(target_dir))
        else:
            if self.mode != "bfres_raw":
                menu.addAction(self.tr("editor_add_file"), lambda: self.add_files(""))
                menu.addAction(self.tr("editor_add_folder"), lambda: self.create_folder(""))
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _get_full_path(self, index):
        item = self.model.itemFromIndex(index)
        path = item.data(Qt.UserRole)
        if path:
            return path
        parts = []
        while index.isValid():
            parts.append(self.model.itemFromIndex(index).text())
            index = index.parent()
        return "/".join(reversed(parts))

    def create_folder(self, target_dir):
        if self.mode == "bfres_raw":
            return
        name, ok = QInputDialog.getText(
            self,
            self.tr("create_folder_dialog_title"),
            self.tr("create_folder_dialog_label")
        )
        if not ok or not name:
            return
        if "/" in name or "\\" in name:
            QMessageBox.critical(self, self.tr("error_dialog_title"),
                                 self.tr("msg_invalid_name"))
            return

        new_path = (target_dir + "/" if target_dir else "") + name

        exists_as_file = new_path in self.files
        exists_as_folder = (
            new_path in self.empty_folders
            or any(p == new_path or p.startswith(new_path + "/") for p in self.files)
        )
        if exists_as_file or exists_as_folder:
            QMessageBox.critical(
                self, self.tr("error_dialog_title"),
                self.tr("msg_folder_exists")
            )
            return

        self.empty_folders.add(new_path)
        self.populate_tree()

    def rename_item(self, index):
        old_path = self._get_full_path(index)
        item = self.model.itemFromIndex(index)
        is_file = item.data(Qt.UserRole) is not None
        is_folder = not is_file
        current_display = item.text()
        if is_file and " (" in current_display:
            current_name = current_display.rsplit(" (", 1)[0]
        else:
            current_name = current_display
        new_name, ok = QInputDialog.getText(self,
                                            self.tr("rename_dialog_title"),
                                            self.tr("rename_dialog_label"),
                                            text=current_name)
        if not ok or new_name == current_name or "/" in new_name or "\\" in new_name:
            return
        if is_folder:
            prefix = old_path + "/"
            new_prefix = os.path.dirname(old_path) + "/" + new_name
            if new_prefix == "/":
                new_prefix = new_name
            new_files = {}
            for p, d in self.files.items():
                new_p = p.replace(prefix, new_prefix, 1) if p.startswith(prefix) else p
                if new_p != p and new_p in self.files:
                    QMessageBox.critical(self, self.tr("error_dialog_title"),
                                         self.tr("msg_path_exists", new_p))
                    return
                new_files[new_p] = d
            self.files = new_files
            self.file_sizes = {p: len(d) for p, d in self.files.items()}
            new_empty = set()
            for f in self.empty_folders:
                if f == old_path:
                    new_empty.add(new_prefix)
                elif f.startswith(prefix):
                    new_empty.add(new_prefix + f[len(old_path):])
                else:
                    new_empty.add(f)
            self.empty_folders = new_empty
        else:
            dirname = os.path.dirname(old_path)
            new_path = (dirname + "/" if dirname else "") + new_name
            if new_path in self.files:
                QMessageBox.critical(self, self.tr("error_dialog_title"),
                                     self.tr("msg_file_exists", new_name))
                return
            data = self.files.pop(old_path)
            self.files[new_path] = data
            self.file_sizes[new_path] = len(data)
            del self.file_sizes[old_path]
        self.modified = True
        self.populate_tree()
        self._update_save_button_state()

    # Implementation details.

    @staticmethod
    def _split_stem(filename):
        """Trennt 'ObjectFoo.phive__ShapeParam.bgyml' in
        ('ObjectFoo', '.phive__ShapeParam.bgyml')."""
        dot = filename.find(".")
        if dot == -1:
            return filename, ""
        return filename[:dot], filename[dot:]

    @staticmethod
    def _ref_suffix(bgyml_suffix):
        """Implementation details."""
        if bgyml_suffix.endswith(".bgyml"):
            return bgyml_suffix[:-6] + ".gyml"
        if bgyml_suffix.endswith(".byml"):
            return bgyml_suffix[:-5] + ".yml"
        return bgyml_suffix

    @classmethod
    def _replace_token_in_value(cls, value, old_token, new_token):
        """Implementation details."""
        if not isinstance(value, str):
            return value, False
        slash_old = "/" + old_token
        if value.endswith(slash_old):
            return value[: -len(slash_old)] + "/" + new_token, True
        if value == old_token:
            return new_token, True
        return value, False

    @classmethod
    def _replace_token_recursive(cls, obj, old_token, new_token):
        """Implementation details."""
        if isinstance(obj, dict):
            changed = False
            new_d = {}
            for k, v in obj.items():
                nv, c = cls._replace_token_recursive(v, old_token, new_token)
                new_d[k] = nv
                changed = changed or c
            return new_d, changed
        if isinstance(obj, list):
            changed = False
            new_l = []
            for v in obj:
                nv, c = cls._replace_token_recursive(v, old_token, new_token)
                new_l.append(nv)
                changed = changed or c
            return new_l, changed
        if isinstance(obj, str):
            return cls._replace_token_in_value(obj, old_token, new_token)
        return obj, False

    def _build_rename_chain_plan(self, start_path, new_stem):
        """Implementation details."""
        old_filename = os.path.basename(start_path)
        old_stem, suffix = self._split_stem(old_filename)
        plan = []
        plan_paths = set()

        # 1) die ausgewaehlte Datei selbst wird immer umbenannt
        dirname = os.path.dirname(start_path)
        new_start_path = (dirname + "/" if dirname else "") + new_stem + suffix
        plan.append({
            "old_path": start_path,
            "new_path": new_start_path,
            "data": self.files[start_path],
            "renamed": True,
        })
        plan_paths.add(start_path)

        # Implementation details.
        queue = [(old_stem, suffix)]
        seen = {(old_stem, suffix)}

        while queue:
            search_stem, search_suffix = queue.pop(0)
            old_token = search_stem + self._ref_suffix(search_suffix)
            new_token = new_stem + self._ref_suffix(search_suffix)

            for path, data in self.files.items():
                if path in plan_paths:
                    continue
                try:
                    obj = parse_byml(data)
                    native = _byml_to_native(obj)
                except Exception:
                    continue  # keine byml/bgyml-Datei bzw. nicht lesbar -> ueberspringen

                new_native, changed = self._replace_token_recursive(native, old_token, new_token)
                if not changed:
                    continue

                new_obj = _native_to_byml(new_native)
                new_data = dump_byml(new_obj)

                filename = os.path.basename(path)
                file_stem, file_suffix = self._split_stem(filename)
                # Traegt die Datei schon den ZIEL-Namen? Dann ist dieser Teil
                # der Kette bereits fertig (unabhaengig vom bisherigen Namen).
                will_rename = (file_stem != new_stem)
                new_path = None
                if will_rename:
                    dname = os.path.dirname(path)
                    candidate = (dname + "/" if dname else "") + new_stem + file_suffix
                    if candidate in self.files or candidate == path:
                        will_rename = False  # Konflikt: nur Inhalt anpassen, nicht umbenennen
                    else:
                        new_path = candidate

                plan.append({
                    "old_path": path,
                    "new_path": new_path,
                    "data": new_data,
                    "renamed": will_rename,
                })
                plan_paths.add(path)

                if will_rename and (file_stem, file_suffix) not in seen:
                    seen.add((file_stem, file_suffix))
                    queue.append((file_stem, file_suffix))

        return plan

    def _show_rename_chain_preview(self, plan):
        """Zeigt eine Vorschau aller betroffenen Dateien und fragt Bestaetigung ab."""
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("rename_chain_preview_title"))
        dialog.resize(700, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tr("rename_chain_preview_label")))
        list_widget = QListWidget()
        for entry in plan:
            if entry["renamed"]:
                text = f"{entry['old_path']}  →  {entry['new_path']}   (Inhalt aktualisiert + umbenannt)"
            else:
                text = f"{entry['old_path']}   (nur Inhalt aktualisiert)"
            list_widget.addItem(text)
        layout.addWidget(list_widget)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(self.tr("rename_chain_apply"))
        cancel_btn = QPushButton(self.tr("editor_cancel"))
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        result = {"ok": False}
        def accept():
            result["ok"] = True
            dialog.accept()
        ok_btn.clicked.connect(accept)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()
        return result["ok"]

    def rename_with_chain(self, index):
        """Rename a file and update its reference chain."""
        old_path = self._get_full_path(index)
        if old_path not in self.files:
            return
        old_filename = os.path.basename(old_path)
        old_stem, suffix = self._split_stem(old_filename)

        new_name, ok = QInputDialog.getText(
            self,
            self.tr("rename_chain_dialog_title"),
            self.tr("rename_chain_dialog_label"),
            text=old_filename,
        )
        if not ok or not new_name or new_name == old_filename:
            return
        if "/" in new_name or "\\" in new_name:
            QMessageBox.critical(self, self.tr("error_dialog_title"),
                                 self.tr("msg_invalid_name"))
            return

        new_stem, new_suffix = self._split_stem(new_name)
        if new_suffix != suffix:
            QMessageBox.critical(
                self, self.tr("error_dialog_title"),
                self.tr("msg_suffix_must_match", suffix)
            )
            return

        dirname = os.path.dirname(old_path)
        target_path = (dirname + "/" if dirname else "") + new_name
        if target_path in self.files:
            QMessageBox.critical(self, self.tr("error_dialog_title"),
                                 self.tr("msg_file_exists", new_name))
            return

        plan = self._build_rename_chain_plan(old_path, new_stem)
        if not self._show_rename_chain_preview(plan):
            return

        for entry in plan:
            if entry["renamed"]:
                del self.files[entry["old_path"]]
                del self.file_sizes[entry["old_path"]]
                self.files[entry["new_path"]] = entry["data"]
                self.file_sizes[entry["new_path"]] = len(entry["data"])
            else:
                self.files[entry["old_path"]] = entry["data"]
                self.file_sizes[entry["old_path"]] = len(entry["data"])

        self.modified = True
        self.populate_tree()
        self._update_save_button_state()

    def delete_item(self, index):
        path = self._get_full_path(index)
        item = self.model.itemFromIndex(index)
        is_file = item.data(Qt.UserRole) is not None
        changed_files = False
        if not is_file:
            prefix = path + "/"
            to_delete = [p for p in self.files if p.startswith(prefix)]
            if QMessageBox.question(self, self.tr("delete_dialog_title"),
                                    self.tr("delete_confirm_folder", path, len(to_delete)),
                                    QMessageBox.Yes|QMessageBox.No) != QMessageBox.Yes:
                return
            for p in to_delete:
                del self.files[p], self.file_sizes[p]
            changed_files = bool(to_delete)
            self.empty_folders = {
                f for f in self.empty_folders
                if not (f == path or f.startswith(prefix))
            }
        else:
            if QMessageBox.question(self, self.tr("delete_dialog_title"),
                                    self.tr("delete_confirm_file", path),
                                    QMessageBox.Yes|QMessageBox.No) != QMessageBox.Yes:
                return
            del self.files[path], self.file_sizes[path]
            changed_files = True
        if changed_files:
            self.modified = True
            self._update_save_button_state()
        self.populate_tree()

    def export_item(self, index):
        path = self._get_full_path(index)
        if self.model.itemFromIndex(index).data(Qt.UserRole) is None:
            return
        data = self.files.get(path)
        if data:
            save_path, _ = QFileDialog.getSaveFileName(self, self.tr("export_dialog_title"), os.path.basename(path))
            if save_path:
                with open(save_path, "wb") as f:
                    f.write(data)

    def export_folder(self, index):
        folder_path = self._get_full_path(index)
        if folder_path:
            prefix = folder_path + "/"
        else:
            prefix = ""
        files_in_folder = {p: d for p, d in self.files.items() if p.startswith(prefix) or (not prefix and not p.startswith("/"))}
        if not files_in_folder:
            QMessageBox.information(self, self.tr("info_dialog_title", "Info"),
                                    self.tr("export_folder_empty", "The folder is empty."))
            return

        export_dir = QFileDialog.getExistingDirectory(
            self,
            self.tr("export_folder_dialog_title", "Choose export folder")
        )
        if not export_dir:
            return

        exported = 0
        errors = []
        for path, data in files_in_folder.items():
            if prefix:
                if path.startswith(prefix):
                    rel_path = path[len(prefix):]
                else:
                    rel_path = path
            else:
                rel_path = path
            rel_path = rel_path.replace("/", os.sep)
            if rel_path.startswith(os.sep):
                rel_path = rel_path[1:]

            if path.lower().endswith(('.byml', '.bgyml')):
                try:
                    obj = parse_byml(data)
                    native = _byml_to_native(obj)
                    json_str = json.dumps(native, indent=2, ensure_ascii=False, default=str)
                    if rel_path.lower().endswith('.byml'):
                        rel_path = rel_path[:-5] + '.json'
                    elif rel_path.lower().endswith('.bgyml'):
                        rel_path = rel_path[:-6] + '.json'
                    else:
                        rel_path += '.json'
                    out_path = os.path.join(export_dir, rel_path)
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(json_str)
                    exported += 1
                except Exception as e:
                    errors.append(f"{path}: {e}")
            elif path.lower().endswith('.asb') or data[:4] == self.ASB_MAGIC:
                try:
                    asb_obj = self._asb_parse(data)
                    json_str = json.dumps(asb_obj, indent=2, ensure_ascii=False, default=str)
                    out_path = os.path.join(export_dir, rel_path + ".json")
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(json_str)
                    exported += 1
                except Exception as e:
                    errors.append(f"{path}: {e}")
            else:
                out_path = os.path.join(export_dir, rel_path)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                try:
                    with open(out_path, "wb") as f:
                        f.write(data)
                    exported += 1
                except Exception as e:
                    errors.append(f"{path}: {e}")

        if errors:
            QMessageBox.warning(
                self,
                self.tr("export_partial_title", "Partial export"),
                self.tr("export_folder_errors", "Some files could not be exported:") + "\n" + "\n".join(errors[:10])
            )
        self.window().statusBar().showMessage(
            self.tr("export_folder_done", f"{exported} files exported to {export_dir}"),
            5000
        )

    def duplicate_file(self, index):
        if self.mode == "bfres_raw":
            return
        path = self._get_full_path(index)
        item = self.model.itemFromIndex(index)
        if item.data(Qt.UserRole) is None:
            return

        current_display = item.text()
        if " (" in current_display:
            current_name = current_display.rsplit(" (", 1)[0]
        else:
            current_name = current_display

        new_name, ok = QInputDialog.getText(
            self,
            self.texts.get("duplicate_dialog_title", "Duplicate file"),
            self.texts.get("duplicate_dialog_label", "New name:"),
            text=current_name
        )
        if not ok or not new_name or new_name == current_name:
            return
        if "/" in new_name or "\\" in new_name:
            QMessageBox.critical(self, self.tr("error_dialog_title"),
                                 self.texts.get("msg_invalid_name", "Invalid name."))
            return

        dirname = os.path.dirname(path)
        new_path = (dirname + "/" if dirname else "") + new_name
        if new_path in self.files:
            QMessageBox.critical(self, self.tr("error_dialog_title"),
                                 self.tr("msg_file_exists", new_name))
            return

        self.files[new_path] = self.files[path]
        self.file_sizes[new_path] = len(self.files[new_path])
        self.modified = True
        self.populate_tree()
        self._update_save_button_state()

    def add_files(self, target_dir):
        if self.mode == "bfres_raw":
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.texts.get("add_file_dialog_title", "Add file(s)"),
            "",
            "All files (*.*)"
        )
        if not paths:
            return

        for file_path in paths:
            base_name = os.path.basename(file_path)
            dest_path = (target_dir + "/" if target_dir else "") + base_name
            while dest_path in self.files:
                new_name, ok = QInputDialog.getText(
                    self,
                    self.texts.get("file_exists_title", "File already exists"),
                    self.texts.get("file_exists_label",
                                   "File '{0}' already exists. Enter a new name or cancel:").format(base_name),
                    text=base_name
                )
                if not ok or not new_name:
                    break
                if "/" in new_name or "\\" in new_name:
                    QMessageBox.critical(self, self.tr("error_dialog_title"),
                                         self.texts.get("msg_invalid_name", "Invalid name."))
                    continue
                dest_path = (target_dir + "/" if target_dir else "") + new_name
            else:
                try:
                    with open(file_path, "rb") as f:
                        data = f.read()
                    self.files[dest_path] = data
                    self.file_sizes[dest_path] = len(data)
                    self.modified = True
                    self.empty_folders.discard(target_dir)
                except Exception as e:
                    QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))

        self.populate_tree()
        self._update_save_button_state()

    def build_save_bytes(self):
        if self.mode == "sarc":
            writer = sarc.SARCWriter(be=False)
            for name, data in self.files.items():
                writer.add_file(name, data)
            out = io.BytesIO()
            writer.write(out)
            return zstd.ZstdCompressor(level=19).compress(out.getvalue())
        elif self.mode in ("byml_single", "asb_single"):
            return zstd.ZstdCompressor(level=19).compress(list(self.files.values())[0])
        raise ValueError("Saving is not supported")

    def save(self):
        if self.mode == "bfres_raw":
            QMessageBox.information(self, self.tr("info_dialog_title"), self.tr("msg_save_bfres_blocked"))
            return
        if not self.original_path:
            self.save_as()
            return
        try:
            with open(self.original_path, "wb") as f:
                f.write(self.build_save_bytes())
            self.modified = False
            self._update_save_button_state()
            self.window().statusBar().showMessage(self.tr("msg_saved"), 3000)
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))

    def save_as(self):
        if self.mode == "bfres_raw":
            QMessageBox.information(self, self.tr("info_dialog_title"), self.tr("msg_save_bfres_blocked"))
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr("save_as_dialog_title"), "",
                                              self.tr("file_filter_pack"))
        if path:
            try:
                with open(path, "wb") as f:
                    f.write(self.build_save_bytes())
                self.original_path = path
                self.modified = False
                self._update_save_button_state()
                self.window().statusBar().showMessage(self.tr("msg_saved"), 3000)
            except Exception as e:
                QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))

    def highlight_byml(self):
        search_text = self.byml_search_edit.text()
        if not search_text:
            self.text_edit.setExtraSelections([])
            return
        selections = []
        doc = self.text_edit.document()
        cursor = QTextCursor(doc)
        while True:
            cursor = doc.find(search_text, cursor)
            if cursor.isNull():
                break
            extra = QTextEdit.ExtraSelection()
            extra.format.setBackground(Qt.yellow)
            extra.cursor = cursor
            selections.append(extra)
        self.text_edit.setExtraSelections(selections)

    def refresh_texts(self, texts):
        self.texts = texts
        self.save_btn.setText(self.tr("editor_save"))
        self.save_as_btn.setText(self.tr("editor_save_as"))
        self.export_byml_btn.setText(self.texts.get("export_all_byml") or "Export all BYML to TXT")
        self.export_ainb_btn.setText(self.texts.get("export_all_ainb") or "Export all AINB as JSON")
        self.search_edit.setPlaceholderText(self.tr("search_placeholder"))
        self.save_current_btn.setText(self.tr("editor_save_current"))
        self.export_json_btn.setText(self.texts.get("export_json_btn", "Export JSON"))
        self.import_json_btn.setText(self.texts.get("import_json_btn", "Import JSON"))
        self.populate_tree()
        self._update_save_button_state()
        self._update_save_current_button_state()

    def update_io_buttons_state(self):
        has_byml = any(p.lower().endswith(('.byml', '.bgyml')) for p in self.files)
        has_ainb = any(p.lower().endswith('.ainb') or self._is_ainb_data(d) for p,d in self.files.items())
        has_asb = any(p.lower().endswith('.asb') or self._is_asb_data(d) for p,d in self.files.items())
        if hasattr(self, 'export_byml_btn'):
            self.export_byml_btn.setEnabled(has_byml)
        if hasattr(self, 'export_ainb_btn'):
            self.export_ainb_btn.setEnabled(has_ainb or has_asb)

    def export_all_byml(self):
        byml_paths = [p for p in self.files if p.lower().endswith(('.byml', '.bgyml'))]
        if not byml_paths:
            QMessageBox.information(self, self.tr("info_dialog_title"), self.tr("export_no_byml"))
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self, self.tr("export_txt_dialog_title"), "",
            self.tr("file_filter_txt") + " (*.txt)"
        )
        if not out_path:
            return
        output_lines = []
        for path in byml_paths:
            data = self.files[path]
            try:
                obj = parse_byml(data)
                native = _byml_to_native(obj)
                text = json.dumps(native, indent=2, ensure_ascii=False, default=str)
            except Exception:
                text = data.decode("utf-8", errors="replace")
                if len(text) > 50000:
                    text = text[:50000] + "\n... (truncated)"
            output_lines.append(f"===== {path} =====")
            output_lines.append(text)
            output_lines.append("")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))
            self.window().statusBar().showMessage(
                self.tr("export_done", len(byml_paths), out_path), 5000
            )
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))

    def export_all_ainb(self):
        target_paths = [
            p for p, d in self.files.items()
            if p.lower().endswith('.ainb') or self._is_ainb_data(d)
            or p.lower().endswith('.asb') or self._is_asb_data(d)
        ]
        if not target_paths:
            QMessageBox.information(
                self, self.tr("info_dialog_title", "Info"),
                self.tr("export_no_ainb", "No AINB/ASB files found.")
            )
            return

        export_dir = QFileDialog.getExistingDirectory(
            self,
            self.tr("export_ainb_folder_title", "Choose export folder")
        )
        if not export_dir:
            return

        exported = 0
        errors = []
        for path in target_paths:
            data = self.files[path]
            try:
                if path.lower().endswith('.asb') or self._is_asb_data(data):
                    asb_obj = self._asb_parse(data)
                    json_str = json.dumps(asb_obj, indent=2, ensure_ascii=False, default=str)
                else:
                    ainb_obj = ainb.AINB.from_binary(data)
                    json_str = ainb_obj.to_json()
                rel_path = path.replace("/", os.sep)
                if not rel_path.lower().endswith(('.ainb', '.asb')):
                    rel_path += '.ainb' if self._is_ainb_data(data) else '.asb'
                out_path = os.path.join(export_dir, rel_path + ".json")
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(json_str)
                exported += 1
            except Exception as e:
                errors.append(f"{path}: {e}")

        if errors:
            QMessageBox.warning(
                self,
                self.tr("export_partial_title", "Partial export"),
                self.tr("export_ainb_errors", "Some files could not be exported:") + "\n" + "\n".join(errors[:10])
            )
        self.window().statusBar().showMessage(
            self.tr("export_done_ainb_folder", f"{exported} AINB/ASB files exported to {export_dir}"), 5000
        )

    def export_current_json(self):
        if not self.current_edit_path:
            return
        base_name = os.path.basename(self.current_edit_path)
        suggest_name = f"{base_name}.json"
        save_path, _ = QFileDialog.getSaveFileName(
            self, self.tr("export_json_dialog_title", "Export JSON"),
            suggest_name,
            "JSON files (*.json);;All files (*.*)"
        )
        if not save_path:
            return
        try:
            text = self.text_edit.toPlainText()
            if self.current_edit_mode == "ainb" and "too large" in text:
                data = self.files[self.current_edit_path]
                ainb_obj = ainb.AINB.from_binary(data)
                text = ainb_obj.to_json()
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            self.window().statusBar().showMessage(
                self.tr("export_json_done", "Exported to {0}").format(save_path), 3000
            )
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))

    def import_current_json(self):
        if not self.current_edit_path:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, self.tr("import_json_dialog_title", "Import JSON"),
            "", "JSON files (*.json);;All files (*.*)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                json_str = f.read()
            if self.current_edit_mode in ("byml", "ainb", "asb"):
                if self.current_edit_mode == "byml":
                    native = json.loads(json_str)
                    _native_to_byml(native)
                elif self.current_edit_mode == "ainb":
                    new_dict = json.loads(json_str)
                    ainb.AINB.from_dict(new_dict)
                elif self.current_edit_mode == "asb":
                    new_dict = json.loads(json_str)
                    asb.ASB.from_dict(new_dict)
                self.text_edit.setPlainText(json_str)
                self.text_edit.setReadOnly(False)
                self.current_modified = True
                self._update_save_current_button_state()
                self.window().statusBar().showMessage(
                    self.tr("import_json_done", "Imported {0}. Use 'Save Current' to apply changes.").format(file_path),
                    5000
                )
            else:
                raise ValueError("Current file does not support JSON import.")
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), f"Import failed:\n{e}")


# Implementation details.

class PackEditorWidget(QWidget):
    def __init__(self, texts, parent=None):
        super().__init__(parent)
        self.texts = texts
        self.open_docs = {}
        self._actors_cache = []
        self._current_source = None  # "original" oder "mod"

        main_layout = QVBoxLayout(self)

        # Toolbar (obere Leiste)
        toolbar = QHBoxLayout()
        self.open_btn = QPushButton(self.tr("editor_open"))
        self.open_btn.clicked.connect(self.open_file)
        self.open_btn.setShortcut(QKeySequence.Open)
        toolbar.addWidget(self.open_btn)

        # Source-Auswahl (Original / Mod)
        toolbar.addWidget(QLabel(self.tr("source_switch") + ":"))
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(100)  # damit Texte nicht abgeschnitten werden
        self.source_combo.addItem(self.texts.get("clone_source_original", "Original"))
        self.source_combo.addItem(self.texts.get("clone_source_mod", "Mod"))
        self.source_combo.setToolTip(self.tr("switch_source_tooltip"))
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        toolbar.addWidget(self.source_combo)

        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # Hauptsplitter: links Actor-Liste mit Suchleiste, rechts Tabs
        self.splitter = QSplitter(Qt.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Suchleiste über der Actor-Liste
        self.search_actor_edit = QLineEdit()
        self.search_actor_edit.setPlaceholderText(self.tr("search_placeholder"))
        self.search_actor_edit.textChanged.connect(self.filter_actor_list)
        left_layout.addWidget(self.search_actor_edit)

        self.actor_list = QListWidget()
        self.actor_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.actor_list.itemDoubleClicked.connect(self.on_actor_double_clicked)
        left_layout.addWidget(self.actor_list)

        self.splitter.addWidget(left_widget)

        self.inner_tabs = QTabWidget()
        self.inner_tabs.setTabsClosable(True)
        self.inner_tabs.tabCloseRequested.connect(self.close_tab)
        self.splitter.addWidget(self.inner_tabs)

        # Set the initial sidebar width.
        self._set_initial_splitter_sizes()

        main_layout.addWidget(self.splitter)

        # Standardmäßig Mod auswählen
        self.source_combo.setCurrentIndex(1)

        # Actor-Liste erst laden, wenn das Widget vollständig initialisiert ist
        QTimer.singleShot(0, self.on_source_changed)

    def _set_initial_splitter_sizes(self):
        """Set the initial sidebar width."""
        # Standardbreite für den Fall, dass keine Actors geladen sind
        left_width = 200
        # Ermitteln der maximalen Textbreite der Actors (sobald geladen)
        if self._actors_cache:
            font = self.actor_list.font()
            fm = QFontMetrics(font)
            max_width = max(fm.horizontalAdvance(name) for name in self._actors_cache)
            left_width = max_width + 30  # etwas Padding
        self.splitter.setSizes([left_width, self.width() - left_width])

    def tr(self, key, *args):
        return self.texts.get(key, key).format(*args)

    def get_source_path(self):
        """Return the path for the selected source."""
        idx = self.source_combo.currentIndex()
        if idx == 0:
            return self.window().settings.get("last_source", "") if hasattr(self.window(), 'settings') else ""
        else:
            return self.window().settings.get("last_dest", "") if hasattr(self.window(), 'settings') else ""

    def on_source_changed(self):
        """Reload actors when the source changes."""
        src = self.get_source_path()
        self._current_source = "original" if self.source_combo.currentIndex() == 0 else "mod"
        self.load_actor_list(src)

    def load_actor_list(self, source_dir):
        """Load actor packs from the selected source."""
        self.actor_list.clear()
        if not source_dir:
            return
        pack_dir = os.path.join(source_dir, "Pack", "Actor")
        if not os.path.isdir(pack_dir):
            return
        actors = []
        for f in os.listdir(pack_dir):
            if f.endswith(".pack.zs"):
                actors.append(f[:-8])  # ohne .pack.zs
        self._actors_cache = sorted(actors)
        self.filter_actor_list()
        # Nach dem Laden die Anfangsbreite anpassen (falls noch nicht geschehen)
        if self.splitter.sizes()[0] == 0:
            self._set_initial_splitter_sizes()

    def filter_actor_list(self):
        """Filter actors by the search text."""
        search = self.search_actor_edit.text().strip().lower()
        self.actor_list.clear()
        for actor in self._actors_cache:
            if not search or search in actor.lower():
                self.actor_list.addItem(actor)

    def on_actor_double_clicked(self, item):
        """Open the selected actor pack."""
        actor_name = item.text()
        src = self.get_source_path()
        if not src:
            return
        pack_path = os.path.join(src, "Pack", "Actor", f"{actor_name}.pack.zs")
        if os.path.isfile(pack_path):
            self.load_file(pack_path)

    def open_file(self):
        """Open a file using the native dialog."""
        path, _ = QFileDialog.getOpenFileName(self, self.tr("open_file_dialog_title"), "",
                                              self.tr("file_filter_all_supported"))
        if path:
            self.load_file(path)

    def load_file(self, path):
        """Load a file into a new or existing tab."""
        norm = os.path.normcase(os.path.abspath(path))
        if norm in self.open_docs:
            self.inner_tabs.setCurrentWidget(self.open_docs[norm])
            return
        editor = SingleFileEditor(self.texts, path, self)
        self.open_docs[norm] = editor
        self.inner_tabs.addTab(editor, os.path.basename(path))
        self.inner_tabs.setCurrentWidget(editor)

    def update_tab_title(self, editor, modified):
        for i in range(self.inner_tabs.count()):
            if self.inner_tabs.widget(i) is editor:
                title = os.path.basename(editor.original_path) if editor.original_path else "Untitled"
                if modified:
                    self.inner_tabs.setTabText(i, title + " *")
                else:
                    self.inner_tabs.setTabText(i, title)
                break

    def close_tab(self, index):
        widget = self.inner_tabs.widget(index)
        if not widget.confirm_close():
            return
        if hasattr(widget, 'modified') and widget.modified:
            res = QMessageBox.question(self, self.tr("unsaved_changes_title"),
                                       self.tr("unsaved_changes_text"),
                                       QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if res == QMessageBox.Cancel:
                return
            if res == QMessageBox.Save:
                widget.save()
                if widget.modified:
                    return
        for k, v in list(self.open_docs.items()):
            if v is widget:
                del self.open_docs[k]
                break
        self.inner_tabs.removeTab(index)
        widget.deleteLater()

    def refresh_texts(self, texts):
        self.texts = texts
        self.open_btn.setText(self.tr("editor_open"))
        # Source-Combo-Einträge aktualisieren
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem(self.texts.get("clone_source_original", "Original"))
        self.source_combo.addItem(self.texts.get("clone_source_mod", "Mod"))
        self.source_combo.setCurrentIndex(1)  # Mod als Standard
        self.source_combo.blockSignals(False)
        self.search_actor_edit.setPlaceholderText(self.tr("search_placeholder"))
        # Tabs aktualisieren
        for i in range(self.inner_tabs.count()):
            self.inner_tabs.widget(i).refresh_texts(texts)
        # Actor-Liste neu laden
        self.on_source_changed()

    def get_open_tabs(self):
        return [editor.original_path for editor in self.open_docs.values() if editor.original_path]

    def restore_tabs(self, paths):
        for p in paths:
            if os.path.isfile(p):
                self.load_file(p)
