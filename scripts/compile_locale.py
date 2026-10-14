"""
Сборка locale/ru/LC_MESSAGES/django.mo из django.po без GNU gettext
(на Windows его обычно нет). Запуск: python scripts/compile_locale.py
"""

import ast
import struct
from pathlib import Path

PO = Path(__file__).resolve().parent.parent / "locale/ru/LC_MESSAGES/django.po"


def parse(path: Path) -> dict[str, str]:
    messages, key, current = {}, None, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            key, current = ast.literal_eval(line[6:]), "id"
        elif line.startswith("msgstr "):
            messages[key] = ast.literal_eval(line[7:])
            current = "str"
        elif line.startswith('"') and key is not None:
            if current == "id":
                key += ast.literal_eval(line)
            else:
                messages[key] += ast.literal_eval(line)
    return messages


def write_mo(messages: dict[str, str], path: Path) -> None:
    keys = sorted(messages)
    ids = b"".join(k.encode() + b"\0" for k in keys)
    strs = b"".join(messages[k].encode() + b"\0" for k in keys)
    n = len(keys)
    keys_start = 7 * 4
    values_start = keys_start + n * 8
    data_start = values_start + n * 8
    offsets, pos = [], 0
    for k in keys:
        length = len(k.encode())
        offsets.append((length, data_start + pos))
        pos += length + 1
    values_offset, pos = [], 0
    for k in keys:
        length = len(messages[k].encode())
        values_offset.append((length, data_start + len(ids) + pos))
        pos += length + 1
    header = struct.pack("Iiiiiii", 0x950412DE, 0, n, keys_start, values_start, 0, 0)
    table = b"".join(struct.pack("ii", *o) for o in offsets + values_offset)
    path.write_bytes(header + table + ids + strs)


if __name__ == "__main__":
    write_mo(parse(PO), PO.with_suffix(".mo"))
    print("OK:", PO.with_suffix(".mo"))
