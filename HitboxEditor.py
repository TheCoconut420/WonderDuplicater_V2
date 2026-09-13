#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Implementation details."""

import os
import io
import copy

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Rectangle

import sarc
import zstandard as zstd

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, pyqtSignal
# QAction wird ueber "from PyQt5.QtWidgets import *" bereits bereitgestellt.

from FileHandler import (
    decompress_zs, parse_byml, dump_byml, _byml_to_native, _native_to_byml,
)


# Implementation details.

def extract_files_from_pack(pack_path: str) -> dict:
    data = decompress_zs(pack_path)
    archive = sarc.SARC(data=data)
    result = {}
    for name in archive.list_files():
        result[name] = bytes(archive.get_file_data(name))
    return result

def get_actor_list(files: dict) -> list:
    actors = []
    for path in files:
        if path.startswith("Actor/") and path.endswith(".engine__actor__ActorParam.bgyml"):
            base = os.path.basename(path)
            if base.endswith(".engine__actor__ActorParam.bgyml"):
                name = base[:-len(".engine__actor__ActorParam.bgyml")]
                actors.append(name)
    return sorted(actors)

def get_actor_param(files: dict, actor_name: str):
    path = f"Actor/{actor_name}.engine__actor__ActorParam.bgyml"
    if path not in files:
        return None
    data = files[path]
    obj = parse_byml(data)
    return _byml_to_native(obj)

def find_value_by_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = find_value_by_key(v, key)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_value_by_key(item, key)
            if result is not None:
                return result
    return None

def resolve_path(ref: str, files: dict):
    if not ref:
        return None, None
    if ref.startswith("Work/"):
        ref = ref[5:]
    ref = ref.replace("\\", "/")
    if ref not in files:
        if ref.endswith(".gyml"):
            alt = ref[:-5] + ".bgyml"
            if alt in files:
                ref = alt
        else:
            base = os.path.basename(ref)
            for f in files:
                if f.endswith("/" + base) or f == base:
                    ref = f
                    break
    if ref not in files:
        return None, None
    data = files[ref]
    try:
        obj = parse_byml(data)
        return _byml_to_native(obj), ref
    except Exception:
        return None, None

def collect_shape_params_for_actor(files: dict, actor_name: str):
    actor_param = get_actor_param(files, actor_name)
    if actor_param is None:
        return []
    game_physics_ref = find_value_by_key(actor_param, "GamePhysicsRef")
    if not game_physics_ref:
        return []
    gp, _ = resolve_path(game_physics_ref, files)
    if gp is None:
        return []
    controller_set_path = gp.get("ControllerSetPath")
    if not controller_set_path:
        return []
    cs, _ = resolve_path(controller_set_path, files)
    if cs is None:
        return []
    shape_path_ary = cs.get("ShapeNamePathAry", [])
    if not shape_path_ary:
        return []
    shape_params = []
    for entry in shape_path_ary:
        file_path = entry.get("FilePath")
        if not file_path:
            continue
        name = entry.get("Name", os.path.basename(file_path))
        sp, found_path = resolve_path(file_path, files)
        if sp is not None and found_path is not None:
            shape_params.append((name, sp, found_path))
    return shape_params


# Implementation details.

def _f(node, default=0.0):
    if isinstance(node, dict) and "value" in node:
        return float(node["value"])
    if isinstance(node, (int, float)):
        return float(node)
    return default

def _vec2(node):
    if not isinstance(node, dict):
        return np.array([0.0, 0.0])
    return np.array([_f(node.get("X")), _f(node.get("Y"))])

def _set_vec2_in_entry(entry, key, vec):
    if key not in entry or not isinstance(entry.get(key), dict):
        entry[key] = {}
    entry[key]["X"] = {"__byml_type__": "Float", "value": float(vec[0])}
    entry[key]["Y"] = {"__byml_type__": "Float", "value": float(vec[1])}
    if "Z" not in entry[key]:
        entry[key]["Z"] = {"__byml_type__": "Float", "value": 0.0}

def _set_float_in_entry(entry, key, value):
    entry[key] = {"__byml_type__": "Float", "value": float(value)}

def _one_vec3():
    one = {"__byml_type__": "Float", "value": 1.0}
    return {"X": dict(one), "Y": dict(one), "Z": dict(one)}

# Alle bekannten Geometrie-Felder ueber alle Shape-Typen hinweg - werden
# bei einem Typ-Wechsel entfernt, bevor die neuen Felder gesetzt werden.
_GEOMETRY_FIELD_KEYS = ("Center", "CenterA", "CenterB", "Radius", "HalfExtents")

def _build_entry_for_type(old_entry, new_type_key):
    """Implementation details."""
    new_entry = copy.deepcopy(old_entry) if isinstance(old_entry, dict) else {}
    for k in _GEOMETRY_FIELD_KEYS:
        new_entry.pop(k, None)

    if new_type_key == "Sphere":
        new_entry["Center"] = _one_vec3()
        new_entry["Radius"] = {"__byml_type__": "Float", "value": 1.0}
    elif new_type_key == "Capsule":
        new_entry["CenterA"] = _one_vec3()
        new_entry["CenterB"] = _one_vec3()
        new_entry["Radius"] = {"__byml_type__": "Float", "value": 1.0}
    elif new_type_key in ("Box", "Obb"):
        new_entry["HalfExtents"] = _one_vec3()

    return new_entry

def extract_shapes_2d_with_ref(shape_param):
    """Implementation details."""
    shapes = []
    for entry in shape_param.get("Sphere", []) or []:
        center = _vec2(entry.get("Center"))
        radius = _f(entry.get("Radius"), 0.1)
        shapes.append({
            "type": "circle",
            "center": center,
            "radius": radius,
            "entry": entry,
            "shape_key": "Sphere",
        })
    for entry in shape_param.get("Capsule", []) or []:
        start = _vec2(entry.get("CenterA"))
        end = _vec2(entry.get("CenterB"))
        center = (start + end) / 2
        radius = _f(entry.get("Radius"), 0.1)
        shapes.append({
            "type": "capsule",
            "center": center,
            "start": start,
            "end": end,
            "radius": radius,
            "entry": entry,
            "shape_key": "Capsule",
        })
    for key in ("Box", "Obb"):
        for entry in shape_param.get(key, []) or []:
            center = _vec2(entry.get("Center"))
            half_extent_node = entry.get("HalfExtents")
            half_extent = _vec2(half_extent_node) if half_extent_node else np.array([0.1, 0.1])
            shapes.append({
                "type": "rect",
                "center": center,
                "half_extent": half_extent,
                "entry": entry,
                "shape_key": key,
            })
    return shapes


# Implementation details.

def _theme_colors(theme):
    if theme == "dark":
        return {
            "fig": "#2e2e2e",
            "axes": "#3c3c3c",
            "text": "white",
            "grid": "#5a5a5a",
            "spine": "#5a5a5a",
            "ground": "#9e9e9e",
            "dim": "#777777",
        }
    return {
        "fig": "white",
        "axes": "white",
        "text": "black",
        "grid": "#cccccc",
        "spine": "black",
        "ground": "#555555",
        "dim": "#b0b0b0",
    }

# Implementation details.
HITBOX_COLOR_PALETTE = [
    "#e6194b",  # rot
    "#3cb44b",  # gruen
    "#4363d8",  # blau
    "#f58231",  # orange
    "#911eb4",  # lila
    "#42d4f4",  # cyan
    "#f032e6",  # magenta
    "#469990",  # teal
    "#e6beff",  # helllila
    "#c9a227",  # gold
    "#f5426d",  # pink
    "#17becf",  # tuerkis
]


# Implementation details.

class DeselectableListWidget(QListWidget):
    """Implementation details."""
    emptyClicked = pyqtSignal()

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        super().mousePressEvent(event)
        if item is None:
            self.clearSelection()
            self.emptyClicked.emit()


class PropertiesPanel(QWidget):
    """Implementation details."""

    # Anzeigename fuer das Dropdown - Obb wird nicht als Zieltyp angeboten,
    # ein Obb-Eintrag wird im Dropdown wie "Box" dargestellt.
    TYPE_OPTIONS = ["Sphere", "Capsule", "Box"]

    def __init__(self, texts=None, parent=None):
        super().__init__(parent)
        self.texts = texts or {}
        self.current_shape = None
        self.current_type = None
        self.on_change_callback = None
        self.on_type_change_callback = None
        self.updating = False

        self.setMinimumWidth(260)
        self.setMaximumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        title_label = QLabel(self.texts.get("hitbox_properties", "Eigenschaften"))
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title_label)

        self.name_label = QLabel(self.texts.get("hitbox_name", "Name:") + " -")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        form = QFormLayout()

        self.type_combo = QComboBox()
        self.type_combo.addItems(self.TYPE_OPTIONS)
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        form.addRow(self.texts.get("hitbox_shape_type", "Shape-Typ:"), self.type_combo)

        self.center_x = QDoubleSpinBox()
        self.center_x.setRange(-1000, 1000)
        self.center_x.setSingleStep(0.05)
        self.center_x.setDecimals(4)
        self.center_x.valueChanged.connect(self.on_value_changed)
        form.addRow(self.texts.get("hitbox_x", "X:"), self.center_x)

        self.center_y = QDoubleSpinBox()
        self.center_y.setRange(-1000, 1000)
        self.center_y.setSingleStep(0.05)
        self.center_y.setDecimals(4)
        self.center_y.valueChanged.connect(self.on_value_changed)
        form.addRow(self.texts.get("hitbox_y", "Y:"), self.center_y)

        self.radius_label = QLabel(self.texts.get("hitbox_radius", "Radius:"))
        self.radius_spin = QDoubleSpinBox()
        self.radius_spin.setRange(0.001, 1000)
        self.radius_spin.setSingleStep(0.05)
        self.radius_spin.setDecimals(4)
        self.radius_spin.valueChanged.connect(self.on_value_changed)
        form.addRow(self.radius_label, self.radius_spin)

        self.hx_label = QLabel(self.texts.get("hitbox_half_extent_x", "Kantenlänge X (halb):"))
        self.hx_spin = QDoubleSpinBox()
        self.hx_spin.setRange(0.001, 1000)
        self.hx_spin.setSingleStep(0.05)
        self.hx_spin.setDecimals(4)
        self.hx_spin.valueChanged.connect(self.on_value_changed)
        form.addRow(self.hx_label, self.hx_spin)

        self.hy_label = QLabel(self.texts.get("hitbox_half_extent_y", "Kantenlänge Y (halb):"))
        self.hy_spin = QDoubleSpinBox()
        self.hy_spin.setRange(0.001, 1000)
        self.hy_spin.setSingleStep(0.05)
        self.hy_spin.setDecimals(4)
        self.hy_spin.valueChanged.connect(self.on_value_changed)
        form.addRow(self.hy_label, self.hy_spin)

        layout.addLayout(form)
        layout.addStretch(1)

        self.set_enabled(False)
        self.clear_values()

    def refresh_texts(self, texts):
        self.texts = texts or {}
        # Labels neu setzen (Werte bleiben unangetastet)
        self.findChild(QLabel)  # no-op, hier nur zur Robustheit falls spaeter erweitert
        self.radius_label.setText(self.texts.get("hitbox_radius", "Radius:"))
        self.hx_label.setText(self.texts.get("hitbox_half_extent_x", "Kantenlänge X (halb):"))
        self.hy_label.setText(self.texts.get("hitbox_half_extent_y", "Kantenlänge Y (halb):"))
        if self.current_shape is None:
            self.name_label.setText(self.texts.get("hitbox_name", "Name:") + " -")

    def set_enabled(self, enabled):
        self.type_combo.setEnabled(enabled)
        self.center_x.setEnabled(enabled)
        self.center_y.setEnabled(enabled)
        self.radius_spin.setEnabled(enabled)
        self.hx_spin.setEnabled(enabled)
        self.hy_spin.setEnabled(enabled)

    def clear_values(self):
        self.updating = True
        self.center_x.setValue(0)
        self.center_y.setValue(0)
        self.radius_spin.setValue(0.1)
        self.hx_spin.setValue(0.1)
        self.hy_spin.setValue(0.1)
        self.name_label.setText(self.texts.get("hitbox_name", "Name:") + " -")
        self.radius_spin.hide()
        self.radius_label.hide()
        self.hx_spin.hide()
        self.hx_label.hide()
        self.hy_spin.hide()
        self.hy_label.hide()
        self.current_shape = None
        self.current_type = None
        self.updating = False

    @staticmethod
    def _display_type_for_shape_key(shape_key):
        if shape_key in ("Box", "Obb"):
            return "Box"
        return shape_key

    def set_shape(self, shape_data, display_name):
        self.updating = True
        self.current_shape = shape_data
        self.current_type = shape_data["type"]
        self.name_label.setText(self.texts.get("hitbox_name", "Name:") + f" {display_name}")
        self.set_enabled(True)

        display_type = self._display_type_for_shape_key(shape_data["shape_key"])
        idx = self.type_combo.findText(display_type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)

        self.radius_spin.hide()
        self.radius_label.hide()
        self.hx_spin.hide()
        self.hx_label.hide()
        self.hy_spin.hide()
        self.hy_label.hide()

        center = shape_data["center"]
        self.center_x.setValue(center[0])
        self.center_y.setValue(center[1])

        if self.current_type in ("circle", "capsule"):
            self.radius_spin.show()
            self.radius_label.show()
            self.radius_spin.setValue(shape_data["radius"])
        elif self.current_type == "rect":
            self.hx_spin.show()
            self.hx_label.show()
            self.hy_spin.show()
            self.hy_label.show()
            half = shape_data["half_extent"]
            self.hx_spin.setValue(half[0])
            self.hy_spin.setValue(half[1])

        self.updating = False

    def on_type_changed(self, new_display_type):
        if self.updating or self.current_shape is None or self.on_type_change_callback is None:
            return
        current_display = self._display_type_for_shape_key(self.current_shape["shape_key"])
        if new_display_type == current_display:
            return
        self.on_type_change_callback(self.current_shape, new_display_type)

    def on_value_changed(self):
        if self.updating or self.current_shape is None or self.on_change_callback is None:
            return

        typ = self.current_type
        center = np.array([self.center_x.value(), self.center_y.value()])

        if typ == "circle":
            radius = max(self.radius_spin.value(), 0.001)
            self.current_shape["center"] = center
            self.current_shape["radius"] = radius
            _set_vec2_in_entry(self.current_shape["entry"], "Center", center)
            _set_float_in_entry(self.current_shape["entry"], "Radius", radius)

        elif typ == "capsule":
            radius = max(self.radius_spin.value(), 0.001)
            old_center = (self.current_shape["start"] + self.current_shape["end"]) / 2
            delta = center - old_center
            self.current_shape["center"] = center
            self.current_shape["start"] = self.current_shape["start"] + delta
            self.current_shape["end"] = self.current_shape["end"] + delta
            self.current_shape["radius"] = radius
            _set_vec2_in_entry(self.current_shape["entry"], "CenterA", self.current_shape["start"])
            _set_vec2_in_entry(self.current_shape["entry"], "CenterB", self.current_shape["end"])
            _set_float_in_entry(self.current_shape["entry"], "Radius", radius)

        elif typ == "rect":
            hx = max(self.hx_spin.value(), 0.001)
            hy = max(self.hy_spin.value(), 0.001)
            self.current_shape["center"] = center
            self.current_shape["half_extent"] = np.array([hx, hy])
            _set_vec2_in_entry(self.current_shape["entry"], "Center", center)
            _set_vec2_in_entry(self.current_shape["entry"], "HalfExtents", np.array([hx, hy]))

        self.on_change_callback()


# Implementation details.

class HitboxEditorWidget(QWidget):
    def __init__(self, settings=None, texts=None, parent=None):
        super().__init__(parent)
        self.settings = settings or {}
        self.texts = texts or {}
        self.theme = self.settings.get("theme", "light")

        self.files = {}
        self.actor_names = []          # Namen aus Pack/Actor/*.pack.zs (Mod-Romfs)
        self.current_actor = None
        self.pack_path = None
        self.shape_params = []         # [(name, sp_data, path), ...]
        self.all_shapes = []           # flache Liste aller Hitboxen des aktuellen Actors
        self.selected_shape = None
        self.dirty_paths = set()

        self._init_ui()
        self.populate_actor_list()

    # Implementation details.
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        menubar = QMenuBar(self)
        file_menu = menubar.addMenu(self.texts.get("hitbox_menu_file", "Datei"))
        open_action = QAction(self.texts.get("hitbox_open_action", "Pack öffnen (Explorer)…"), self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_pack_dialog)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        save_action = QAction(self.texts.get("hitbox_save_action", "Speichern"), self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_pack)
        file_menu.addAction(save_action)
        self._menubar = menubar
        self._file_menu = file_menu
        self._open_action = open_action
        self._save_action = save_action
        layout.setMenuBar(menubar)

        toolbar = QToolBar()
        self.refresh_btn = QPushButton(self.texts.get("hitbox_refresh", "Aktualisieren"))
        self.refresh_btn.clicked.connect(self.populate_actor_list)
        toolbar.addWidget(self.refresh_btn)
        self.save_btn = QPushButton(self.texts.get("hitbox_save", "Speichern"))
        self.save_btn.clicked.connect(self.save_pack)
        toolbar.addWidget(self.save_btn)
        toolbar.addSeparator()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(self.texts.get("hitbox_filter_actor", "Actor filtern…"))
        self.filter_edit.textChanged.connect(self.filter_actor_list)
        self.filter_edit.setMinimumWidth(160)
        self.filter_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        toolbar.addWidget(self.filter_edit)
        self._toolbar = toolbar
        layout.addWidget(toolbar)

        outer_splitter = QSplitter(Qt.Horizontal)
        outer_splitter.setHandleWidth(8)
        outer_splitter.setChildrenCollapsible(False)

        # -- linke Spalte: Actor-Liste (oben) + Hitbox-Liste (unten) --
        left_splitter = QSplitter(Qt.Vertical)
        left_splitter.setHandleWidth(8)
        left_splitter.setChildrenCollapsible(False)

        actor_widget = QWidget()
        actor_layout = QVBoxLayout(actor_widget)
        actor_layout.setContentsMargins(0, 0, 0, 0)
        self.actor_list_label = QLabel(self.texts.get("hitbox_mod_actors", "Actors (Mod):"))
        actor_layout.addWidget(self.actor_list_label)
        self.actor_list = QListWidget()
        self.actor_list.setMinimumHeight(80)
        self.actor_list.itemClicked.connect(self.on_actor_selected)
        actor_layout.addWidget(self.actor_list)
        self.actor_widget = actor_widget
        left_splitter.addWidget(actor_widget)

        hitbox_widget = QWidget()
        hitbox_layout = QVBoxLayout(hitbox_widget)
        hitbox_layout.setContentsMargins(0, 0, 0, 0)
        self.hitbox_list_label = QLabel(self.texts.get("hitbox_hitboxes", "Hitboxen:"))
        hitbox_layout.addWidget(self.hitbox_list_label)
        self.hitbox_list = DeselectableListWidget()
        self.hitbox_list.setMinimumHeight(80)
        self.hitbox_list.itemClicked.connect(self.on_hitbox_selected)
        self.hitbox_list.emptyClicked.connect(self.on_hitbox_list_empty_clicked)
        hitbox_layout.addWidget(self.hitbox_list)
        left_splitter.addWidget(hitbox_widget)

        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 1)
        left_splitter.setSizes([250, 350])
        outer_splitter.addWidget(left_splitter)

        # -- rechte Spalte: Visualisierung + Eigenschaften-Panel --
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.figure = Figure(figsize=(6, 6), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout.addWidget(self.canvas)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        right_layout.addWidget(self.info_label)

        self.props_panel = PropertiesPanel(self.texts, self)
        self.props_panel.on_change_callback = self.on_property_changed
        self.props_panel.on_type_change_callback = self.on_type_changed
        right_layout.addWidget(self.props_panel)

        outer_splitter.addWidget(right_widget)
        outer_splitter.setSizes([300, 1000])
        layout.addWidget(outer_splitter)

        self._render_empty_plot()

    # Implementation details.
    def populate_actor_list(self):
        last_dest = self.settings.get("last_dest", "")
        pack_dir = os.path.join(last_dest, "Pack", "Actor") if last_dest else ""
        if last_dest and os.path.isdir(pack_dir):
            self.actor_names = sorted(
                f[:-len(".pack.zs")] for f in os.listdir(pack_dir) if f.endswith(".pack.zs")
            )
            self.actor_list.setEnabled(True)
            self.filter_edit.setEnabled(True)
        else:
            self.actor_names = []
            self.actor_list.setEnabled(False)
            self.filter_edit.setEnabled(False)
            self.info_label.setText(self.texts.get("hitbox_no_mod_dir", "Kein gültiges Mod-Verzeichnis eingestellt (siehe Settings)."))
        self._refill_actor_list()

    def _refill_actor_list(self):
        self.actor_list.clear()
        for name in self.actor_names:
            self.actor_list.addItem(name)
        self.filter_actor_list()

    def filter_actor_list(self):
        filter_text = self.filter_edit.text().strip().lower()
        for i in range(self.actor_list.count()):
            item = self.actor_list.item(i)
            item.setHidden(bool(filter_text) and filter_text not in item.text().lower())

    def on_actor_selected(self, item):
        name = item.text()
        last_dest = self.settings.get("last_dest", "")
        pack_path = os.path.join(last_dest, "Pack", "Actor", f"{name}.pack.zs")
        try:
            files = extract_files_from_pack(pack_path)
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))
            return
        self._load_from_files(files, pack_path, preferred_actor_name=name)

    def open_pack_dialog(self):
        """Fallback: beliebige .pack.zs-Datei per Explorer oeffnen (z.B.
        ausserhalb der eingestellten Mod-Romfs)."""
        path, _ = QFileDialog.getOpenFileName(
            self, self.texts.get("hitbox_open_dialog_title", "Pack-Datei öffnen"), "",
            self.texts.get("hitbox_file_filter", "Pack-Dateien (*.pack.zs);;Alle Dateien (*.*)"))
        if not path:
            return
        try:
            files = extract_files_from_pack(path)
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))
            return
        self._load_from_files(files, path)

    def _load_from_files(self, files, pack_path, preferred_actor_name=None):
        self.files = files
        self.pack_path = pack_path
        self.dirty_paths.clear()

        inner_actors = get_actor_list(files)
        actor_name = None
        if preferred_actor_name and preferred_actor_name in inner_actors:
            actor_name = preferred_actor_name
        elif inner_actors:
            actor_name = inner_actors[0]

        if actor_name is None:
            QMessageBox.information(self, self.tr("info_dialog_title"),
                                     self.texts.get("hitbox_pack_not_found", "In dieser Pack-Datei wurde kein Actor gefunden."))
            self.current_actor = None
            self.shape_params = []
            self._rebuild_hitbox_list()
            self._render_empty_plot()
            return

        self.current_actor = actor_name
        self.shape_params = collect_shape_params_for_actor(files, actor_name)
        if not self.shape_params:
            QMessageBox.information(self, self.tr("info_dialog_title"),
                                     self.texts.get("hitbox_no_shape_params", "Keine ShapeParams gefunden."))
        self._rebuild_hitbox_list()
        self.update_plot()

    # Implementation details.
    def _rebuild_hitbox_list(self):
        self.all_shapes = []
        for sp_name, sp_data, path in self.shape_params:
            shapes_2d = extract_shapes_2d_with_ref(sp_data)
            for shp in shapes_2d:
                shp["sp_name"] = sp_name
                shp["sp_data"] = sp_data
                shp["path"] = path
                self.all_shapes.append(shp)

        self.hitbox_list.clear()
        self.selected_shape = None
        self.props_panel.clear_values()
        self.props_panel.set_enabled(False)

        counters = {}
        for shp in self.all_shapes:
            entry_name = shp["entry"].get("Name")
            if entry_name:
                label = f"{shp['sp_name']} – {entry_name}"
            else:
                key = (shp["sp_name"], shp["shape_key"])
                counters[key] = counters.get(key, 0) + 1
                label = f"{shp['sp_name']} – {shp['shape_key']}"
            shp["label"] = label
            self.hitbox_list.addItem(label)

    def on_hitbox_selected(self, item):
        idx = self.hitbox_list.row(item)
        if idx < 0 or idx >= len(self.all_shapes):
            return
        self.selected_shape = self.all_shapes[idx]
        self.props_panel.set_shape(self.selected_shape, self.selected_shape["label"])
        self.update_plot()

    def on_hitbox_list_empty_clicked(self):
        """Klick in den leeren Bereich der Hitbox-Liste: Auswahl aufheben,
        damit wieder alle Hitboxen in ihren normalen Farben sichtbar sind."""
        self.selected_shape = None
        self.props_panel.clear_values()
        self.props_panel.set_enabled(False)
        self.update_plot()

    # Implementation details.
    def _render_empty_plot(self):
        self.figure.clear()
        colors = _theme_colors(self.theme)
        self.figure.patch.set_facecolor(colors["fig"])
        self.canvas.draw()

    def update_plot(self):
        self.figure.clear()
        colors = _theme_colors(self.theme)
        self.figure.patch.set_facecolor(colors["fig"])
        ax = self.figure.add_subplot(111)
        ax.set_facecolor(colors["axes"])

        if not self.all_shapes:
            ax.set_facecolor(colors["axes"])
            self.canvas.draw()
            return

        ax.axhline(y=0, color=colors["ground"], linestyle='--', linewidth=0.8, alpha=0.7)

        all_centers = []
        all_radii = []
        all_half_extents = []
        legend_handles = []
        legend_labels = []

        for idx, shp in enumerate(self.all_shapes):
            base_color = HITBOX_COLOR_PALETTE[idx % len(HITBOX_COLOR_PALETTE)]
            is_selected = self.selected_shape is not None and shp is self.selected_shape
            has_selection = self.selected_shape is not None
            if has_selection and not is_selected:
                self._draw_shape(ax, shp, colors["dim"], dim=True)
            else:
                self._draw_shape(ax, shp, base_color, highlight=is_selected)

            legend_handles.append(plt.Line2D([0], [0], color=base_color, linewidth=2.5))
            legend_labels.append(shp["label"])

            if shp["type"] in ("circle", "capsule"):
                all_centers.append(shp["center"])
                all_radii.append(shp["radius"])
            elif shp["type"] == "rect":
                all_centers.append(shp["center"])
                all_half_extents.append(shp["half_extent"])

        if all_centers:
            xs = [c[0] for c in all_centers]
            ys = [c[1] for c in all_centers]
            max_r = max(all_radii) if all_radii else 0.5
            if all_half_extents:
                max_he = max(max(h) for h in all_half_extents)
                max_r = max(max_r, max_he)
            ax.set_xlim(min(xs) - max_r - 0.5, max(xs) + max_r + 0.5)
            ax.set_ylim(min(ys) - max_r - 0.5, max(ys) + max_r + 0.5)

        ax.set_xlabel("X", color=colors["text"])
        ax.set_ylabel("Y", color=colors["text"])
        title = f"Actor: {self.current_actor}"
        ax.set_title(title, color=colors["text"])
        ax.tick_params(colors=colors["text"])
        for spine in ax.spines.values():
            spine.set_color(colors["spine"])
        ax.grid(True, linestyle=':', alpha=0.5, color=colors["grid"])
        ax.set_aspect('equal')

        if legend_handles:
            legend = ax.legend(legend_handles, legend_labels, loc='upper right', fontsize='small')
            legend.get_frame().set_facecolor(colors["axes"])
            legend.get_frame().set_edgecolor(colors["spine"])
            for text in legend.get_texts():
                text.set_color(colors["text"])

        self.canvas.draw()
        self.info_label.setText(title)

    def _draw_shape(self, ax, shp, color, highlight=False, dim=False):
        if highlight:
            linewidth = 3.2
            alpha = 1.0
        elif dim:
            linewidth = 1.0
            alpha = 0.45
        else:
            linewidth = 1.8
            alpha = 1.0
        if shp["type"] == "circle":
            patch = Circle(shp["center"], shp["radius"], fill=False, edgecolor=color, linewidth=linewidth, alpha=alpha)
            ax.add_patch(patch)
        elif shp["type"] == "capsule":
            for c in (shp["start"], shp["end"]):
                patch = Circle(c, shp["radius"], fill=False, edgecolor=color, linewidth=linewidth, alpha=alpha)
                ax.add_patch(patch)
            ax.plot([shp["start"][0], shp["end"][0]], [shp["start"][1], shp["end"][1]],
                    color=color, linewidth=linewidth, alpha=alpha)
        elif shp["type"] == "rect":
            center = shp["center"]
            half = shp["half_extent"]
            x0 = center[0] - half[0]
            y0 = center[1] - half[1]
            patch = Rectangle((x0, y0), 2 * half[0], 2 * half[1], fill=False, edgecolor=color, linewidth=linewidth, alpha=alpha)
            ax.add_patch(patch)

    # Implementation details.
    def mark_dirty_for_shape(self, shape_data):
        path = shape_data.get("path")
        if path:
            self.dirty_paths.add(path)

    def on_property_changed(self):
        if self.selected_shape is not None:
            self.mark_dirty_for_shape(self.selected_shape)
            self.update_plot()

    def on_type_changed(self, shape_data, new_display_type):
        old_key = shape_data["shape_key"]
        sp_data = shape_data["sp_data"]
        old_entry = shape_data["entry"]

        old_list = sp_data.get(old_key, [])
        for i, e in enumerate(old_list):
            if e is old_entry:
                old_list.pop(i)
                break

        new_entry = _build_entry_for_type(old_entry, new_display_type)
        sp_data.setdefault(new_display_type, []).append(new_entry)

        self.mark_dirty_for_shape(shape_data)

        # Hitbox-/Shape-Liste neu aufbauen, damit shape_key/entry/typ
        # konsistent aus den (jetzt geaenderten) sp_data-Arrays kommen.
        selected_path = shape_data.get("path")
        selected_sp_name = shape_data.get("sp_name")
        self._rebuild_hitbox_list()

        # Versuchen, den soeben konvertierten Eintrag wieder auszuwaehlen
        # (letzter Eintrag im neuen Array fuer diese ShapeParam-Datei).
        for i in range(len(self.all_shapes) - 1, -1, -1):
            shp = self.all_shapes[i]
            if shp["path"] == selected_path and shp["sp_name"] == selected_sp_name and shp["entry"] is new_entry:
                self.selected_shape = shp
                self.hitbox_list.setCurrentRow(i)
                self.props_panel.set_shape(shp, shp["label"])
                break

        self.update_plot()

    # Implementation details.
    def save_pack(self):
        if not self.pack_path or not self.files:
            QMessageBox.warning(self, self.tr("error_dialog_title"),
                                 self.texts.get("hitbox_no_actor_selected", "Kein Actor ausgewählt."))
            return
        if not self.dirty_paths:
            QMessageBox.information(self, self.tr("info_dialog_title"), self.texts.get("hitbox_no_changes", "No changes made."))
            return

        for name, sp_data, path in self.shape_params:
            if path in self.dirty_paths:
                try:
                    byml_obj = _native_to_byml(sp_data)
                    byml_data = dump_byml(byml_obj)
                    self.files[path] = byml_data
                except Exception as e:
                    QMessageBox.critical(self, self.tr("error_dialog_title"),
                                          self.texts.get("hitbox_save_error", "Error at {0}: {1}").format(path, e))
                    return

        try:
            writer = sarc.SARCWriter(be=False)
            for name, data in self.files.items():
                writer.add_file(name, data)
            out = io.BytesIO()
            writer.write(out)
            compressed = zstd.ZstdCompressor(level=19).compress(out.getvalue())
            with open(self.pack_path, "wb") as f:
                f.write(compressed)
            self.dirty_paths.clear()
            self.info_label.setText(self.texts.get("hitbox_pack_saved_status", "Pack saved: {0}").format(self.pack_path))
            QMessageBox.information(self, self.tr("info_dialog_title"), self.texts.get("hitbox_pack_saved_success", "Pack file saved successfully."))
        except Exception as e:
            QMessageBox.critical(self, self.tr("error_dialog_title"), str(e))

    # Implementation details.
    def set_theme(self, theme):
        if theme == self.theme:
            return
        self.theme = theme
        if self.all_shapes:
            self.update_plot()
        else:
            self._render_empty_plot()

    def refresh_texts(self, texts):
        self.texts = texts or {}
        self._file_menu.setTitle(self.texts.get("hitbox_menu_file", "Datei"))
        self._open_action.setText(self.texts.get("hitbox_open_action", "Pack öffnen (Explorer)…"))
        self._save_action.setText(self.texts.get("hitbox_save_action", "Speichern"))
        self.refresh_btn.setText(self.texts.get("hitbox_refresh", "Aktualisieren"))
        self.save_btn.setText(self.texts.get("hitbox_save", "Speichern"))
        self.filter_edit.setPlaceholderText(self.texts.get("hitbox_filter_actor", "Actor filtern…"))
        self.actor_list_label.setText(self.texts.get("hitbox_mod_actors", "Actors (Mod):"))
        self.hitbox_list_label.setText(self.texts.get("hitbox_hitboxes", "Hitboxen:"))
        self.props_panel.refresh_texts(self.texts)
        if self.all_shapes:
            self.update_plot()

    def tr(self, key, *args):
        return self.texts.get(key, key).format(*args)
