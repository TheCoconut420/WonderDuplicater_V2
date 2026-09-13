#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Implementation details."""

import os
import io
import json

import sarc

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QEvent, pyqtSignal
from PyQt5.QtGui import QColor

from FileHandler import (
    parse_byml, dump_byml, decompress_zs, compress_and_write_zs,
    byml_copy, byml_find_field, get_byml_leaf_type, make_byml_int,
    _native_to_byml,
)

# Import für den visuellen Hitbox-Editor (wird später im Hitbox-Tab eingebettet)
from HitboxEditor import HitboxEditorWidget, get_actor_list, collect_shape_params_for_actor


# Implementation details.

def set_button_dirty(btn, dirty):
    """Style a button to reflect its dirty state."""
    if dirty:
        btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
    else:
        btn.setStyleSheet("")


# Implementation details.

KNOWN_VALUES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "actor_wizard_known_values.json")

# Nur die Kategorien – die eigentlichen Werte kommen aus der JSON.
KNOWN_CATEGORIES = [
    "ReactionType", "DeathType", "DamageType", "AtType", "AtAttribute",
    "LayerSensor", "SubLayerSensor", "EnableLayerHitMask", "EnableSubLayerHitMask",
]

# Felder von RigidBodySensorParam, die im Hitbox-Verhalten-Tab als Dropdown
# (mit den obigen KNOWN_CATEGORIES-Kategorien) bearbeitet werden.
HITBOX_SENSOR_FIELDS = ["LayerSensor", "SubLayerSensor", "EnableLayerHitMask", "EnableSubLayerHitMask"]

def load_known_values():
    if not os.path.isfile(KNOWN_VALUES_FILE):
        empty = {cat: {} for cat in KNOWN_CATEGORIES}
        save_known_values(empty)
        return empty
    try:
        with open(KNOWN_VALUES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for cat in KNOWN_CATEGORIES:
            if cat not in data:
                data[cat] = {}
        return data
    except Exception:
        return {cat: {} for cat in KNOWN_CATEGORIES}

def save_known_values(data):
    with open(KNOWN_VALUES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def add_known_value(category, value):
    if not value:
        return
    data = load_known_values()
    data.setdefault(category, {})
    if value not in data[category]:
        data[category][value] = ""
        save_known_values(data)


# Implementation details.

def get_tooltip(known_values, category, key):
    if category in known_values and key in known_values[category]:
        return known_values[category][key]
    return ""


# Implementation details.

def load_pack_files(pack_path):
    data = decompress_zs(pack_path)
    archive = sarc.SARC(data=data)
    return {n: bytes(archive.get_file_data(n)) for n in archive.list_files()}

def write_pack_files(pack_path, files):
    writer = sarc.SARCWriter(be=False)
    for n, d in files.items():
        writer.add_file(n, d)
    out = io.BytesIO()
    writer.write(out)
    compress_and_write_zs(pack_path, out.getvalue())

def work_path_to_pack_path(work_path):
    p = work_path
    if p.startswith("Work/"):
        p = p[len("Work/"):]
    if p.endswith(".gyml"):
        p = p[:-len(".gyml")] + ".bgyml"
    return p

def pack_path_to_work_path(pack_path):
    p = pack_path
    if p.endswith(".bgyml"):
        p = p[:-len(".bgyml")] + ".gyml"
    return "Work/" + p

def actor_param_path_for(actor_name):
    return f"Actor/{actor_name}.engine__actor__ActorParam.bgyml"

def resolve_parent_chain(files, obj, max_depth=10):
    chain = [obj]
    current = obj
    depth = 0
    while isinstance(current, dict) and current.get("$parent") and depth < max_depth:
        parent_pack_path = work_path_to_pack_path(current["$parent"])
        if parent_pack_path not in files:
            break
        current = parse_byml(files[parent_pack_path])
        chain.append(current)
        depth += 1
    return chain

def rename_and_rewire(files, actor_name, ref_holder_path, ref_field_path, target_pack_path):
    if target_pack_path not in files:
        return None

    directory, filename = target_pack_path.rsplit("/", 1)
    suffix = filename.split(".", 1)[1] if "." in filename else filename
    new_filename = f"{actor_name}.{suffix}"
    new_path = f"{directory}/{new_filename}"

    if new_path == target_pack_path:
        return target_pack_path

    files[new_path] = files.pop(target_pack_path)

    holder_obj = parse_byml(files[ref_holder_path])
    node = _ensure_nested_path(files, holder_obj, ref_field_path[:-1])
    if node is None:
        return None
    node[ref_field_path[-1]] = pack_path_to_work_path(new_path)
    files[ref_holder_path] = dump_byml(holder_obj)

    return new_path

def _lookup_path(obj, path):
    """Läuft `path` rein lesend ab (ohne etwas anzulegen) und gibt den Wert
    zurück, oder None, falls der Pfad an irgendeiner Stelle nicht existiert."""
    node = obj
    for key in path:
        if isinstance(key, int):
            if not isinstance(node, list) or key >= len(node):
                return None
            node = node[key]
        else:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
    return node

def _ensure_nested_path(files, obj, path):
    """Implementation details."""
    node = obj
    chain = None
    container = None
    container_key = None
    for i, key in enumerate(path):
        if isinstance(key, int):
            if not isinstance(node, list) or key >= len(node):
                if chain is None:
                    chain = resolve_parent_chain(files, obj)
                inherited = None
                for level in chain[1:]:
                    candidate = _lookup_path(level, path[:i])
                    if isinstance(candidate, list) and key < len(candidate):
                        inherited = candidate
                        break
                if inherited is None:
                    return None
                node = byml_copy(inherited)
                if container is not None:
                    container[container_key] = node
            container = node
            container_key = key
            node = node[key]
        else:
            next_key = path[i + 1] if i + 1 < len(path) else None
            expects_list = isinstance(next_key, int)
            current = node.get(key) if isinstance(node, dict) else None
            if expects_list:
                if not isinstance(current, list):
                    node[key] = []
            else:
                if not isinstance(current, dict):
                    node[key] = {}
            container = node
            container_key = key
            node = node[key]
    return node

def copy_and_rewire(files, actor_name, ref_holder_path, ref_field_path, target_pack_path):
    if target_pack_path not in files:
        return None
    directory, filename = target_pack_path.rsplit("/", 1)
    suffix = filename.split(".", 1)[1] if "." in filename else filename
    new_path = f"{directory}/{actor_name}.{suffix}"
    if new_path == target_pack_path:
        return target_pack_path

    files[new_path] = files[target_pack_path]  # Kopie - Original bleibt bestehen

    holder_obj = parse_byml(files[ref_holder_path])
    node = _ensure_nested_path(files, holder_obj, ref_field_path[:-1])
    if node is None:
        return None
    node[ref_field_path[-1]] = pack_path_to_work_path(new_path)
    files[ref_holder_path] = dump_byml(holder_obj)

    return new_path

def copy_and_add_dict_entry(files, actor_name, holder_path, dict_key, entry_key, target_pack_path):
    if target_pack_path not in files:
        return None
    directory, filename = target_pack_path.rsplit("/", 1)
    suffix = filename.split(".", 1)[1] if "." in filename else filename
    new_path = f"{directory}/{actor_name}.{suffix}"
    if new_path != target_pack_path:
        files[new_path] = files[target_pack_path]

    holder_obj = parse_byml(files[holder_path])
    if not isinstance(holder_obj.get(dict_key), dict):
        holder_obj[dict_key] = {}
    holder_obj[dict_key][entry_key] = pack_path_to_work_path(new_path)
    files[holder_path] = dump_byml(holder_obj)
    return new_path

def get_effective_dict(files, obj, dict_key=None):
    chain = resolve_parent_chain(files, obj)

    def get_map(level):
        if not isinstance(level, dict):
            return {}
        if dict_key is None:
            return {k: v for k, v in level.items() if k != "$parent"}
        m = level.get(dict_key)
        return dict(m) if isinstance(m, dict) else {}

    own_keys = set(get_map(chain[0]).keys())
    merged = {}
    for level in reversed(chain):
        merged.update(get_map(level))

    return {k: {"value": v, "source": "own" if k in own_keys else "inherited"} for k, v in merged.items()}

def resolve_actor_component(ctx, key):
    ap_obj = parse_byml(ctx.files[ctx.actor_param_path])
    comps = ap_obj.get("Components") if isinstance(ap_obj, dict) else None
    work_ref = comps.get(key) if isinstance(comps, dict) else None
    if not work_ref:
        return None, None, False
    pack_path = work_path_to_pack_path(work_ref)
    if pack_path not in ctx.files:
        return work_ref, pack_path, False
    owner = pack_path.rsplit("/", 1)[1].split(".", 1)[0]
    return work_ref, pack_path, (owner == ctx.actor_name)

def ensure_own_actor_component(ctx, key):
    work_ref, pack_path, is_own = resolve_actor_component(ctx, key)
    if pack_path is None or pack_path not in ctx.files:
        return None
    if is_own:
        return pack_path
    return copy_and_rewire(ctx.files, ctx.actor_name, ctx.actor_param_path, ["Components", key], pack_path)

def resolve_game_parameter_component(ctx, key):
    _, gpt_pack_path, _ = resolve_actor_component(ctx, "GameParameterTableRef")
    if gpt_pack_path is None or gpt_pack_path not in ctx.files:
        return None, None, False
    gpt_obj = parse_byml(ctx.files[gpt_pack_path])
    effective = get_effective_dict(ctx.files, gpt_obj, dict_key="Components")
    if key not in effective:
        return gpt_pack_path, None, False
    own_components = gpt_obj.get("Components") if isinstance(gpt_obj, dict) else None
    is_own = isinstance(own_components, dict) and key in own_components
    target_pack_path = work_path_to_pack_path(effective[key]["value"])
    return gpt_pack_path, target_pack_path, is_own

def ensure_own_game_parameter_component(ctx, key):
    gpt_pack_path, target_pack_path, is_own = resolve_game_parameter_component(ctx, key)
    if gpt_pack_path is None or target_pack_path is None:
        return None

    gpt_owner = gpt_pack_path.rsplit("/", 1)[1].split(".", 1)[0]
    if gpt_owner != ctx.actor_name:
        old_work_ref = pack_path_to_work_path(gpt_pack_path)
        directory, filename = gpt_pack_path.rsplit("/", 1)
        suffix = filename.split(".", 1)[1] if "." in filename else filename
        new_gpt_path = f"{directory}/{ctx.actor_name}.{suffix}"
        ctx.files[new_gpt_path] = dump_byml({"$parent": old_work_ref, "Components": {}})

        ap_obj = parse_byml(ctx.files[ctx.actor_param_path])
        if not isinstance(ap_obj.get("Components"), dict):
            ap_obj["Components"] = {}
        ap_obj["Components"]["GameParameterTableRef"] = pack_path_to_work_path(new_gpt_path)
        ctx.files[ctx.actor_param_path] = dump_byml(ap_obj)

        gpt_pack_path = new_gpt_path
        is_own = False

    if not is_own:
        target_pack_path = copy_and_add_dict_entry(
            ctx.files, ctx.actor_name, gpt_pack_path, "Components", key, target_pack_path
        )

    return target_pack_path


# Implementation details.

def flatten_byml_leaves(obj, prefix="", exclude_top_level=None):
    """Implementation details."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "$parent":
                continue
            if not prefix and exclude_top_level and k in exclude_top_level:
                continue
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                results.extend(flatten_byml_leaves(v, path, exclude_top_level))
            else:
                results.append((path, v, obj, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                results.extend(flatten_byml_leaves(v, path, exclude_top_level))
            else:
                results.append((path, v, obj, i))
    return results


class GenericFieldEditor(QWidget):
    valueChanged = pyqtSignal()

    def __init__(self, texts=None, parent=None):
        super().__init__(parent)
        self.texts = texts or {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([
            self.texts.get("generic_editor_field", "Feld"),
            self.texts.get("generic_editor_value", "Wert")
        ])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 200)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.table)
        self._entries = []

    def load_object(self, obj, exclude_top_level=None):
        self.table.setRowCount(0)
        self._entries = []
        for path, value, container, key in flatten_byml_leaves(obj, exclude_top_level=exclude_top_level):
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(path)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            edit = QLineEdit(str(value))
            edit.textChanged.connect(lambda: self.valueChanged.emit())
            self.table.setCellWidget(row, 1, edit)
            self._entries.append((container, key, type(value)))

    def apply_to_object(self):
        for row in range(self.table.rowCount()):
            container, key, orig_type = self._entries[row]
            text = self.table.cellWidget(row, 1).text()
            try:
                if orig_type is bool:
                    new_val = text.strip().lower() in ("1", "true", "yes", "ja")
                elif issubclass(orig_type, int):
                    new_val = orig_type(int(text))
                elif issubclass(orig_type, float):
                    new_val = orig_type(float(text))
                else:
                    new_val = text
            except (ValueError, TypeError):
                continue
            container[key] = new_val


# Implementation details.

class WizardContext:
    def __init__(self, texts=None, log_fn=None):
        self.texts = texts or {}
        self.log = log_fn or (lambda msg: None)
        self.pack_path = None
        self.files = None
        self.actor_name = None
        self.actor_param_path = None
        self.dirty = False

    def tr(self, key, *args):
        return self.texts.get(key, key).format(*args)

    @property
    def loaded(self):
        return self.files is not None

    def load(self, pack_path, actor_name):
        self.pack_path = pack_path
        self.actor_name = actor_name
        self.files = load_pack_files(pack_path)
        self.actor_param_path = actor_param_path_for(actor_name)
        self.dirty = False
        return self.actor_param_path in self.files

    def save(self):
        if not self.loaded:
            return
        write_pack_files(self.pack_path, self.files)
        self.dirty = False
        self.log(self.tr("actor_wizard_log_pack_saved", self.pack_path))

    def mark_dirty(self):
        self.dirty = True


# Implementation details.

def find_field_path(obj, field, path=None):
    if path is None:
        path = []
    if isinstance(obj, dict):
        if field in obj:
            return path + [field]
        for k, v in obj.items():
            if k == "$parent":
                continue
            result = find_field_path(v, field, path + [k])
            if result is not None:
                return result
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            result = find_field_path(v, field, path + [i])
            if result is not None:
                return result
    return None

def resolve_damage_reaction_ref(ctx):
    """Implementation details."""
    ap_obj = parse_byml(ctx.files[ctx.actor_param_path])
    chain = resolve_parent_chain(ctx.files, ap_obj)

    ref_field_path = None
    ref = None
    for level in chain:
        candidate_path = find_field_path(level, "DamageReactionRef")
        if candidate_path is None:
            continue
        node = level
        for key in candidate_path[:-1]:
            node = node[key]
        candidate_ref = node[candidate_path[-1]]
        if candidate_ref:
            ref_field_path = candidate_path
            ref = candidate_ref
            break
        if ref_field_path is None:
            # Feld existiert auf dieser Ebene, ist aber leer - merken, falls
            # in keiner weiteren (Parent-)Ebene ein besetzter Wert gefunden wird.
            ref_field_path = candidate_path

    if not ref_field_path:
        return None, None, False
    if not ref:
        return ref_field_path, None, False

    pack_path = work_path_to_pack_path(ref)
    if pack_path not in ctx.files:
        return ref_field_path, pack_path, False
    owner = pack_path.rsplit("/", 1)[1].split(".", 1)[0]
    return ref_field_path, pack_path, (owner == ctx.actor_name)

def list_damage_reaction_states(ctx, damage_param_path):
    if not damage_param_path or damage_param_path not in ctx.files:
        return []
    damage_param_obj = parse_byml(ctx.files[damage_param_path])
    chain = resolve_parent_chain(ctx.files, damage_param_obj)
    state_array = None
    for level in chain:
        if isinstance(level, dict) and level.get("StateArray"):
            state_array = level["StateArray"]
            break
    if not state_array:
        return []
    result = []
    for i, state in enumerate(state_array):
        if not isinstance(state, dict):
            continue
        base_ref = state.get("BaseTableFilePath")
        if not base_ref:
            continue
        table_pack_path = work_path_to_pack_path(base_ref)
        exists = table_pack_path in ctx.files
        is_own = False
        if exists:
            owner = table_pack_path.rsplit("/", 1)[1].split(".", 1)[0]
            is_own = (owner == ctx.actor_name)

        # Implementation details.
        real_name = state.get("Name") or state.get("StateName") or state.get("State")
        state_name = real_name
        if not state_name:
            # Versuche, den Namen aus dem Dateinamen der Table zu extrahieren
            if table_pack_path:
                filename = table_pack_path.rsplit("/", 1)[-1]
                name_part = filename.split(".", 1)[0]
                if name_part and "." in name_part:
                    name_part = name_part.split(".")[-1]
                if name_part:
                    state_name = name_part
        if not state_name:
            state_name = f"State {i}"
        result.append({
            "index": i,
            "label": state_name,
            "real_name": real_name,
            "table_pack_path": table_pack_path,
            "exists": exists,
            "is_own": is_own,
        })
    return result

def get_effective_damage_table(files, table_obj):
    chain = resolve_parent_chain(files, table_obj)
    own_keys = set(k for k in chain[0].keys() if k != "$parent") if isinstance(chain[0], dict) else set()

    merged = {}
    for level in reversed(chain):
        if not isinstance(level, dict):
            continue
        for key, value in level.items():
            if key == "$parent":
                continue
            merged[key] = value

    result = {}
    for key, value in merged.items():
        result[key] = {"entry": value, "source": "own" if key in own_keys else "inherited"}
    return result

def ensure_own_damage_reaction_param(ctx):
    ref_field_path, pack_path, is_own = resolve_damage_reaction_ref(ctx)
    if not ref_field_path or pack_path is None or pack_path not in ctx.files:
        return None
    if is_own:
        return pack_path
    return copy_and_rewire(ctx.files, ctx.actor_name, ctx.actor_param_path, ref_field_path, pack_path)

def ensure_own_damage_reaction_table(ctx, damage_param_path, state_index):
    if damage_param_path not in ctx.files:
        return None
    damage_param_obj = parse_byml(ctx.files[damage_param_path])

    chain = resolve_parent_chain(ctx.files, damage_param_obj)
    state_array = None
    for level in chain:
        if isinstance(level, dict) and level.get("StateArray"):
            state_array = level["StateArray"]
            break

    if not state_array or state_index >= len(state_array):
        return None
    base_ref = state_array[state_index].get("BaseTableFilePath")
    if not base_ref:
        return None
    table_pack_path = work_path_to_pack_path(base_ref)
    if table_pack_path not in ctx.files:
        return None
    owner = table_pack_path.rsplit("/", 1)[1].split(".", 1)[0]
    if owner == ctx.actor_name:
        return table_pack_path
    return copy_and_rewire(
        ctx.files, ctx.actor_name, damage_param_path,
        ["StateArray", state_index, "BaseTableFilePath"], table_pack_path,
    )

def make_custom_damage_reaction_table(ctx, state_index):
    damage_param_path = ensure_own_damage_reaction_param(ctx)
    if not damage_param_path:
        return None, None
    table_pack_path = ensure_own_damage_reaction_table(ctx, damage_param_path, state_index)
    return damage_param_path, table_pack_path

def _unwrap_int_like(rn):
    if rn is None:
        return None
    if isinstance(rn, dict):
        if "value" in rn:
            return _unwrap_int_like(rn["value"])
        return None
    try:
        return int(rn)
    except (TypeError, ValueError):
        return None

def _rewrap_int_like(existing, new_value):
    if isinstance(existing, dict) and "__byml_type__" in existing:
        new_dict = dict(existing)
        new_dict["value"] = int(new_value)
        return new_dict
    leaf_type = get_byml_leaf_type(existing)
    if leaf_type is not None:
        return leaf_type(int(new_value))
    return make_byml_int(new_value)

def extract_entry_values(entry):
    reaction_type = None
    death_type = None
    receive_num = None
    if isinstance(entry, dict):
        rp = entry.get("ReactionParam")
        if isinstance(rp, dict):
            reaction_type = rp.get("ReactionType")
        dp = entry.get("DeathParam")
        if isinstance(dp, dict):
            death_type = dp.get("DeathType")
        receive_num = _unwrap_int_like(entry.get("ReceiveNumToDie"))
    return reaction_type, death_type, receive_num

def apply_entry_values(entry, reaction_type, death_type, receive_num):
    if reaction_type:
        rp = entry.get("ReactionParam")
        if not isinstance(rp, dict):
            rp = {}
            entry["ReactionParam"] = rp
        rp["ReactionType"] = reaction_type
    elif isinstance(entry.get("ReactionParam"), dict):
        entry["ReactionParam"].pop("ReactionType", None)
        if not entry["ReactionParam"]:
            del entry["ReactionParam"]

    if death_type:
        dp = entry.get("DeathParam")
        if not isinstance(dp, dict):
            dp = {}
            entry["DeathParam"] = dp
        dp["DeathType"] = death_type
    elif isinstance(entry.get("DeathParam"), dict):
        entry["DeathParam"].pop("DeathType", None)
        if not entry["DeathParam"]:
            del entry["DeathParam"]

    if receive_num is None:
        entry.pop("ReceiveNumToDie", None)
    else:
        entry["ReceiveNumToDie"] = _rewrap_int_like(entry.get("ReceiveNumToDie"), receive_num)


class NoScrollComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()

    def showPopup(self):
        super().showPopup()


class TooltipComboBox(NoScrollComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tooltip_data = {}
        self._category = ""
        self.currentIndexChanged.connect(self._update_tooltip)

    def set_tooltip_data(self, category, tooltip_dict):
        self._category = category
        self._tooltip_dict = tooltip_dict
        self._update_tooltip()

    def _update_tooltip(self):
        current_text = self.currentText()
        if current_text and self._category in self._tooltip_dict:
            tooltip = self._tooltip_dict[self._category].get(current_text, "")
            if tooltip:
                self.setToolTip(tooltip)
            else:
                self.setToolTip("")

    def addItem(self, text, userData=None):
        idx = self.count()
        super().addItem(text, userData)
        if self._category in self._tooltip_dict:
            tooltip = self._tooltip_dict[self._category].get(text, "")
            if tooltip:
                self.setItemData(idx, tooltip, Qt.ToolTipRole)

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def setCurrentText(self, text):
        super().setCurrentText(text)
        self._update_tooltip()


class NoScrollSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class DamageWizardTab(QWidget):
    def __init__(self, ctx: WizardContext, texts=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.texts = texts or {}
        self.damage_param_path = None
        self.table_pack_path = None
        self.states = []
        self.selected_state_index = None
        self.known_values = load_known_values()
        self.dirty = False

        layout = QVBoxLayout(self)

        state_row = QHBoxLayout()
        self.state_label = QLabel(self.texts["actor_wizard_select_state"])
        self.state_label.setToolTip(self.texts["actor_wizard_state_tooltip"])
        state_row.addWidget(self.state_label)
        self.state_combo = QComboBox()
        self.state_combo.setToolTip(self.texts["actor_wizard_state_tooltip"])
        self.state_combo.currentIndexChanged.connect(self.on_state_selected)
        state_row.addWidget(self.state_combo, stretch=1)
        layout.addLayout(state_row)

        self.custom_table_btn = QPushButton(self.texts["actor_wizard_create_custom_table"])
        self.custom_table_btn.setToolTip(self.texts["actor_wizard_custom_table_btn_tooltip"])
        self.custom_table_btn.setEnabled(False)
        self.custom_table_btn.clicked.connect(self.make_custom_table)
        layout.addWidget(self.custom_table_btn)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            self.texts["actor_wizard_damage_type_column"],
            self.texts["actor_wizard_source_column"],
            self.texts["actor_wizard_reaction_type_column"],
            self.texts["actor_wizard_death_type_column"],
            self.texts["actor_wizard_receive_num_column"],
        ])
        header = self.table.horizontalHeader()
        for i in range(5):
            header.setSectionResizeMode(i, QHeaderView.Stretch)

        header_items = [
            (0, "actor_wizard_damagetype_tooltip"),
            (1, "actor_wizard_source_tooltip"),
            (2, "actor_wizard_reactiontype_tooltip"),
            (3, "actor_wizard_deathtype_tooltip"),
            (4, "actor_wizard_receivenum_tooltip"),
        ]
        for col, key in header_items:
            item = self.table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(self.texts[key])

        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        layout.addWidget(self.table, stretch=1)

        add_row = QHBoxLayout()
        self.add_combo = QComboBox()
        add_row.addWidget(self.add_combo, stretch=1)
        add_btn = QPushButton(self.texts["actor_wizard_add_inherited"])
        add_btn.clicked.connect(self.add_inherited_entry)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        btn_row = QHBoxLayout()
        remove_btn = QPushButton(self.texts["actor_wizard_remove_row"])
        remove_btn.clicked.connect(self.remove_selected_row)
        btn_row.addWidget(remove_btn)
        self.apply_btn = QPushButton(self.texts["actor_wizard_apply_changes"])
        self.apply_btn.setToolTip(self.texts["actor_wizard_apply_tooltip"])
        self.apply_btn.clicked.connect(self.apply_table)
        btn_row.addWidget(self.apply_btn)
        layout.addLayout(btn_row)

        self.setEnabled(False)

    def on_pack_loaded(self):
        self.damage_param_path = None
        self.table_pack_path = None
        self.states = []
        self.selected_state_index = None
        self.table.setRowCount(0)
        self.add_combo.clear()
        self.state_combo.blockSignals(True)
        self.state_combo.clear()
        self.state_combo.blockSignals(False)
        self.custom_table_btn.setEnabled(False)
        self.setEnabled(False)
        self.dirty = False
        set_button_dirty(self.apply_btn, False)

        if not self.ctx.loaded or not self.ctx.actor_param_path:
            return

        if self.ctx.actor_param_path not in self.ctx.files:
            return

        ref_field_path, damage_param_path, is_own = resolve_damage_reaction_ref(self.ctx)
        if not damage_param_path or damage_param_path not in self.ctx.files:
            return

        self.damage_param_path = damage_param_path
        self.states = list_damage_reaction_states(self.ctx, damage_param_path)
        if not self.states:
            return

        self.setEnabled(True)
        self.state_combo.blockSignals(True)
        for state in self.states:
            self.state_combo.addItem(state["label"])
        self.state_combo.blockSignals(False)
        self.state_combo.setCurrentIndex(0)
        self.on_state_selected(0)

    def on_state_selected(self, combo_index):
        if combo_index < 0 or combo_index >= len(self.states):
            return
        state = self.states[combo_index]
        self.selected_state_index = state["index"]
        self.table_pack_path = state["table_pack_path"] if state["exists"] else None

        if not state["exists"]:
            self.custom_table_btn.setEnabled(False)
            self.table.setRowCount(0)
            self.add_combo.clear()
            return

        self.custom_table_btn.setEnabled(True)
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self.refresh_table()

    def make_custom_table(self):
        if self.selected_state_index is None:
            return
        new_damage_param_path, new_table_pack_path = make_custom_damage_reaction_table(
            self.ctx, self.selected_state_index
        )
        if not new_damage_param_path or not new_table_pack_path:
            QMessageBox.warning(
                self,
                self.texts["actor_wizard_error_title"],
                self.texts["actor_wizard_error_cannot_create_own_table"]
            )
            self.ctx.log(self.texts["actor_wizard_log_custom_table_failed"])
            return
        self.ctx.log(
            self.texts["actor_wizard_log_damage_table_created"].format(
                new_damage_param_path, new_table_pack_path
            )
        )
        self.on_pack_loaded()

    def refresh_table(self):
        table_obj = parse_byml(self.ctx.files[self.table_pack_path])
        effective = get_effective_damage_table(self.ctx.files, table_obj)

        self.table.setRowCount(0)

        own_keys = []
        inherited_keys = []
        for key in effective.keys():
            if effective[key]["source"] == "own":
                own_keys.append(key)
            else:
                inherited_keys.append(key)
        own_keys.sort()
        inherited_keys.sort()
        sorted_keys = own_keys + inherited_keys

        for key in sorted_keys:
            info = effective[key]
            reaction_type, death_type, receive_num = extract_entry_values(info["entry"])
            self._add_row(key, info["source"], reaction_type, death_type, receive_num)

        self.add_combo.clear()
        own_keys_set = set(k for k in table_obj.keys() if k != "$parent") if isinstance(table_obj, dict) else set()
        for key in sorted(effective.keys()):
            if key not in own_keys_set:
                self.add_combo.addItem(key)

    def _add_row(self, damage_type, source, reaction_type, death_type, receive_num):
        row = self.table.rowCount()
        self.table.insertRow(row)

        item = QTableWidgetItem(damage_type)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        tooltip = get_tooltip(self.known_values, "DamageType", damage_type)
        if tooltip:
            item.setToolTip(tooltip)
        self.table.setItem(row, 0, item)

        source_text = self.texts["actor_wizard_own"] if source == "own" else self.texts["actor_wizard_inherited"]
        source_item = QTableWidgetItem(source_text)
        source_item.setFlags(source_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 1, source_item)

        reaction_combo = self._create_tooltip_combo("ReactionType", reaction_type)
        reaction_combo.setEnabled(source == "own")
        reaction_combo.currentTextChanged.connect(lambda: self._mark_dirty())
        self.table.setCellWidget(row, 2, reaction_combo)

        death_combo = self._create_tooltip_combo("DeathType", death_type)
        death_combo.setEnabled(source == "own")
        death_combo.currentTextChanged.connect(lambda: self._mark_dirty())
        self.table.setCellWidget(row, 3, death_combo)

        spin = NoScrollSpinBox()
        spin.setRange(-1, 999)
        spin.setSpecialValueText(self.texts["actor_wizard_not_set"])
        spin.setValue(receive_num if receive_num is not None else -1)
        spin.setEnabled(source == "own")
        spin.valueChanged.connect(lambda: self._mark_dirty())
        self.table.setCellWidget(row, 4, spin)

    def _create_tooltip_combo(self, category, current_value):
        combo = TooltipComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(140)

        combo.set_tooltip_data(category, self.known_values)

        values = sorted(self.known_values.get(category, {}).keys())
        for value in values:
            combo.addItem(value)

        if current_value:
            index = combo.findText(current_value)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.addItem(current_value)
                idx = combo.count() - 1
                combo.setCurrentIndex(idx)
                tooltip = get_tooltip(self.known_values, category, current_value)
                if tooltip:
                    combo.setItemData(idx, tooltip, Qt.ToolTipRole)
                    combo._update_tooltip()
        else:
            combo.setCurrentIndex(0)

        return combo

    def _mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            set_button_dirty(self.apply_btn, True)

    def add_inherited_entry(self):
        key = self.add_combo.currentText()
        if not key:
            return
        table_obj = parse_byml(self.ctx.files[self.table_pack_path])
        effective = get_effective_damage_table(self.ctx.files, table_obj)
        if key not in effective:
            return
        table_obj[key] = byml_copy(effective[key]["entry"])
        self.ctx.files[self.table_pack_path] = dump_byml(table_obj)
        self.ctx.mark_dirty()
        self._mark_dirty()
        self.refresh_table()

    def remove_selected_row(self):
        row = self.table.currentRow()
        if row < 0:
            return
        key = self.table.item(row, 0).text()
        table_obj = parse_byml(self.ctx.files[self.table_pack_path])
        if key in table_obj:
            del table_obj[key]
            self.ctx.files[self.table_pack_path] = dump_byml(table_obj)
            self.ctx.mark_dirty()
            self._mark_dirty()
            self.refresh_table()
        else:
            QMessageBox.information(
                self,
                self.texts["actor_wizard_info_title"],
                self.texts["actor_wizard_info_only_inherited"].format(key)
            )

    def apply_table(self):
        table_obj = parse_byml(self.ctx.files[self.table_pack_path])
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).text()
            reaction_combo = self.table.cellWidget(row, 2)
            death_combo = self.table.cellWidget(row, 3)
            spin = self.table.cellWidget(row, 4)

            reaction_type = reaction_combo.currentText().strip()
            death_type = death_combo.currentText().strip()
            receive_num = spin.value()
            receive_num = None if receive_num < 0 else receive_num

            if reaction_type:
                add_known_value("ReactionType", reaction_type)
            if death_type:
                add_known_value("DeathType", death_type)

            if key not in table_obj:
                continue
            entry = table_obj[key]
            if not isinstance(entry, dict):
                entry = {}
                table_obj[key] = entry
            apply_entry_values(entry, reaction_type, death_type, receive_num)

        self.ctx.files[self.table_pack_path] = dump_byml(table_obj)
        self.known_values = load_known_values()
        self.ctx.mark_dirty()
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self.ctx.log(self.texts["actor_wizard_log_damage_table_updated"].format(self.table_pack_path))


# Implementation details.

class ComponentSection(QGroupBox):
    def __init__(self, title_key, resolve_fn, ensure_own_fn, texts=None, parent=None):
        super().__init__(texts[title_key] if texts else title_key, parent)
        self.texts = texts or {}
        self.resolve_fn = resolve_fn
        self.ensure_own_fn = ensure_own_fn
        self.ctx = None
        self.pack_path = None
        self.current_obj = None
        self.dirty = False

        layout = QVBoxLayout(self)

        self.status_label = QLabel(self.texts["actor_wizard_no_pack_loaded"])
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.own_btn = QPushButton(self.texts["actor_wizard_make_own_file"])
        self.own_btn.setEnabled(False)
        self.own_btn.clicked.connect(self.make_own)
        layout.addWidget(self.own_btn)

        self.editor = GenericFieldEditor(texts=self.texts)
        self.editor.valueChanged.connect(self._on_editor_change)
        layout.addWidget(self.editor)

        self.apply_btn = QPushButton(self.texts["actor_wizard_apply_changes"])
        self.apply_btn.clicked.connect(self.apply_changes)
        layout.addWidget(self.apply_btn)

    def _on_editor_change(self):
        if not self.dirty:
            self.dirty = True
            set_button_dirty(self.apply_btn, True)

    def refresh(self, ctx):
        self.ctx = ctx
        self.pack_path = None
        self.current_obj = None
        self.editor.load_object({})
        self.own_btn.setEnabled(False)
        self.dirty = False
        set_button_dirty(self.apply_btn, False)

        if not ctx.loaded:
            self.status_label.setText(self.texts["actor_wizard_no_pack_loaded"])
            return

        pack_path, is_own = self.resolve_fn(ctx)
        if not pack_path:
            self.status_label.setText(self.texts["actor_wizard_no_reference"])
            return

        self.pack_path = pack_path
        if is_own:
            self.status_label.setText(self.texts["actor_wizard_file_own"].format(pack_path))
            self.own_btn.setEnabled(False)
            self.current_obj = parse_byml(ctx.files[pack_path])
            self.editor.load_object(self.current_obj)
        else:
            self.status_label.setText(
                self.texts["actor_wizard_file_inherited"].format(pack_path)
            )
            self.own_btn.setEnabled(True)

    def make_own(self):
        if not self.ctx:
            return
        new_path = self.ensure_own_fn(self.ctx)
        if new_path:
            self.ctx.mark_dirty()
            self.ctx.log(self.texts["actor_wizard_log_own_file_created"].format(new_path))
            self.refresh(self.ctx)
        else:
            QMessageBox.warning(
                self,
                self.texts["actor_wizard_error_title"],
                self.texts["actor_wizard_error_cannot_create_own_file"]
            )

    def apply_changes(self):
        if not self.ctx or not self.pack_path or self.current_obj is None:
            return
        self.editor.apply_to_object()
        self.ctx.files[self.pack_path] = dump_byml(self.current_obj)
        self.ctx.mark_dirty()
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self.ctx.log(self.texts["actor_wizard_log_physics_updated"].format(self.pack_path))


def _resolve_physics(ctx):
    _, pack_path, is_own = resolve_actor_component(ctx, "GamePhysicsRef")
    return pack_path, is_own

def _ensure_own_physics(ctx):
    return ensure_own_actor_component(ctx, "GamePhysicsRef")

def _resolve_throw(ctx):
    _, target_pack_path, is_own = resolve_game_parameter_component(ctx, "ThrowParam")
    return target_pack_path, is_own

def _ensure_own_throw(ctx):
    return ensure_own_game_parameter_component(ctx, "ThrowParam")

def _resolve_speed(ctx):
    _, target_pack_path, is_own = resolve_game_parameter_component(ctx, "SpeedSetParam")
    return target_pack_path, is_own

def _ensure_own_speed(ctx):
    return ensure_own_game_parameter_component(ctx, "SpeedSetParam")


class PhysicsWizardTab(QWidget):
    def __init__(self, ctx: WizardContext, texts=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.texts = texts or {}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        self.physics_section = ComponentSection(
            "actor_wizard_physics_section",
            _resolve_physics, _ensure_own_physics, texts=self.texts
        )
        self.throw_section = ComponentSection(
            "actor_wizard_throw_section",
            _resolve_throw, _ensure_own_throw, texts=self.texts
        )
        self.speed_section = ComponentSection(
            "actor_wizard_speed_section",
            _resolve_speed, _ensure_own_speed, texts=self.texts
        )

        layout.addWidget(self.physics_section)
        layout.addWidget(self.throw_section)
        layout.addWidget(self.speed_section)
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

        self.sections = [self.physics_section, self.throw_section, self.speed_section]

    def on_pack_loaded(self):
        for section in self.sections:
            section.refresh(self.ctx)


SHAPE_SUFFIX = ".phive__ShapeParam.bgyml"

RBSENSOR_DIR = "Phive/RigidBodySensorParam"
RBSENSOR_SUFFIX = ".phive__RigidBodySensorParam.bgyml"
RBCTRLSENSOR_DIR = "Phive/RigidBodyControllerSensorParam"
RBCTRLSENSOR_SUFFIX = ".phive__RigidBodyControllerSensorParam.bgyml"

CONTROLLERSET_ARRAY_KEYS = ("ShapeNamePathAry", "RigidBodySensorNamePathAry", "ControllerSensorNamePathAry")

def find_shape_param_files(files):
    return sorted(n for n in files if n.endswith(SHAPE_SUFFIX))

def find_rigid_body_sensor_files(files):
    return sorted(n for n in files if n.endswith(RBSENSOR_SUFFIX))

def find_shape_path_for_name(ctx, shape_name):
    """Implementation details."""
    if not shape_name:
        return None
    _, _, controllerset_pack_path, _ = resolve_controller_set_ref(ctx)
    if not controllerset_pack_path or controllerset_pack_path not in ctx.files:
        return None
    cs_obj = parse_byml(ctx.files[controllerset_pack_path])
    chain = resolve_parent_chain(ctx.files, cs_obj)
    for level in chain:
        if not isinstance(level, dict):
            continue
        for entry in level.get("ShapeNamePathAry", []) or []:
            if isinstance(entry, dict) and entry.get("Name") == shape_name and entry.get("FilePath"):
                return work_path_to_pack_path(entry["FilePath"])
    return None


def hitbox_logical_name(actor_name, base_name):
    prefix = f"{actor_name}_"
    if actor_name and base_name.startswith(prefix):
        return base_name[len(prefix):]
    return base_name

KNOWN_ACTOR_CATEGORY_PREFIXES = ["Enemy", "Object"]

def strip_category_prefix(name):
    for prefix in KNOWN_ACTOR_CATEGORY_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name

def _find_array_entry(array, file_work_path):
    if not isinstance(array, list):
        return None
    for entry in array:
        if isinstance(entry, dict) and entry.get("FilePath") == file_work_path:
            return entry
    return None

def _append_unique_entry(array, entry):
    for e in array:
        if isinstance(e, dict) and e.get("FilePath") == entry.get("FilePath"):
            return False
    array.append(entry)
    return True

def _unique_array_name(array, desired):
    existing = {e.get("Name") for e in array if isinstance(e, dict)}
    if desired not in existing:
        return desired
    i = 2
    while f"{desired}{i}" in existing:
        i += 1
    return f"{desired}{i}"

def _default_rigid_body_sensor(shape_name):
    return {
        "EnableLayerHitMask": "HitAll",
        "EnableSubLayerHitMask": "HitAll",
        "LayerSensor": "EnemyAttack",
        "MotionType": "Kinematic",
        "ShapeName": shape_name,
        "SubLayerSensor": "Unspecified",
    }

def _default_controller_unit(name):
    return {
        "BoneBindModePosition": "All",
        "BoneBindModeRotation": "All",
        "ContactCollectionName": "Main",
        "IsAddToWorldOnReset": True,
        "IsSkipTrackingWhenNoHit": True,
        "IsTrackingActor": True,
        "IsTrackingEntity": False,
        "IsTrackingMainRigidBody": False,
        "Name": name,
        "TrackingBoneName": "",
        "TrackingEntityAlias": "",
        "TreatStartTrackAsAddedToWorld": False,
        "WarpMode": "AfterUpdateWorldMtx",
    }

def _find_named_entry(array, name):
    if not isinstance(array, list):
        return None
    for e in array:
        if isinstance(e, dict) and e.get("Name") == name:
            return e
    return None

def _is_own_pack_path(actor_name, pack_path):
    filename = pack_path.rsplit("/", 1)[1]
    stem = filename.split(".", 1)[0]
    return stem == actor_name or stem.startswith(actor_name + "_")

def ensure_general_controller_unit(ctx, cs_obj, unit_name):
    """Implementation details."""
    array = cs_obj.setdefault("ControllerSensorNamePathAry", [])
    entry = _find_named_entry(array, "General")

    if entry is None:
        ctrl_path = f"{RBCTRLSENSOR_DIR}/{ctx.actor_name}_{unit_name}{RBCTRLSENSOR_SUFFIX}"
        ctx.files[ctrl_path] = dump_byml({"RigidBodyControllerUnitAry": [_default_controller_unit(unit_name)]})
        _append_unique_entry(array, {"FilePath": pack_path_to_work_path(ctrl_path), "Name": "General"})
        return ctrl_path

    general_pack_path = work_path_to_pack_path(entry["FilePath"])
    if general_pack_path in ctx.files:
        rc_obj = byml_copy(parse_byml(ctx.files[general_pack_path]))
    else:
        rc_obj = {}

    units = rc_obj.get("RigidBodyControllerUnitAry")
    if not isinstance(units, list):
        units = []
        rc_obj["RigidBodyControllerUnitAry"] = units

    if not any(isinstance(u, dict) and u.get("Name") == unit_name for u in units):
        units.append(_default_controller_unit(unit_name))

    if _is_own_pack_path(ctx.actor_name, general_pack_path):
        target_path = general_pack_path
    else:
        # fremde/geerbte Datei - eigene Kopie anlegen statt eines
        # gemeinsam genutzten Templates schreibend zu veraendern
        target_path = f"{RBCTRLSENSOR_DIR}/{ctx.actor_name}_General{RBCTRLSENSOR_SUFFIX}"
        entry["FilePath"] = pack_path_to_work_path(target_path)

    ctx.files[target_path] = dump_byml(rc_obj)
    return target_path

def resolve_controller_set_ref(ctx):
    _, physics_pack_path, _ = resolve_actor_component(ctx, "GamePhysicsRef")
    if not physics_pack_path or physics_pack_path not in ctx.files:
        return None, None, None, False

    physics_obj = parse_byml(ctx.files[physics_pack_path])
    chain = resolve_parent_chain(ctx.files, physics_obj)

    ref_field_path = None
    ref = None
    for level in chain:
        candidate_path = find_field_path(level, "ControllerSetPath")
        if candidate_path is None:
            continue
        node = level
        for key in candidate_path[:-1]:
            node = node[key]
        candidate_ref = node[candidate_path[-1]]
        if candidate_ref:
            ref_field_path = candidate_path
            ref = candidate_ref
            break
        if ref_field_path is None:
            ref_field_path = candidate_path

    if not ref_field_path:
        return physics_pack_path, None, None, False
    if not ref:
        return physics_pack_path, ref_field_path, None, False

    controllerset_pack_path = work_path_to_pack_path(ref)
    if controllerset_pack_path not in ctx.files:
        return physics_pack_path, ref_field_path, controllerset_pack_path, False
    owner = controllerset_pack_path.rsplit("/", 1)[1].split(".", 1)[0]
    return physics_pack_path, ref_field_path, controllerset_pack_path, (owner == ctx.actor_name)

def ensure_own_controller_set(ctx):
    physics_pack_path = ensure_own_actor_component(ctx, "GamePhysicsRef")
    if not physics_pack_path:
        return None

    physics_pack_path, ref_field_path, controllerset_pack_path, is_own = resolve_controller_set_ref(ctx)
    if controllerset_pack_path is None or controllerset_pack_path not in ctx.files:
        return None
    if is_own:
        return controllerset_pack_path

    return copy_and_rewire(ctx.files, ctx.actor_name, physics_pack_path, ref_field_path, controllerset_pack_path)


# Implementation details.

def resolve_hit_info_sensor_ref(ctx):
    _, hitinforef_pack_path, _ = resolve_actor_component(ctx, "HitInfoRef")
    if not hitinforef_pack_path or hitinforef_pack_path not in ctx.files:
        return None, None, None, False

    ref_obj = parse_byml(ctx.files[hitinforef_pack_path])
    chain = resolve_parent_chain(ctx.files, ref_obj)

    ref_field_path = None
    ref = None
    for level in chain:
        candidate_path = find_field_path(level, "HitInfoCommonParamFilePath")
        if candidate_path is None:
            continue
        node = level
        for key in candidate_path[:-1]:
            node = node[key]
        candidate_ref = node[candidate_path[-1]]
        if candidate_ref:
            ref_field_path = candidate_path
            ref = candidate_ref
            break
        if ref_field_path is None:
            ref_field_path = candidate_path

    if not ref_field_path:
        return hitinforef_pack_path, None, None, False
    if not ref:
        return hitinforef_pack_path, ref_field_path, None, False

    hitinfo_pack_path = work_path_to_pack_path(ref)
    if hitinfo_pack_path not in ctx.files:
        return hitinforef_pack_path, ref_field_path, hitinfo_pack_path, False
    owner = hitinfo_pack_path.rsplit("/", 1)[1].split(".", 1)[0]
    return hitinforef_pack_path, ref_field_path, hitinfo_pack_path, (owner == ctx.actor_name)

def ensure_own_hit_info_sensor_param(ctx):
    hitinforef_pack_path = ensure_own_actor_component(ctx, "HitInfoRef")
    if not hitinforef_pack_path:
        return None

    hitinforef_pack_path, ref_field_path, hitinfo_pack_path, is_own = resolve_hit_info_sensor_ref(ctx)
    if hitinfo_pack_path is None or hitinfo_pack_path not in ctx.files:
        return None
    if is_own:
        return hitinfo_pack_path

    return copy_and_rewire(ctx.files, ctx.actor_name, hitinforef_pack_path, ref_field_path, hitinfo_pack_path)

def get_current_stampable_area(ctx):
    """Liest den aktuell wirksamen (auch geerbten) StampableAreaFilePath-Wert,
    rein lesend - ohne dabei irgendetwas als 'eigen' anzulegen."""
    _, _, hitinfo_pack_path, _ = resolve_hit_info_sensor_ref(ctx)
    if not hitinfo_pack_path or hitinfo_pack_path not in ctx.files:
        return None
    hitinfo_obj = parse_byml(ctx.files[hitinfo_pack_path])
    chain = resolve_parent_chain(ctx.files, hitinfo_obj)
    for level in chain:
        if isinstance(level, dict) and level.get("StampableAreaFilePath"):
            return level["StampableAreaFilePath"]
    return None

def set_stampable_area(ctx, shape_pack_path):
    hitinfo_pack_path = ensure_own_hit_info_sensor_param(ctx)
    if not hitinfo_pack_path:
        return False, ctx.texts["actor_wizard_error_no_hitinfo"]
    hitinfo_obj = parse_byml(ctx.files[hitinfo_pack_path])
    hitinfo_obj["StampableAreaFilePath"] = pack_path_to_work_path(shape_pack_path)
    ctx.files[hitinfo_pack_path] = dump_byml(hitinfo_obj)
    ctx.mark_dirty()
    return True, ctx.texts["actor_wizard_log_stampable_area_set"].format(hitinfo_pack_path, shape_pack_path)

def _ensure_own_controllerset_arrays(files, cs_obj, chain):
    for key in CONTROLLERSET_ARRAY_KEYS:
        if isinstance(cs_obj.get(key), list):
            continue
        inherited = []
        for level in chain[1:]:
            if isinstance(level, dict) and isinstance(level.get(key), list):
                inherited = level[key]
                break
        cs_obj[key] = byml_copy(inherited)

def wire_new_hitbox(ctx, template_shape_path, new_shape_path):
    files = ctx.files

    old_dir, old_filename = template_shape_path.rsplit("/", 1)
    old_base = old_filename[:-len(SHAPE_SUFFIX)]
    new_dir, new_filename = new_shape_path.rsplit("/", 1)
    new_base = new_filename[:-len(SHAPE_SUFFIX)]

    old_rbsensor_path = f"{RBSENSOR_DIR}/{old_base}{RBSENSOR_SUFFIX}"
    old_rbctrl_path = f"{RBCTRLSENSOR_DIR}/{old_base}{RBCTRLSENSOR_SUFFIX}"
    new_rbsensor_path = f"{RBSENSOR_DIR}/{new_base}{RBSENSOR_SUFFIX}"
    new_rbctrl_path = f"{RBCTRLSENSOR_DIR}/{new_base}{RBCTRLSENSOR_SUFFIX}"

    if new_rbsensor_path in files or new_rbctrl_path in files:
        return False, [], ctx.texts["actor_wizard_error_sensor_exists"], None

    log_lines = []

    old_logical = hitbox_logical_name(ctx.actor_name, old_base)
    new_logical = hitbox_logical_name(ctx.actor_name, new_base)
    if new_logical == new_base:
        new_logical = strip_category_prefix(new_base)
        log_lines.append(
            ctx.texts["actor_wizard_hint_logical_name"].format(
                new_base, ctx.actor_name, new_logical
            )
        )

    controllerset_pack_path = ensure_own_controller_set(ctx)
    if controllerset_pack_path is None:
        return False, [], ctx.texts["actor_wizard_error_no_controllerset"], None

    cs_obj = parse_byml(files[controllerset_pack_path])
    chain = resolve_parent_chain(files, cs_obj)
    _ensure_own_controllerset_arrays(files, cs_obj, chain)

    old_work_shape = pack_path_to_work_path(template_shape_path)
    old_work_rbsensor = pack_path_to_work_path(old_rbsensor_path)
    old_work_rbctrl = pack_path_to_work_path(old_rbctrl_path)

    rbsensor_entry = _find_array_entry(cs_obj["RigidBodySensorNamePathAry"], old_work_rbsensor)
    old_rbsensor_field_name = rbsensor_entry["Name"] if rbsensor_entry else old_logical

    new_shape_field_name = _unique_array_name(cs_obj["ShapeNamePathAry"], f"{new_logical}Sensor")
    new_rbsensor_field_name = _unique_array_name(cs_obj["RigidBodySensorNamePathAry"], new_logical)
    # Implementation details.
    new_ctrl_field_name = _unique_array_name(cs_obj["ControllerSensorNamePathAry"], new_rbsensor_field_name)

    # --- RigidBodySensorParam ---
    if old_rbsensor_path in files:
        rb_obj = byml_copy(parse_byml(files[old_rbsensor_path]))
        log_lines.append(ctx.texts["actor_wizard_log_rbsensor_copied"].format(new_rbsensor_path))
    else:
        rb_obj = _default_rigid_body_sensor(new_shape_field_name)
        log_lines.append(ctx.texts["actor_wizard_log_rbsensor_default"].format(new_rbsensor_path))
    rb_obj["ShapeName"] = new_shape_field_name
    files[new_rbsensor_path] = dump_byml(rb_obj)

    # Implementation details.
    if old_rbctrl_path in files:
        rc_obj = byml_copy(parse_byml(files[old_rbctrl_path]))
        units = rc_obj.get("RigidBodyControllerUnitAry")
        matching_unit = None
        if isinstance(units, list):
            for u in units:
                if isinstance(u, dict) and u.get("Name") == old_rbsensor_field_name:
                    matching_unit = u
                    break
            if matching_unit is None and units and isinstance(units[0], dict):
                matching_unit = units[0]
        if matching_unit is not None:
            matching_unit["Name"] = new_rbsensor_field_name
            rc_obj["RigidBodyControllerUnitAry"] = [matching_unit]
        else:
            rc_obj["RigidBodyControllerUnitAry"] = [_default_controller_unit(new_rbsensor_field_name)]
        log_lines.append(ctx.texts["actor_wizard_log_rbctrl_copied"].format(new_rbctrl_path))
    else:
        rc_obj = {"RigidBodyControllerUnitAry": [_default_controller_unit(new_rbsensor_field_name)]}
        log_lines.append(ctx.texts["actor_wizard_log_rbctrl_default"].format(new_rbctrl_path))
    files[new_rbctrl_path] = dump_byml(rc_obj)

    # --- In ControllerSetParam einhängen ---
    _append_unique_entry(cs_obj["ShapeNamePathAry"], {
        "FilePath": pack_path_to_work_path(new_shape_path), "Name": new_shape_field_name,
    })
    _append_unique_entry(cs_obj["RigidBodySensorNamePathAry"], {
        "FilePath": pack_path_to_work_path(new_rbsensor_path), "Name": new_rbsensor_field_name,
    })
    _append_unique_entry(cs_obj["ControllerSensorNamePathAry"], {
        "FilePath": pack_path_to_work_path(new_rbctrl_path), "Name": new_ctrl_field_name,
    })
    files[controllerset_pack_path] = dump_byml(cs_obj)

    log_lines.append(
        ctx.texts["actor_wizard_log_controllerset_wired"].format(
            controllerset_pack_path, new_shape_field_name, new_rbsensor_field_name, new_ctrl_field_name
        )
    )

    return True, log_lines, None, new_rbsensor_field_name


def ensure_attack_tag_sensor(ctx, sensor_name):
    physics_pack_path = ensure_own_actor_component(ctx, "GamePhysicsRef")
    if not physics_pack_path:
        return None, None

    physics_obj = parse_byml(ctx.files[physics_pack_path])
    tags = physics_obj.get("AdditionalUserTagSensor")
    if not isinstance(tags, list):
        tags = []
        physics_obj["AdditionalUserTagSensor"] = tags

    tag_name = f"{sensor_name}Tag"
    for entry in tags:
        if isinstance(entry, dict) and entry.get("Name") == tag_name:
            names = entry.get("RigidBodyNames")
            if not isinstance(names, list):
                names = []
                entry["RigidBodyNames"] = names
            if sensor_name not in names:
                names.append(sensor_name)
            break
    else:
        tags.append({"Name": tag_name, "RigidBodyNames": [sensor_name]})

    ctx.files[physics_pack_path] = dump_byml(physics_obj)
    return tag_name, physics_pack_path

def create_matching_attack_info_entry(ctx, entry_name, sensor_name):
    ref_pack_path = ensure_own_actor_component(ctx, "AttackerRef")
    if not ref_pack_path:
        return False, ctx.texts["actor_wizard_error_no_attackerref"]

    tag_name, physics_pack_path = ensure_attack_tag_sensor(ctx, sensor_name)
    if not tag_name:
        return False, ctx.texts["actor_wizard_error_no_gp_for_sensor"]

    ref_obj = parse_byml(ctx.files[ref_pack_path])
    if not isinstance(ref_obj.get("AttackInfo"), dict):
        ref_obj["AttackInfo"] = {}
    if entry_name in ref_obj["AttackInfo"]:
        return False, ctx.texts["actor_wizard_error_attackinfo_exists"].format(entry_name, tag_name)
    ref_obj["AttackInfo"][entry_name] = {"SensorTagName": [tag_name]}
    ctx.files[ref_pack_path] = dump_byml(ref_obj)
    return True, ctx.texts["actor_wizard_log_attackinfo_created"].format(
        entry_name, tag_name, physics_pack_path, sensor_name, ref_pack_path
    )

def list_registered_sensor_names(ctx):
    _, _, controllerset_pack_path, _ = resolve_controller_set_ref(ctx)
    if not controllerset_pack_path or controllerset_pack_path not in ctx.files:
        return []
    cs_obj = parse_byml(ctx.files[controllerset_pack_path])
    chain = resolve_parent_chain(ctx.files, cs_obj)
    names = set()
    for level in chain:
        if not isinstance(level, dict):
            continue
        for entry in level.get("RigidBodySensorNamePathAry", []) or []:
            if isinstance(entry, dict) and entry.get("Name"):
                names.add(entry["Name"])
    return sorted(names)


# Reihenfolge nach Häufigkeit/Spezifität - Capsule vor Box vor Sphere, falls
# (untypisch) mehrere Shape-Arrays gleichzeitig in einer Datei stehen sollten.
SHAPE_TYPE_KEYS = ("Capsule", "Box", "Sphere")

def _get_shape_array(obj):
    """Implementation details."""
    if not isinstance(obj, dict):
        return None, None
    for key in SHAPE_TYPE_KEYS:
        val = obj.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return key, val
    return None, None

def _get_num(value):
    return value if value is not None else 0.0

def _set_num(container, key, text):
    try:
        new_value = float(text)
    except (TypeError, ValueError):
        return
    existing = container.get(key)
    leaf_type = get_byml_leaf_type(existing)
    container[key] = leaf_type(new_value) if leaf_type is not None else new_value


class Vector3Edit(QWidget):
    """Kleines Verbund-Widget für einen X/Y/Z-Vektor in einer Formular-Zeile
    (z.B. Center, CenterA, CenterB, OffsetTranslation, OffsetRotation)."""
    valueChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.x_edit = QLineEdit("0")
        self.y_edit = QLineEdit("0")
        self.z_edit = QLineEdit("0")
        self._suspend = False
        for label, edit in (("X", self.x_edit), ("Y", self.y_edit), ("Z", self.z_edit)):
            layout.addWidget(QLabel(label))
            layout.addWidget(edit)
            edit.textChanged.connect(self._emit_changed)

    def _emit_changed(self):
        if not self._suspend:
            self.valueChanged.emit()

    def set_values(self, vec):
        vec = vec if isinstance(vec, dict) else {}
        self._suspend = True
        self.x_edit.setText(str(_get_num(vec.get("X"))))
        self.y_edit.setText(str(_get_num(vec.get("Y"))))
        self.z_edit.setText(str(_get_num(vec.get("Z"))))
        self._suspend = False

    def apply_to(self, container, key):
        vec = container.get(key)
        if not isinstance(vec, dict):
            vec = {}
            container[key] = vec
        _set_num(vec, "X", self.x_edit.text())
        _set_num(vec, "Y", self.y_edit.text())
        _set_num(vec, "Z", self.z_edit.text())


class HitboxShapeEditor(QWidget):
    """Implementation details."""
    valueChanged = pyqtSignal()

    def __init__(self, texts=None, parent=None):
        super().__init__(parent)
        self.texts = texts or {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        sub_row = QHBoxLayout()
        self.sub_shape_label = QLabel(self.texts.get("hitbox_editor_sub_shape", "Teilform:"))
        sub_row.addWidget(self.sub_shape_label)
        self.sub_shape_combo = QComboBox()
        self.sub_shape_combo.currentIndexChanged.connect(self._on_sub_shape_selected)
        sub_row.addWidget(self.sub_shape_combo, stretch=1)
        self.add_sub_shape_btn = QPushButton(self.texts.get("hitbox_editor_add_sub_shape", "Teilform hinzufügen"))
        self.add_sub_shape_btn.clicked.connect(self._add_sub_shape)
        sub_row.addWidget(self.add_sub_shape_btn)
        self.remove_sub_shape_btn = QPushButton(self.texts.get("hitbox_editor_remove_sub_shape", "Teilform entfernen"))
        self.remove_sub_shape_btn.clicked.connect(self._remove_sub_shape)
        sub_row.addWidget(self.remove_sub_shape_btn)
        outer.addLayout(sub_row)
        self._sub_row_widgets = [
            self.sub_shape_label, self.sub_shape_combo,
            self.add_sub_shape_btn, self.remove_sub_shape_btn,
        ]

        self.form_widget = QWidget()
        self.form = QFormLayout(self.form_widget)
        outer.addWidget(self.form_widget)

        self.unsupported_label = QLabel(self.texts.get("hitbox_editor_unsupported_type", ""))
        self.unsupported_label.setWordWrap(True)
        outer.addWidget(self.unsupported_label)
        self.unsupported_label.hide()

        self.fallback_editor = GenericFieldEditor(texts=self.texts)
        self.fallback_editor.valueChanged.connect(self.valueChanged.emit)
        outer.addWidget(self.fallback_editor, stretch=1)

        outer.addStretch(0)

        self._mode = None          # "fallback" oder Shape-Typ in lowercase
        self._shape_type = None    # "Sphere" | "Box" | "Capsule"
        self._array = None         # die Liste, z.B. obj["Capsule"]
        self._index = 0
        self._entry = None         # self._array[self._index]
        self._suspend_signals = False

        self._radius_edit = None
        self._edge_edit = None
        self._height_edit = None
        self._center = None
        self._center_a = None
        self._center_b = None
        self._offset_translation = None
        self._offset_rotation = None
        self._name_edit = None
        self._material_edit = None

    def _reset_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)

    def _on_edit_changed(self):
        if not self._suspend_signals:
            self.valueChanged.emit()

    def _set_sub_row_visible(self, visible):
        for w in self._sub_row_widgets:
            w.setVisible(visible)

    def load_object(self, obj, exclude_top_level=None):
        self._reset_form()
        self._mode = None
        self._entry = None
        self.unsupported_label.hide()
        obj = obj if isinstance(obj, dict) else {}

        shape_type, array = _get_shape_array(obj)
        if array is None:
            self._array = None
            self._shape_type = None
            self._set_sub_row_visible(False)
            self._mode = "fallback"
            self.form_widget.hide()
            self.fallback_editor.show()
            self.fallback_editor.load_object(obj, exclude_top_level=exclude_top_level)
            return

        self._shape_type = shape_type
        self._array = array
        self._set_sub_row_visible(True)
        self._populate_sub_shape_combo()
        self._suspend_signals = True
        self.sub_shape_combo.setCurrentIndex(0)
        self._suspend_signals = False
        self._load_entry(0)

    def _populate_sub_shape_combo(self):
        self._suspend_signals = True
        self.sub_shape_combo.clear()
        for i, entry in enumerate(self._array):
            label = entry.get("Name") if isinstance(entry, dict) and entry.get("Name") else f"#{i + 1}"
            self.sub_shape_combo.addItem(label)
        self._suspend_signals = False

    def _on_sub_shape_selected(self, idx):
        if self._suspend_signals or idx < 0 or self._array is None:
            return
        if self._entry is not None:
            self._apply_fields_to(self._entry)
        self._load_entry(idx)
        self.valueChanged.emit()

    def _load_entry(self, index):
        self._index = index
        self._entry = self._array[index]
        self._suspend_signals = True
        self._build_fields_for(self._shape_type, self._entry)
        self._suspend_signals = False
        self.form_widget.show()
        self.fallback_editor.hide()
        self._mode = self._shape_type.lower()

    def _build_fields_for(self, shape_type, entry):
        self._reset_form()
        entry = entry if isinstance(entry, dict) else {}

        if shape_type == "Sphere":
            self._radius_edit = QLineEdit(str(_get_num(entry.get("Radius"))))
            self._radius_edit.textChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_radius", "Radius:")), self._radius_edit)

            self._center = Vector3Edit()
            self._center.set_values(entry.get("Center"))
            self._center.valueChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_position_x", "Position:")), self._center)

        elif shape_type == "Box":
            half_extents = entry.get("HalfExtents") if isinstance(entry.get("HalfExtents"), dict) else {}
            self._edge_edit = QLineEdit(str(_get_num(half_extents.get("X"))))
            self._height_edit = QLineEdit(str(_get_num(half_extents.get("Y"))))
            self._edge_edit.textChanged.connect(self._on_edit_changed)
            self._height_edit.textChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_edge_length", "Kantenlänge (halb, X/Z):")), self._edge_edit)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_height", "Höhe (halb, Y):")), self._height_edit)

            self._center = Vector3Edit()
            self._center.set_values(entry.get("Center"))
            self._center.valueChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_position_x", "Position:")), self._center)

        elif shape_type == "Capsule":
            self._radius_edit = QLineEdit(str(_get_num(entry.get("Radius"))))
            self._radius_edit.textChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_radius", "Radius:")), self._radius_edit)

            self._center_a = Vector3Edit()
            self._center_a.set_values(entry.get("CenterA"))
            self._center_a.valueChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_center_a", "Mittelpunkt A:")), self._center_a)

            self._center_b = Vector3Edit()
            self._center_b.set_values(entry.get("CenterB"))
            self._center_b.valueChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_center_b", "Mittelpunkt B:")), self._center_b)

            self._offset_translation = Vector3Edit()
            self._offset_translation.set_values(entry.get("OffsetTranslation"))
            self._offset_translation.valueChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_offset_translation", "Offset-Verschiebung:")), self._offset_translation)

            self._offset_rotation = Vector3Edit()
            self._offset_rotation.set_values(entry.get("OffsetRotation"))
            self._offset_rotation.valueChanged.connect(self._on_edit_changed)
            self.form.addRow(QLabel(self.texts.get("hitbox_editor_offset_rotation", "Offset-Rotation:")), self._offset_rotation)

        # -- gemeinsam für alle Shape-Typen --
        name_val = entry.get("Name")
        self._name_edit = QLineEdit(name_val if isinstance(name_val, str) else "")
        self._name_edit.textChanged.connect(self._on_edit_changed)
        self.form.addRow(QLabel(self.texts.get("hitbox_editor_name", "Name (optional):")), self._name_edit)

        presets = entry.get("MaterialPresets")
        presets_text = ", ".join(presets) if isinstance(presets, list) else ""
        self._material_edit = QLineEdit(presets_text)
        self._material_edit.textChanged.connect(self._on_edit_changed)
        self.form.addRow(QLabel(self.texts.get("hitbox_editor_material_presets", "MaterialPresets:")), self._material_edit)

    def _apply_fields_to(self, entry):
        if self._shape_type == "Sphere":
            _set_num(entry, "Radius", self._radius_edit.text())
            self._center.apply_to(entry, "Center")
        elif self._shape_type == "Box":
            half_extents = entry.get("HalfExtents")
            if not isinstance(half_extents, dict):
                half_extents = {}
                entry["HalfExtents"] = half_extents
            _set_num(half_extents, "X", self._edge_edit.text())
            _set_num(half_extents, "Z", self._edge_edit.text())
            _set_num(half_extents, "Y", self._height_edit.text())
            self._center.apply_to(entry, "Center")
        elif self._shape_type == "Capsule":
            _set_num(entry, "Radius", self._radius_edit.text())
            self._center_a.apply_to(entry, "CenterA")
            self._center_b.apply_to(entry, "CenterB")
            self._offset_translation.apply_to(entry, "OffsetTranslation")
            self._offset_rotation.apply_to(entry, "OffsetRotation")

        # Implementation details.
        name_text = self._name_edit.text().strip()
        if name_text:
            entry["Name"] = name_text
        elif "Name" in entry:
            del entry["Name"]

        presets_text = self._material_edit.text().strip()
        if presets_text:
            entry["MaterialPresets"] = [p.strip() for p in presets_text.split(",") if p.strip()]
        elif "MaterialPresets" in entry:
            del entry["MaterialPresets"]

    def apply_to_object(self):
        if self._mode == "fallback":
            self.fallback_editor.apply_to_object()
            return
        if self._entry is not None:
            self._apply_fields_to(self._entry)
            # Combo-Beschriftung nachziehen, falls sich der Name geändert hat
            self._populate_sub_shape_combo()
            self._suspend_signals = True
            self.sub_shape_combo.setCurrentIndex(self._index)
            self._suspend_signals = False

    def _add_sub_shape(self):
        if self._array is None:
            return
        if self._entry is not None:
            self._apply_fields_to(self._entry)
        new_entry = byml_copy(self._entry) if self._entry is not None else {}
        base_name = new_entry.get("Name") if isinstance(new_entry.get("Name"), str) and new_entry.get("Name") else None
        if base_name:
            existing = {e.get("Name") for e in self._array if isinstance(e, dict)}
            i = 2
            candidate = f"{base_name}{i}"
            while candidate in existing:
                i += 1
                candidate = f"{base_name}{i}"
            new_entry["Name"] = candidate
        self._array.append(new_entry)
        self._populate_sub_shape_combo()
        new_index = len(self._array) - 1
        self._suspend_signals = True
        self.sub_shape_combo.setCurrentIndex(new_index)
        self._suspend_signals = False
        self._load_entry(new_index)
        self.valueChanged.emit()

    def _remove_sub_shape(self):
        if self._array is None or len(self._array) <= 1:
            QMessageBox.information(
                self, self.texts.get("actor_wizard_info_title", "Info"),
                self.texts.get("hitbox_editor_cannot_remove_last",
                                "Die letzte verbleibende Teilform kann nicht entfernt werden.")
            )
            return
        del self._array[self._index]
        new_index = min(self._index, len(self._array) - 1)
        self._populate_sub_shape_combo()
        self._suspend_signals = True
        self.sub_shape_combo.setCurrentIndex(new_index)
        self._suspend_signals = False
        self._load_entry(new_index)
        self.valueChanged.emit()


# Implementation details.

def remove_hitbox_and_wiring(ctx, shape_path):
    """Implementation details."""
    texts = getattr(ctx, "texts", {}) or {}
    files = ctx.files
    if shape_path not in files:
        return False, texts.get("actor_wizard_err_shapeparam_not_found", "ShapeParam not found: {0}").format(shape_path)

    shape_name = shape_path.rsplit("/", 1)[1][:-len(SHAPE_SUFFIX)]

    # Implementation details.
    rbsensor_path = f"{RBSENSOR_DIR}/{shape_name}{RBSENSOR_SUFFIX}"
    guessed_rbctrl_path = f"{RBCTRLSENSOR_DIR}/{shape_name}{RBCTRLSENSOR_SUFFIX}"

    # 2. ControllerSetParam finden
    _, _, controllerset_path, _ = resolve_controller_set_ref(ctx)
    if not controllerset_path or controllerset_path not in files:
        return False, texts.get("actor_wizard_err_controllerset_not_found",
                                 "ControllerSetParam not found – wiring cannot be cleaned up.")

    cs_obj = parse_byml(files[controllerset_path])
    chain = resolve_parent_chain(files, cs_obj)
    # Auf eigene Arrays stellen (falls noch nicht geschehen)
    _ensure_own_controllerset_arrays(files, cs_obj, chain)

    work_shape = pack_path_to_work_path(shape_path)
    work_sensor = pack_path_to_work_path(rbsensor_path)
    guessed_work_ctrl = pack_path_to_work_path(guessed_rbctrl_path)

    # Implementation details.
    sensor_logical_name = shape_name
    rbsensor_array = cs_obj.get("RigidBodySensorNamePathAry")
    if isinstance(rbsensor_array, list):
        for entry in rbsensor_array:
            if isinstance(entry, dict) and entry.get("FilePath") == work_sensor and entry.get("Name"):
                sensor_logical_name = entry["Name"]
                break

    # Implementation details.
    removed_ctrl_fps = set()
    ctrl_array = cs_obj.get("ControllerSensorNamePathAry")
    if isinstance(ctrl_array, list):
        for entry in ctrl_array:
            if not isinstance(entry, dict):
                continue
            fp = entry.get("FilePath")
            if not fp:
                continue
            ctrl_pack_path = work_path_to_pack_path(fp)
            if ctrl_pack_path not in files:
                continue
            ctrl_obj = parse_byml(files[ctrl_pack_path])
            units = ctrl_obj.get("RigidBodyControllerUnitAry")
            if not isinstance(units, list):
                continue
            new_units = [u for u in units if not (isinstance(u, dict) and u.get("Name") == sensor_logical_name)]
            if len(new_units) == len(units):
                continue
            if new_units:
                ctrl_obj["RigidBodyControllerUnitAry"] = new_units
                files[ctrl_pack_path] = dump_byml(ctrl_obj)
            else:
                del files[ctrl_pack_path]
                removed_ctrl_fps.add(fp)

    # 4. Einträge aus den ControllerSet-Arrays entfernen
    removed_any = False
    for key in CONTROLLERSET_ARRAY_KEYS:
        arr = cs_obj.get(key)
        if isinstance(arr, list):
            new_arr = []
            for entry in arr:
                if isinstance(entry, dict):
                    fp = entry.get("FilePath")
                    if fp and (fp in (work_shape, work_sensor, guessed_work_ctrl) or fp in removed_ctrl_fps):
                        removed_any = True
                        continue
                new_arr.append(entry)
            cs_obj[key] = new_arr

    if removed_any:
        files[controllerset_path] = dump_byml(cs_obj)

    # Implementation details.
    ref_pack_path, is_own = resolve_actor_component(ctx, "AttackerRef")[1:]
    if ref_pack_path and ref_pack_path in files:
        ref_obj = parse_byml(files[ref_pack_path])
        attack_info = ref_obj.get("AttackInfo")
        if isinstance(attack_info, dict):
            candidates = {shape_name, sensor_logical_name}
            tag_candidates = candidates | {f"{c}Tag" for c in candidates}
            to_delete = []
            for key, entry in attack_info.items():
                if isinstance(entry, dict):
                    tags = entry.get("SensorTagName")
                    if isinstance(tags, list) and any(t in tag_candidates for t in tags):
                        to_delete.append(key)
            for key in to_delete:
                del attack_info[key]
            if to_delete:
                files[ref_pack_path] = dump_byml(ref_obj)

    # Implementation details.
    for path in (shape_path, rbsensor_path, guessed_rbctrl_path):
        if path in files:
            del files[path]

    return True, texts.get("actor_wizard_msg_hitbox_removed", "Hitbox and all wiring removed.")


# Implementation details.

class HitboxWizardTab(QWidget):
    def __init__(self, ctx: WizardContext, texts=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.texts = texts or {}
        self.current_path = None
        self.current_obj = None
        self.dirty = False

        layout = QVBoxLayout(self)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(self.texts["actor_wizard_existing_hitboxes"]))
        self.shape_list = QListWidget()
        self.shape_list.currentItemChanged.connect(self.on_shape_selected)
        left_layout.addWidget(self.shape_list)

        btn_row = QHBoxLayout()
        delete_btn = QPushButton(self.texts["actor_wizard_remove_row"])
        delete_btn.clicked.connect(self.delete_selected_hitbox)
        btn_row.addWidget(delete_btn)
        left_layout.addLayout(btn_row)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.editor = HitboxShapeEditor(texts=self.texts)
        self.editor.valueChanged.connect(self._on_edit_changed)
        right_layout.addWidget(self.editor, stretch=1)

        apply_btn = QPushButton(self.texts["actor_wizard_apply_hitbox"])
        apply_btn.clicked.connect(self.apply_changes)
        right_layout.addWidget(apply_btn)
        self.apply_btn = apply_btn

        split.addWidget(right)
        split.setSizes([250, 500])
        layout.addWidget(split, stretch=1)

        new_row = QHBoxLayout()
        new_row.addWidget(QLabel(self.texts["actor_wizard_new_hitbox_name"]))
        self.new_name_edit = QLineEdit()
        self.new_name_edit.setPlaceholderText(self.texts["actor_wizard_new_hitbox_placeholder"])
        new_row.addWidget(self.new_name_edit, stretch=1)
        new_row.addWidget(QLabel(SHAPE_SUFFIX))
        create_btn = QPushButton(self.texts["actor_wizard_create_from_template"])
        create_btn.clicked.connect(self.create_from_template)
        new_row.addWidget(create_btn)
        layout.addLayout(new_row)

        self.auto_attack_info_check = QCheckBox(self.texts["actor_wizard_auto_attack_info"])
        self.auto_attack_info_check.setChecked(True)
        layout.addWidget(self.auto_attack_info_check)

        self.setEnabled(False)

    def on_pack_loaded(self):
        self.shape_list.clear()
        self.current_path = None
        self.current_obj = None
        self.editor.load_object({})
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self.setEnabled(False)
        if not self.ctx.loaded:
            return
        self.setEnabled(True)
        for path in find_shape_param_files(self.ctx.files):
            name = path.rsplit("/", 1)[1][: -len(SHAPE_SUFFIX)]
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            self.shape_list.addItem(item)

    def on_shape_selected(self, curr, prev):
        if not curr:
            return
        path = curr.data(Qt.UserRole)
        self.current_path = path
        self.current_obj = parse_byml(self.ctx.files[path])
        self.editor.load_object(self.current_obj)
        self.dirty = False
        set_button_dirty(self.apply_btn, False)

    def _on_edit_changed(self):
        if not self.dirty:
            self.dirty = True
            set_button_dirty(self.apply_btn, True)

    def apply_changes(self):
        if not self.current_path or self.current_obj is None:
            return
        self.editor.apply_to_object()
        self.ctx.files[self.current_path] = dump_byml(self.current_obj)
        self.ctx.mark_dirty()
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self.ctx.log(self.texts["actor_wizard_log_hitbox_updated"].format(self.current_path))

    def delete_selected_hitbox(self):
        item = self.shape_list.currentItem()
        if not item:
            QMessageBox.information(self, self.texts["actor_wizard_info_title"],
                                     self.texts["actor_wizard_info_select_template_first"])
            return
        path = item.data(Qt.UserRole)
        name = item.text()
        reply = QMessageBox.question(self, self.texts["actor_wizard_question_title"],
                                     self.texts.get("actor_wizard_confirm_delete_hitbox",
                                                     "Delete hitbox '{0}' and all wiring?").format(name),
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        success, msg = remove_hitbox_and_wiring(self.ctx, path)
        if success:
            self.ctx.mark_dirty()
            self.on_pack_loaded()
            self.ctx.log(msg)
        else:
            QMessageBox.warning(self, self.texts["actor_wizard_error_title"], msg)

    def create_from_template(self):
        if not self.current_path:
            QMessageBox.information(
                self,
                self.texts["actor_wizard_info_title"],
                self.texts["actor_wizard_info_select_template_first"]
            )
            return
        new_name = self.new_name_edit.text().strip()
        if not new_name:
            QMessageBox.information(
                self,
                self.texts["actor_wizard_info_title"],
                self.texts["actor_wizard_info_enter_name"]
            )
            return
        directory = self.current_path.rsplit("/", 1)[0]
        new_path = f"{directory}/{new_name}{SHAPE_SUFFIX}"
        if new_path in self.ctx.files:
            QMessageBox.warning(
                self,
                self.texts["actor_wizard_error_title"],
                self.texts["actor_wizard_error_hitbox_exists"]
            )
            return
        self.ctx.files[new_path] = self.ctx.files[self.current_path]
        self.ctx.mark_dirty()
        self.ctx.log(self.texts["actor_wizard_log_new_hitbox_created"].format(self.current_path, new_path))

        ok, log_lines, error, sensor_name = wire_new_hitbox(self.ctx, self.current_path, new_path)
        for line in log_lines:
            self.ctx.log(line)
        if not ok:
            self.ctx.log(self.texts.get("actor_wizard_log_auto_wire_failed", "Automatic wiring failed: {0}").format(error))
            QMessageBox.warning(
                self,
                self.texts["actor_wizard_warning_title"],
                self.texts["actor_wizard_info_hitbox_created_but_wiring_failed"].format(error)
            )
        elif self.auto_attack_info_check.isChecked() and sensor_name:
            attack_ok, attack_msg = create_matching_attack_info_entry(self.ctx, new_name, sensor_name)
            self.ctx.log(attack_msg)
            if not attack_ok:
                QMessageBox.information(
                    self,
                    self.texts["actor_wizard_info_title"],
                    attack_msg
                )
        self.ctx.mark_dirty()

        self.on_pack_loaded()
        for i in range(self.shape_list.count()):
            if self.shape_list.item(i).data(Qt.UserRole) == new_path:
                self.shape_list.setCurrentRow(i)
                break


# Implementation details.

class HitboxBehaviorWizardTab(QWidget):
    def __init__(self, ctx: WizardContext, texts=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.texts = texts or {}
        self.current_path = None
        self.current_obj = None
        self.known_values = load_known_values()
        self.field_combos = {}
        self.dirty = False

        layout = QVBoxLayout(self)

        split = QSplitter(Qt.Horizontal)

        left = QVBoxLayout()
        left.addWidget(QLabel(self.texts["actor_wizard_existing_sensors"]))
        self.sensor_list = QListWidget()
        self.sensor_list.currentItemChanged.connect(self.on_sensor_selected)
        left.addWidget(self.sensor_list, stretch=1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        split.addWidget(left_widget)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)

        field_labels = {
            "LayerSensor": self.texts["actor_wizard_field_layersensor"],
            "SubLayerSensor": self.texts["actor_wizard_field_sublayersensor"],
            "EnableLayerHitMask": self.texts["actor_wizard_field_enablelayerhitmask"],
            "EnableSubLayerHitMask": self.texts["actor_wizard_field_enablesublayerhitmask"],
        }
        field_tooltip_keys = {
            "LayerSensor": "actor_wizard_layersensor_tooltip",
            "SubLayerSensor": "actor_wizard_sublayersensor_tooltip",
            "EnableLayerHitMask": "actor_wizard_enablelayerhitmask_tooltip",
            "EnableSubLayerHitMask": "actor_wizard_enablesublayerhitmask_tooltip",
        }
        for field in HITBOX_SENSOR_FIELDS:
            row = QHBoxLayout()
            label = QLabel(field_labels[field])
            tooltip = self.texts[field_tooltip_keys[field]]
            if tooltip:
                label.setToolTip(tooltip)
            label.setMinimumWidth(170)
            row.addWidget(label)
            combo = self._make_multi_combo(field)
            if tooltip:
                combo.setToolTip(tooltip)
            combo.currentTextChanged.connect(self._on_combo_changed)
            row.addWidget(combo, stretch=1)
            self.field_combos[field] = combo
            right_layout.addLayout(row)

        self.apply_btn = QPushButton(self.texts["actor_wizard_apply_hitbox_behavior"])
        self.apply_btn.clicked.connect(self.apply_changes)
        right_layout.addWidget(self.apply_btn)

        right_layout.addStretch(1)

        split.addWidget(right)
        split.setSizes([250, 500])
        layout.addWidget(split, stretch=1)

        self.setEnabled(False)

    def _make_multi_combo(self, category):
        combo = TooltipComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(140)
        combo.set_tooltip_data(category, self.known_values)
        for value in sorted(self.known_values.get(category, {}).keys()):
            combo.addItem(value)
        combo.setCurrentIndex(-1)
        combo.setEditText("")
        return combo

    def _refresh_multi_combo(self, combo, category):
        current_text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.set_tooltip_data(category, self.known_values)
        for value in sorted(self.known_values.get(category, {}).keys()):
            combo.addItem(value)
        combo.blockSignals(False)
        combo.setEditText(current_text)

    def _refresh_all_field_combos(self):
        self.known_values = load_known_values()
        for field, combo in self.field_combos.items():
            self._refresh_multi_combo(combo, field)

    def _on_combo_changed(self):
        if not self.dirty:
            self.dirty = True
            set_button_dirty(self.apply_btn, True)

    def on_pack_loaded(self):
        self.sensor_list.clear()
        self.current_path = None
        self.current_obj = None
        self._refresh_all_field_combos()
        for combo in self.field_combos.values():
            combo.setCurrentIndex(-1)
            combo.setEditText("")
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self.setEnabled(False)
        if not self.ctx.loaded:
            return
        for path in find_rigid_body_sensor_files(self.ctx.files):
            name = path.rsplit("/", 1)[1][: -len(RBSENSOR_SUFFIX)]
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            self.sensor_list.addItem(item)
        self.setEnabled(True)

    def on_sensor_selected(self, curr, prev):
        if not curr:
            return
        path = curr.data(Qt.UserRole)
        self.current_path = path
        self.current_obj = parse_byml(self.ctx.files[path])
        self.status_label.setText(path)
        for field, combo in self.field_combos.items():
            value = self.current_obj.get(field) if isinstance(self.current_obj, dict) else None
            combo.blockSignals(True)
            combo.setEditText(str(value) if value not in (None, "") else "")
            combo.blockSignals(False)
        self.dirty = False
        set_button_dirty(self.apply_btn, False)

    def apply_changes(self):
        if not self.current_path or self.current_obj is None:
            return
        for field, combo in self.field_combos.items():
            value = combo.currentText().strip()
            if value:
                self.current_obj[field] = value
                add_known_value(field, value)
            elif field in self.current_obj:
                del self.current_obj[field]
        self.ctx.files[self.current_path] = dump_byml(self.current_obj)
        self.ctx.mark_dirty()
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self._refresh_all_field_combos()
        for field, combo in self.field_combos.items():
            value = self.current_obj.get(field)
            combo.blockSignals(True)
            combo.setEditText(str(value) if value not in (None, "") else "")
            combo.blockSignals(False)
        self.ctx.log(self.texts["actor_wizard_log_hitbox_updated"].format(self.current_path))


# Attack-info tab.

ATTACK_PARAM_DIR = "Gyml/Actor/AttackParam"
ATTACK_PARAM_SUFFIX = ".game__actor__AttackParam.bgyml"

KNOWN_ATTACK_PARAM_TEMPLATES = [
    "NormalEnemyBodyAttack",
    "ShellAttack",
]

def find_attack_param_files(files):
    return sorted(n for n in files if n.endswith(ATTACK_PARAM_SUFFIX))

def list_attack_param_templates(files):
    found = find_attack_param_files(files)
    result = []
    seen_names = set()
    for path in found:
        name = path.rsplit("/", 1)[1][: -len(ATTACK_PARAM_SUFFIX)]
        result.append((name, path))
        seen_names.add(name)
    for name in KNOWN_ATTACK_PARAM_TEMPLATES:
        if name not in seen_names:
            path = f"{ATTACK_PARAM_DIR}/{name}{ATTACK_PARAM_SUFFIX}"
            result.append((name, path if path in files else None))
    return result

def list_attack_info_entries(ctx):
    ref_pack_path, is_own = resolve_actor_component(ctx, "AttackerRef")[1:]
    if not ref_pack_path or ref_pack_path not in ctx.files:
        return None, False, None, {}
    ref_obj = parse_byml(ctx.files[ref_pack_path])
    effective = get_effective_dict(ctx.files, ref_obj, dict_key="AttackInfo")
    return ref_pack_path, is_own, ref_obj, effective

def _as_str_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


class NewAttackInfoEntryDialog(QDialog):
    def __init__(self, files, texts=None, parent=None):
        super().__init__(parent)
        self.texts = texts or {}
        self.setWindowTitle(self.texts["actor_wizard_new_attack_entry_title"])

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(self.texts["actor_wizard_new_attack_entry_name"]))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.texts["actor_wizard_new_attack_entry_placeholder"])
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel(self.texts["actor_wizard_new_attack_entry_template"]))
        self.template_combo = QComboBox()
        self.template_combo.addItem(self.texts["actor_wizard_new_attack_entry_empty"], None)
        for name, path in list_attack_param_templates(files):
            label = name if path else f"{name} ({self.texts['actor_wizard_template_missing']})"
            self.template_combo.addItem(label, path)
        layout.addWidget(self.template_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_values(self):
        name = self.name_edit.text().strip()
        template_path = self.template_combo.currentData()
        return name, template_path


ATTACK_ENTRY_DEDICATED_FIELDS = ["AtType", "AtAttribute", "SensorTagName"]

REFLECT_DEFAULT_FIELDS = {
    "EnableEntitySubLayerHitMask": "Default",
    "IsDefaultEntity": True,
    "IsDefaultSensor": False,
    "IsSensorLayer": True,
    "LayerEntity": "GroundObject",
    "LayerSensor": "AllAttack",
    "OnResetActive": True,
    "SubLayerEntity": "Unspecified",
}


class AttackInfoWizardTab(QWidget):
    def __init__(self, ctx: WizardContext, texts=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.texts = texts or {}
        self.known_values = load_known_values()

        self.ref_pack_path = None
        self.ref_is_own = False
        self.ref_obj = None
        self.entries = {}
        self.current_key = None
        self.current_entry_obj = None
        self.dirty = False

        layout = QVBoxLayout(self)

        self.status_label = QLabel(self.texts["actor_wizard_no_pack_loaded"])
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.own_file_btn = QPushButton(self.texts["actor_wizard_make_own_file"])
        self.own_file_btn.setEnabled(False)
        self.own_file_btn.clicked.connect(self.make_own_file)
        layout.addWidget(self.own_file_btn)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(self.texts["actor_wizard_attack_entries_label"]))
        self.entry_list = QListWidget()
        self.entry_list.currentItemChanged.connect(self.on_entry_selected)
        left_layout.addWidget(self.entry_list, stretch=1)

        new_entry_btn = QPushButton(self.texts["actor_wizard_new_attack_entry"])
        new_entry_btn.clicked.connect(self.new_entry_dialog)
        left_layout.addWidget(new_entry_btn)

        remove_entry_btn = QPushButton(self.texts["actor_wizard_remove_attack_entry"])
        remove_entry_btn.clicked.connect(self.remove_selected_entry)
        left_layout.addWidget(remove_entry_btn)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel(self.texts.get("attack_wizard_attype_label", "AtType:")))
        self.attype_combo = self._make_multi_combo("AtType")
        self.attype_combo.currentTextChanged.connect(self._on_combo_changed)
        type_row.addWidget(self.attype_combo, stretch=1)
        right_layout.addLayout(type_row)

        attr_row = QHBoxLayout()
        attr_row.addWidget(QLabel(self.texts.get("attack_wizard_atattribute_label", "AtAttribute:")))
        self.atattribute_combo = self._make_multi_combo("AtAttribute")
        self.atattribute_combo.currentTextChanged.connect(self._on_combo_changed)
        attr_row.addWidget(self.atattribute_combo, stretch=1)
        right_layout.addLayout(attr_row)

        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel(self.texts.get("attack_wizard_sensortagname_label", "SensorTagName:")))
        self.sensor_tag_edit = QLineEdit()
        self.sensor_tag_edit.textChanged.connect(self._on_combo_changed)
        tag_row.addWidget(self.sensor_tag_edit, stretch=1)
        right_layout.addLayout(tag_row)

        self.editor = GenericFieldEditor(texts=self.texts)
        self.editor.valueChanged.connect(self._on_combo_changed)
        right_layout.addWidget(self.editor, stretch=1)

        self.apply_btn = QPushButton(self.texts["actor_wizard_apply_changes"])
        self.apply_btn.clicked.connect(self.apply_entry)
        right_layout.addWidget(self.apply_btn)

        split.addWidget(right)
        split.setSizes([250, 500])
        layout.addWidget(split, stretch=1)

        self.setEnabled(False)

    def _make_multi_combo(self, category):
        combo = TooltipComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(140)
        combo.set_tooltip_data(category, self.known_values)
        for value in sorted(self.known_values.get(category, {}).keys()):
            combo.addItem(value)
        combo.setCurrentIndex(-1)
        combo.setEditText("")
        return combo

    def _refresh_multi_combo(self, combo, category):
        current_text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.set_tooltip_data(category, self.known_values)
        for value in sorted(self.known_values.get(category, {}).keys()):
            combo.addItem(value)
        combo.blockSignals(False)
        combo.setEditText(current_text)

    def _on_combo_changed(self):
        if not self.dirty:
            self.dirty = True
            set_button_dirty(self.apply_btn, True)

    def on_pack_loaded(self):
        self.ref_pack_path = None
        self.ref_is_own = False
        self.ref_obj = None
        self.entries = {}
        self.current_key = None
        self.current_entry_obj = None
        self.entry_list.clear()
        self.known_values = load_known_values()
        self._refresh_multi_combo(self.attype_combo, "AtType")
        self._refresh_multi_combo(self.atattribute_combo, "AtAttribute")
        self._clear_editors()
        self.own_file_btn.setEnabled(False)
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self.setEnabled(False)
        self.status_label.setText(self.texts["actor_wizard_no_pack_loaded"])

        if not self.ctx.loaded:
            return

        ref_pack_path, is_own, ref_obj, effective = list_attack_info_entries(self.ctx)
        if not ref_pack_path:
            self.status_label.setText(self.texts["actor_wizard_no_reference"])
            return

        self.ref_pack_path = ref_pack_path
        self.ref_is_own = is_own
        self.ref_obj = ref_obj
        self.entries = effective
        self.setEnabled(True)

        if is_own:
            self.status_label.setText(self.texts["actor_wizard_file_own"].format(ref_pack_path))
            self.own_file_btn.setEnabled(False)
        else:
            self.status_label.setText(
                self.texts["actor_wizard_file_inherited"].format(ref_pack_path)
            )
            self.own_file_btn.setEnabled(True)

        self._refresh_entry_list()

    def make_own_file(self):
        new_path = ensure_own_actor_component(self.ctx, "AttackerRef")
        if new_path:
            self.ctx.mark_dirty()
            self.ctx.log(self.texts["actor_wizard_log_own_file_created"].format(new_path))
            self.on_pack_loaded()
        else:
            QMessageBox.warning(
                self,
                self.texts["actor_wizard_error_title"],
                self.texts["actor_wizard_error_cannot_create_own_file"]
            )

    def _refresh_entry_list(self):
        self.entry_list.blockSignals(True)
        self.entry_list.clear()
        own_keys = sorted(k for k, v in self.entries.items() if v["source"] == "own")
        inherited_keys = sorted(k for k, v in self.entries.items() if v["source"] != "own")
        for key in own_keys + inherited_keys:
            source = self.entries[key]["source"]
            label = key if source == "own" else f"{key} ({self.texts['actor_wizard_inherited']})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.entry_list.addItem(item)
        self.entry_list.blockSignals(False)

    def _clear_editors(self):
        self.attype_combo.setCurrentIndex(-1)
        self.attype_combo.setEditText("")
        self.atattribute_combo.setCurrentIndex(-1)
        self.atattribute_combo.setEditText("")
        self.sensor_tag_edit.clear()
        self.editor.load_object({})

    def on_entry_selected(self, curr, prev):
        if not curr:
            self.current_key = None
            self.current_entry_obj = None
            self._clear_editors()
            return

        key = curr.data(Qt.UserRole)
        self.current_key = key
        entry_value = self.entries[key]["value"]
        self.current_entry_obj = entry_value

        effective_fields = get_effective_dict(self.ctx.files, entry_value, dict_key=None)

        at_type_values = _as_str_list(effective_fields.get("AtType", {}).get("value"))
        self.attype_combo.setEditText(", ".join(at_type_values))

        at_attr_values = _as_str_list(effective_fields.get("AtAttribute", {}).get("value"))
        self.atattribute_combo.setEditText(", ".join(at_attr_values))

        sensor_tags = _as_str_list(effective_fields.get("SensorTagName", {}).get("value"))
        self.sensor_tag_edit.setText(sensor_tags[0] if sensor_tags else "")

        self.editor.load_object(entry_value, exclude_top_level=ATTACK_ENTRY_DEDICATED_FIELDS)
        self.dirty = False
        set_button_dirty(self.apply_btn, False)

    def new_entry_dialog(self):
        if not self.ctx.loaded or not self.ref_pack_path:
            return
        if not self.ref_is_own:
            QMessageBox.information(
                self,
                self.texts["actor_wizard_info_title"],
                self.texts["actor_wizard_error_need_own_file_first"]
            )
            return
        dialog = NewAttackInfoEntryDialog(self.ctx.files, texts=self.texts, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        name, template_path = dialog.result_values()
        if not name:
            QMessageBox.information(
                self,
                self.texts["actor_wizard_info_title"],
                self.texts["actor_wizard_info_enter_entry_name"]
            )
            return

        ref_obj = parse_byml(self.ctx.files[self.ref_pack_path])
        if not isinstance(ref_obj.get("AttackInfo"), dict):
            ref_obj["AttackInfo"] = {}
        if name in ref_obj["AttackInfo"]:
            QMessageBox.warning(
                self,
                self.texts["actor_wizard_error_title"],
                self.texts["actor_wizard_error_attack_entry_exists"]
            )
            return

        new_entry = {}
        if template_path:
            new_entry["$parent"] = pack_path_to_work_path(template_path)
        ref_obj["AttackInfo"][name] = new_entry
        self.ctx.files[self.ref_pack_path] = dump_byml(ref_obj)
        self.ctx.mark_dirty()
        self.ctx.log(self.texts["actor_wizard_log_new_attack_entry_created"].format(name, self.ref_pack_path))
        self.on_pack_loaded()
        for i in range(self.entry_list.count()):
            if self.entry_list.item(i).data(Qt.UserRole) == name:
                self.entry_list.setCurrentRow(i)
                break

    def remove_selected_entry(self):
        if not self.current_key or not self.ref_pack_path:
            return
        if not self.ref_is_own:
            return
        ref_obj = parse_byml(self.ctx.files[self.ref_pack_path])
        attack_info = ref_obj.get("AttackInfo")
        if isinstance(attack_info, dict) and self.current_key in attack_info:
            del attack_info[self.current_key]
            self.ctx.files[self.ref_pack_path] = dump_byml(ref_obj)
            self.ctx.mark_dirty()
            self.on_pack_loaded()
        else:
            QMessageBox.information(
                self,
                self.texts["actor_wizard_info_title"],
                self.texts["actor_wizard_info_only_inherited"].format(self.current_key)
            )

    def apply_entry(self):
        if not self.current_key or not self.ref_pack_path or self.ref_obj is None:
            return

        self.editor.apply_to_object()

        if not isinstance(self.ref_obj.get("AttackInfo"), dict):
            self.ref_obj["AttackInfo"] = {}
        own_attack_info = self.ref_obj["AttackInfo"]

        if self.current_key in own_attack_info:
            entry = own_attack_info[self.current_key]
        else:
            entry = byml_copy(self.current_entry_obj)
            own_attack_info[self.current_key] = entry

        at_types = [v.strip() for v in self.attype_combo.currentText().split(",") if v.strip()]
        at_attrs = [v.strip() for v in self.atattribute_combo.currentText().split(",") if v.strip()]
        sensor_tag = self.sensor_tag_edit.text().strip()

        if at_types:
            entry["AtType"] = at_types
            for v in at_types:
                add_known_value("AtType", v)
        else:
            entry.pop("AtType", None)

        if at_attrs:
            entry["AtAttribute"] = at_attrs
            for v in at_attrs:
                add_known_value("AtAttribute", v)
        else:
            entry.pop("AtAttribute", None)

        if sensor_tag:
            entry["SensorTagName"] = [sensor_tag]
        else:
            entry.pop("SensorTagName", None)

        added_reflect_fields = []
        if "Reflect" in at_types:
            for field, default_value in REFLECT_DEFAULT_FIELDS.items():
                if field not in entry:
                    entry[field] = default_value
                    added_reflect_fields.append(field)

        self.ctx.files[self.ref_pack_path] = dump_byml(self.ref_obj)
        self.known_values = load_known_values()
        self._refresh_multi_combo(self.attype_combo, "AtType")
        self._refresh_multi_combo(self.atattribute_combo, "AtAttribute")
        self.ctx.mark_dirty()
        self.dirty = False
        set_button_dirty(self.apply_btn, False)
        self.ctx.log(self.texts["actor_wizard_log_attack_entry_applied"].format(self.current_key, self.ref_pack_path))
        if added_reflect_fields:
            self.ctx.log(self.texts["actor_wizard_log_reflect_fields_added"].format(", ".join(added_reflect_fields)))

        selected_key = self.current_key
        self.on_pack_loaded()
        for i in range(self.entry_list.count()):
            if self.entry_list.item(i).data(Qt.UserRole) == selected_key:
                self.entry_list.setCurrentRow(i)
                break


# Implementation details.

class VisualHitboxAdapter(HitboxEditorWidget):
    """Wrapper für den HitboxEditor, der die bestehende ctx-Infrastruktur nutzt."""
    def __init__(self, settings, texts, ctx, parent=None):
        super().__init__(settings, texts, parent)
        self.ctx = ctx
        # Menü und Toolbar ausblenden (eigene Speicher-Buttons verstecken)
        if hasattr(self, '_menubar') and self._menubar:
            self._menubar.hide()
        if hasattr(self, '_toolbar') and self._toolbar:
            self._toolbar.hide()
        if hasattr(self, 'save_btn'):
            self.save_btn.hide()
        if hasattr(self, 'actor_widget') and self.actor_widget:
            self.actor_widget.hide()
        # Eigene "Apply" Logik später über externen Button

    def set_actor_context(self, ctx):
        self.ctx = ctx
        self.files = ctx.files
        self.pack_path = ctx.pack_path
        self.dirty_paths.clear()
        inner_actors = get_actor_list(self.files)
        actor_name = ctx.actor_name if ctx.actor_name in inner_actors else (inner_actors[0] if inner_actors else None)
        if actor_name:
            self.current_actor = actor_name
            self.shape_params = collect_shape_params_for_actor(self.files, actor_name)
            self._rebuild_hitbox_list()
            self.update_plot()
        else:
            self.current_actor = None
            self.shape_params = []
            self._rebuild_hitbox_list()
            self._render_empty_plot()

    def apply_to_ctx(self):
        """Übernimmt alle Änderungen in ctx.files und markiert dirty."""
        for name, sp_data, path in self.shape_params:
            if path in self.dirty_paths:
                byml_obj = _native_to_byml(sp_data)
                byml_data = dump_byml(byml_obj)
                self.ctx.files[path] = byml_data
        if self.dirty_paths:
            self.dirty_paths.clear()
            self.ctx.mark_dirty()
            self._render_empty_plot()  # nur um UI zu refreshen
            self.update_plot()


# Implementation details.

class ActorWizardWidget(QWidget):
    def __init__(self, settings=None, texts=None, log_fn=None, parent=None, on_actor_changed=None):
        super().__init__(parent)
        self.settings = settings or {}
        self.texts = texts or {}
        self.ctx = WizardContext(texts=self.texts, log_fn=log_fn)
        self.on_actor_changed = on_actor_changed
        self.actors = []

        outer = QHBoxLayout(self)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel(self.texts["actor_wizard_search_label"]))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.texts["actor_wizard_search_placeholder"])
        self.search_edit.textChanged.connect(self.filter_actors)
        left_layout.addWidget(self.search_edit)

        self.actor_list = QListWidget()
        self.actor_list.currentItemChanged.connect(self.on_actor_selected)
        left_layout.addWidget(self.actor_list, stretch=1)

        refresh_btn = QPushButton(self.texts["actor_wizard_refresh_list"])
        refresh_btn.clicked.connect(self.load_actors)
        left_layout.addWidget(refresh_btn)

        left.setMaximumWidth(260)
        outer.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        save_row = QHBoxLayout()
        save_row.addStretch()
        self.save_btn = QPushButton(self.texts["actor_wizard_save_pack"])
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_pack)
        save_row.addWidget(self.save_btn)
        right_layout.addLayout(save_row)

        self.inner_tabs = QTabWidget()
        right_layout.addWidget(self.inner_tabs, stretch=1)

        self.physics_tab = PhysicsWizardTab(self.ctx, texts=self.texts)
        self.damage_tab = DamageWizardTab(self.ctx, texts=self.texts)

        # Build the hitbox tabs.
        self.hitbox_inner_tabs = QTabWidget()
        self.hitbox_manage_tab = HitboxWizardTab(self.ctx, texts=self.texts)
        self.hitbox_visual_tab = VisualHitboxAdapter(self.settings, self.texts, self.ctx)
        self.hitbox_behavior_tab = HitboxBehaviorWizardTab(self.ctx, texts=self.texts)
        self.hitbox_inner_tabs.addTab(self.hitbox_manage_tab, self.texts["hitbox_manage_tab"])
        self.hitbox_inner_tabs.addTab(self.hitbox_visual_tab, self.texts["hitbox_visual_tab"])
        self.hitbox_inner_tabs.addTab(self.hitbox_behavior_tab, self.texts["actor_wizard_tab_hitbox_behavior"])

        self.attack_tab = AttackInfoWizardTab(self.ctx, texts=self.texts)

        self.inner_tabs.addTab(self.physics_tab, self.texts["actor_wizard_tab_physics"])
        self.inner_tabs.addTab(self.damage_tab, self.texts["actor_wizard_tab_damage"])
        self.inner_tabs.addTab(self.hitbox_inner_tabs, self.texts["actor_wizard_tab_hitbox"])
        self.inner_tabs.addTab(self.attack_tab, self.texts["actor_wizard_tab_attack"])

        self.sub_tabs = [
            self.physics_tab, self.damage_tab,
            self.hitbox_manage_tab, self.hitbox_behavior_tab, self.attack_tab,
        ]
        self.visual_tab = self.hitbox_visual_tab

        outer.addWidget(right, stretch=1)

        self.load_actors()

    def _mod_folder(self):
        return self.settings.get("last_dest", "")

    def load_actors(self):
        src = self._mod_folder()
        pack_dir = os.path.join(src, "Pack", "Actor") if src else ""
        if src and os.path.isdir(pack_dir):
            self.actors = sorted(f[:-8] for f in os.listdir(pack_dir) if f.endswith(".pack.zs"))
        else:
            self.actors = []
        self.filter_actors()

    def filter_actors(self):
        s = self.search_edit.text().lower()
        self.actor_list.clear()
        for a in self.actors:
            if s in a.lower():
                self.actor_list.addItem(a)

    def on_actor_selected(self, curr, prev):
        if not curr:
            return

        if self.ctx.loaded and self.ctx.dirty:
            reply = QMessageBox.question(
                self,
                self.texts["actor_wizard_unsaved_title"],
                self.texts["actor_wizard_unsaved_text"],
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
            )
            if reply == QMessageBox.Cancel:
                if prev:
                    self.actor_list.setCurrentItem(prev)
                else:
                    self.actor_list.clearSelection()
                return
            elif reply == QMessageBox.Save:
                self.save_pack()

        actor_name = curr.text()
        src = self._mod_folder()
        pack_path = os.path.join(src, "Pack", "Actor", f"{actor_name}.pack.zs")
        if not os.path.isfile(pack_path):
            QMessageBox.warning(
                self,
                self.texts["actor_wizard_error_title"],
                self.texts["actor_wizard_error_pack_not_found"].format(pack_path)
            )
            return

        ok = self.ctx.load(pack_path, actor_name)
        if not ok:
            QMessageBox.warning(
                self,
                self.texts["actor_wizard_error_title"],
                self.texts["actor_wizard_error_no_actor_param"].format(self.ctx.actor_param_path)
            )
            self.save_btn.setEnabled(False)
            for tab in self.sub_tabs:
                tab.on_pack_loaded()
            if self.visual_tab:
                self.visual_tab.set_actor_context(self.ctx)
            return

        self.save_btn.setEnabled(True)
        set_button_dirty(self.save_btn, False)  # nach Laden immer grau
        for tab in self.sub_tabs:
            tab.on_pack_loaded()
        if self.visual_tab:
            self.visual_tab.set_actor_context(self.ctx)

        # Globalen Dirty-Status überwachen
        self.ctx.dirty = False
        self.update_save_button()

    def update_save_button(self):
        """Aktualisiert den großen Pack-speichern-Button basierend auf ctx.dirty."""
        set_button_dirty(self.save_btn, self.ctx.dirty)

    def save_pack(self):
        if not self.ctx.loaded:
            return
        # Vor dem Speichern noch alle visuellen Änderungen übernehmen (falls vorhanden)
        if self.visual_tab and self.visual_tab.dirty_paths:
            self.visual_tab.apply_to_ctx()
        self.ctx.save()
        self.save_btn.setEnabled(True)
        set_button_dirty(self.save_btn, False)
        QMessageBox.information(
            self,
            self.texts["info_dialog_title"],
            self.texts["actor_wizard_saved"]
        )
        if callable(self.on_actor_changed):
            self.on_actor_changed()

    def refresh_texts(self, texts):
        self.texts = texts
        self.ctx.texts = texts
        self.save_btn.setText(self.texts["actor_wizard_save_pack"])
        self.inner_tabs.setTabText(0, self.texts["actor_wizard_tab_physics"])
        self.inner_tabs.setTabText(1, self.texts["actor_wizard_tab_damage"])
        self.inner_tabs.setTabText(2, self.texts["actor_wizard_tab_hitbox"])
        self.inner_tabs.setTabText(3, self.texts["actor_wizard_tab_attack"])

        # Unter-Titel für Hitbox-Inner-Tabs aktualisieren
        self.hitbox_inner_tabs.setTabText(0, self.texts["hitbox_manage_tab"])
        self.hitbox_inner_tabs.setTabText(1, self.texts["hitbox_visual_tab"])
        self.hitbox_inner_tabs.setTabText(2, self.texts["actor_wizard_tab_hitbox_behavior"])

        # Sub-Tabs
        for tab in self.sub_tabs:
            if hasattr(tab, 'texts'):
                tab.texts = texts
        if self.visual_tab:
            self.visual_tab.texts = texts
            self.visual_tab.refresh_texts(texts)
