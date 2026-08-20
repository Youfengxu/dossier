#!/usr/bin/env python3
"""Insert a column into a sheet, shifting everything to its right.

    ./insert-column.py --xlsx book.xlsx --sheet "Comments" --after I \
        --header "Assessment" --out book-new.xlsx

`writeback.py` fills a column that already exists. This makes room for one that
does not, which is a different job: every cell from the insertion point rightward
has to be renamed (J5 -> K5), or the workbook opens with the new content sitting
on top of the old.

Everything else in the file is copied byte-for-byte — styles, other sheets, the
shared string table. Only the target sheet's XML is rewritten, and only the cell
references in it.

WHY NOT JUST APPEND AT THE END. Because a reviewer reads a matrix left to right
and a verdict belongs beside the verdict it revises, not eleven columns away past
the notes. The position is the point; if appending were acceptable this script
would not need to exist.
"""

import argparse
import os
import re
import shutil
import sys
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CELL = re.compile(r'\b([A-Z]{1,3})(\d+)\b')


def col_to_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def num_to_col(number):
    out = ""
    while number:
        number, rem = divmod(number - 1, 26)
        out = chr(65 + rem) + out
    return out


def sheet_path(zf, sheet_name):
    book = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    for sheet in book.findall(f"{{{NS}}}sheets/{{{NS}}}sheet"):
        if sheet.get("name") == sheet_name:
            rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument"
                            "/2006/relationships}id")
            return "xl/" + target[rid].lstrip("/").replace("xl/", "", 1)
    names = [s.get("name") for s in book.findall(f"{{{NS}}}sheets/{{{NS}}}sheet")]
    sys.exit(f"no sheet {sheet_name!r}; have: " + ", ".join(names))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", "--matrix", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--after", required=True,
                        help="column letter; the new column lands to its right")
    parser.add_argument("--header", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    at = col_to_num(args.after.upper()) + 1
    src = os.path.abspath(os.path.expanduser(args.xlsx))
    out = os.path.abspath(os.path.expanduser(args.out))
    if src == out:
        sys.exit("refusing to write over the source; pass a different --out")

    with zipfile.ZipFile(src) as zf:
        part = sheet_path(zf, args.sheet)
        xml = zf.read(part).decode("utf-8")
        members = {n: zf.read(n) for n in zf.namelist()}

    # Rename right-to-left. Left-to-right would rewrite J->K and then meet that
    # same cell again as K and push it to L, walking every cell to the end of the
    # sheet instead of one place.
    def shift(match):
        letters, row = match.group(1), match.group(2)
        number = col_to_num(letters)
        return (num_to_col(number + 1) + row) if number >= at else match.group(0)

    root = ET.fromstring(xml)
    ET.register_namespace("", NS)
    moved = 0
    for row in root.iter(f"{{{NS}}}row"):
        cells = [c for c in row.findall(f"{{{NS}}}c")]
        for cell in reversed(cells):
            ref = cell.get("r") or ""
            m = CELL.fullmatch(ref)
            if m and col_to_num(m.group(1)) >= at:
                cell.set("r", shift(m))
                moved += 1

    # The header cell for the new column, as an inline string so it needs no
    # entry in the shared string table.
    if args.header:
        first = root.find(f"{{{NS}}}sheetData/{{{NS}}}row")
        if first is not None:
            cell = ET.SubElement(first, f"{{{NS}}}c")
            cell.set("r", f"{num_to_col(at)}{first.get('r', '1')}")
            cell.set("t", "inlineStr")
            is_el = ET.SubElement(cell, f"{{{NS}}}is")
            t = ET.SubElement(is_el, f"{{{NS}}}t")
            t.text = args.header
            # Cells must be in column order or Excel repairs the file.
            order = lambda c: col_to_num(CELL.fullmatch(c.get("r") or "A1").group(1))
            for child in sorted(first.findall(f"{{{NS}}}c"), key=order):
                first.remove(child); first.append(child)

    # A stale <dimension> makes some readers ignore the new column entirely.
    dim = root.find(f"{{{NS}}}dimension")
    if dim is not None and ":" in (dim.get("ref") or ""):
        start, end = dim.get("ref").split(":")
        m = CELL.fullmatch(end)
        if m and col_to_num(m.group(1)) >= at:
            dim.set("ref", f"{start}:{num_to_col(col_to_num(m.group(1)) + 1)}{m.group(2)}")

    members[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    print(f"inserted column {num_to_col(at)} in {args.sheet!r}; "
          f"{moved} cell reference(s) shifted right")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
