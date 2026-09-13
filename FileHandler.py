#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Implementation details."""

import os
import struct

import zstandard as zstd

try:
    import byml as byml_legacy
    HAVE_LEGACY_BYML = True
except ImportError:
    HAVE_LEGACY_BYML = False


# Implementation details.

def is_byml_container(obj):
    return isinstance(obj, (byml_legacy.SortedDict, list))

def byml_set_field_computed(obj, field, compute_fn):
    if not HAVE_LEGACY_BYML:
        return
    if isinstance(obj, dict):
        if field in obj and isinstance(obj[field], str):
            obj[field] = compute_fn(obj[field])
        for v in obj.values():
            byml_set_field_computed(v, field, compute_fn)
    elif isinstance(obj, list):
        for v in obj:
            byml_set_field_computed(v, field, compute_fn)

def byml_copy(obj):
    if isinstance(obj, dict):
        new_dict = type(obj)() if hasattr(type(obj), '__call__') else {}
        for k, v in obj.items():
            new_dict[k] = byml_copy(v)
        return new_dict
    if isinstance(obj, list):
        return [byml_copy(v) for v in obj]
    return obj

def byml_set_field(obj, field, new_value):
    if isinstance(obj, dict):
        if field in obj:
            obj[field] = new_value
        for v in obj.values():
            byml_set_field(v, field, new_value)
    elif isinstance(obj, list):
        for v in obj:
            byml_set_field(v, field, new_value)

def byml_find_field(obj, field):
    if isinstance(obj, dict):
        if field in obj:
            return obj[field]
        for v in obj.values():
            result = byml_find_field(v, field)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for v in obj:
            result = byml_find_field(v, field)
            if result is not None:
                return result
    return None

def get_byml_leaf_type(value):
    """Implementation details."""
    if not HAVE_LEGACY_BYML:
        return None
    for type_name in ("Int64", "UInt64", "Int", "UInt", "Double", "Float"):
        t = getattr(byml_legacy, type_name, None)
        if t is not None and type(value) is t:
            return t
    return None

def make_byml_int(value):
    """Implementation details."""
    if HAVE_LEGACY_BYML:
        return byml_legacy.Int(int(value))
    return int(value)

def parse_byml(data: bytes):
    if not HAVE_LEGACY_BYML:
        raise RuntimeError("byml ist nicht installiert.")
    return byml_legacy.Byml(data).parse()

def dump_byml(obj) -> bytes:
    if not HAVE_LEGACY_BYML:
        raise RuntimeError("byml ist nicht installiert.")
    writer = byml_legacy.Writer(obj)
    return writer.get_bytes()

def decompress_zs(path: str) -> bytes:
    with open(path, "rb") as f:
        return zstd.ZstdDecompressor().decompress(f.read())

def compress_and_write_zs(path: str, data: bytes, level=19):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(zstd.ZstdCompressor(level=level).compress(data))

def try_extract_bfres_embeds(raw):
    if raw[:4] != b"FRES" or len(raw) < 0xEC+2: return {}
    bom = raw[0x0C:0x0E]; endian = ">" if bom == b"\xfe\xff" else "<"
    embed_count = struct.unpack_from(endian + "H", raw, 0xEC)[0]
    if embed_count == 0: return {}
    array_rel = struct.unpack_from(endian + "q", raw, 0xB8)[0]
    dict_rel = struct.unpack_from(endian + "q", raw, 0xC0)[0]
    if array_rel == 0 or dict_rel == 0: return {}
    array_addr = 0xB8 + array_rel; dict_addr = 0xC0 + dict_rel
    num_nodes = struct.unpack_from(endian + "I", raw, dict_addr)[0]
    root_idx = struct.unpack_from(endian + "I", raw, dict_addr + 4)[0]
    if num_nodes == 0: return {}
    NODE_SIZE = 0x18; base = dict_addr + 8
    items = []; visited = set(); stack = [root_idx]
    while stack:
        idx = stack.pop()
        if idx < 0 or idx >= num_nodes or idx in visited: continue
        visited.add(idx); node = base + idx * NODE_SIZE
        if node + NODE_SIZE > len(raw): continue
        left = struct.unpack_from(endian + "i", raw, node)[0]
        right = struct.unpack_from(endian + "i", raw, node + 4)[0]
        key_rel = struct.unpack_from(endian + "q", raw, node + 8)[0]
        value = struct.unpack_from(endian + "Q", raw, node + 16)[0]
        name = ""
        if key_rel != 0:
            key_addr = node + 8 + key_rel
            s = raw.find(b"\x00", key_addr)
            if s != -1 and s - key_addr <= 200:
                name = raw[key_addr:s].decode("utf-8", errors="ignore")
        if name: items.append((name, value))
        if left >= 0: stack.append(left)
        if right >= 0: stack.append(right)
    ENTRY_SIZE = 0x10; embeds = {}
    for name, index in items:
        entry = array_addr + index * ENTRY_SIZE
        if entry + ENTRY_SIZE > len(raw): continue
        file_size = struct.unpack_from(endian + "I", raw, entry)[0]
        data_offset = struct.unpack_from(endian + "q", raw, entry + 8)[0]
        if data_offset < 0 or data_offset + file_size > len(raw): continue
        embeds[name] = raw[data_offset:data_offset + file_size]
    return embeds

def _byml_to_native(obj):
    if isinstance(obj, dict):
        return {k: _byml_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_byml_to_native(v) for v in obj]
    if isinstance(obj, bytes):
        return obj.hex()
    if not HAVE_LEGACY_BYML:
        return obj
    for type_name in ("Int64", "UInt64", "Int", "UInt", "Double", "Float"):
        t = getattr(byml_legacy, type_name, None)
        if t is not None and type(obj) is t:
            is_float = type_name in ("Double", "Float")
            return {"__byml_type__": type_name, "value": (float(obj) if is_float else int(obj))}
    return obj

def _native_to_byml(obj):
    if isinstance(obj, dict):
        if "__byml_type__" in obj and "value" in obj:
            type_name = obj["__byml_type__"]
            value = obj["value"]
            type_class = getattr(byml_legacy, type_name, None)
            if type_class is not None:
                return type_class(value)
            if "Float" in type_name or "Double" in type_name:
                return float(value)
            return int(value)
        else:
            return {k: _native_to_byml(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_native_to_byml(v) for v in obj]
    if isinstance(obj, str):
        return obj
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return byml_legacy.Int(obj)
    if isinstance(obj, float):
        return byml_legacy.Float(obj)
    return obj

