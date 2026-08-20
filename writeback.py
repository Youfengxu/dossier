#!/usr/bin/env python3
"""Write closure evidence back into the comment matrix.

The matrix has a column for how and where each comment was adjudicated. Filling
it by hand is the last manual step in the loop: the evidence already exists as
line numbers in a frozen document, and copying 85 of them across is exactly the
kind of transcription that introduces errors nobody catches.

    ./writeback.py --project . --from deliverable-v1 --to deliverable-v2 \
        --xlsx source/feedback-matrix.xlsx --sheet "Comments"

Writes a NEW workbook — `<name>-annotated.xlsx` — and a CSV of the same content
for anyone who would rather paste two columns than open a second file.

IT NEVER TOUCHES THE INPUT. The matrix is a jointly-agreed artefact; a tool that
edits it in place can silently destroy the other side's work, and "I regenerated
it" is not a defence anyone should have to make. Existing cell contents are left
alone too, unless --overwrite is passed: a filled cell is someone's considered
entry, and the default assumption is that they meant it.

WHAT THE EVIDENCE SAYS, AND WHAT IT DOES NOT. Each cell records what the text
did between the two revisions and where to look. UNCHANGED and STILL ABSENT are
assertions — the text did not move. CHANGED is a pointer, not a verdict: it says
the revision engaged with the area, never that the change is adequate. The cell
wording keeps that distinction, because this column will be read by people who
were not in this conversation.
"""

import argparse
import csv
import os
import re
import shutil
import sys
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import closure  # noqa: E402
import matrix as matrix_reader  # noqa: E402

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# What each state means in a cell a client will read. Deliberately plain, and
# deliberately not claiming more than the check supports.
PHRASING = {
    "UNCHANGED": "Not addressed. Text carrying this comment is identical in {new}",
    "STILL ABSENT": "Not addressed. Absent from both {old} and {new}",
    "REMOVED": "Text present in {old} is absent from {new} — confirm fix vs regression",
    "ADDED": "Now present in {new}, absent from {old}",
    "CHANGED": "Text changed between {old} and {new} — review against the comment",
    "TOO BROAD": "Not assessed: search terms match too much of the document",
    "ABSENT": "Not assessed: search terms match nothing in either revision",
}


def column_index(letter):
    n = 0
    for char in letter.upper():
        n = n * 26 + (ord(char) - 64)
    return n


def evidence(row, old_slug, new_slug, locators):
    """One cell's worth: the verdict, then where to look."""
    if not row["terms"]:
        return "Not assessed: no distinctive search term for this comment"
    text = PHRASING.get(row["state"], row["state"]).format(
        old=old_slug, new=new_slug)
    cited = row["new"] or row["old"]
    which = new_slug if row["new"] else old_slug
    if cited:
        refs = list(cited)[:locators]
        text += " (%s: %s" % (which, ", ".join(f"line {n}" for n in refs))
        if len(cited) > locators:
            text += f", +{len(cited) - locators} more"
        text += ")"
    if row["state"] == "CHANGED" and row["detail"]:
        text += f" [{row['detail']} lines]"
    return text


def annotate_csv(src, dst, values, col_letter, overwrite):
    """Set one column of a delimited file, preserving everything else.

    EVERY physical record is rewritten, including the blank ones the reader drops.
    The reader skips blanks so callers are not handed empty dicts; the writer must
    keep them so record N in equals record N out. Dropping them here would shift
    every row below a gap upward by one and silently file each answer against the
    wrong comment — the same defect `__row__` exists to prevent, reintroduced at
    the other end of the round trip.
    """
    with open(src, newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(handle, dialect))

    index = matrix_reader.col_num(col_letter) - 1
    written = skipped = 0
    for record, fields in enumerate(rows, start=1):
        text = values.get(str(record))
        if text is None:
            continue
        while len(fields) <= index:
            fields.append("")
        if fields[index].strip() and not overwrite:
            skipped += 1
            continue
        fields[index] = text
        written += 1

    width = max((len(f) for f in rows), default=0)
    with open(dst, "w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle, delimiter=dialect.delimiter,
                         quoting=csv.QUOTE_MINIMAL)
        for fields in rows:
            out.writerow(fields + [""] * (width - len(fields)))
    return written, skipped


def annotate(src, dst, sheet_name, values, col_letter, overwrite):
    """Copy the workbook, setting one column of one sheet. Inline strings only.

    Inline strings keep the edit local to the worksheet part: adding entries to
    sharedStrings.xml means rewriting every index that follows, and one slip
    there rewrites unrelated cells into the wrong text.
    """
    if src.lower().endswith(matrix_reader.CSV_SUFFIXES):
        return annotate_csv(src, dst, values, col_letter, overwrite)
    zin = zipfile.ZipFile(src)
    book = ET.fromstring(zin.read("xl/workbook.xml"))
    rels = ET.fromstring(zin.read("xl/_rels/workbook.xml.rels"))
    target = None
    for sheet in book.findall(f"{{{NS}}}sheets/{{{NS}}}sheet"):
        if sheet.get("name") == sheet_name:
            rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/"
                            "2006/relationships}id")
            for rel in rels:
                if rel.get("Id") == rid:
                    target = "xl/" + rel.get("Target").lstrip("/").replace(
                        "xl/", "", 1)
    if target is None:
        sys.exit(f"no sheet {sheet_name!r} in {src}")

    xml = zin.read(target).decode("utf-8")
    col = col_letter.upper()
    index = column_index(col)
    written, skipped = 0, 0

    def patch_row(match):
        nonlocal written, skipped
        block = match.group(0)
        number = match.group(1)
        if number not in values:
            return block
        cell = re.search(r'<c r="%s%s"[^>]*(?:/>|>.*?</c>)' % (col, number),
                         block, re.S)
        if cell and not overwrite:
            has_value = re.search(r"<v>|<is>", cell.group(0))
            if has_value:
                skipped += 1
                return block
        new_cell = ('<c r="%s%s" t="inlineStr"><is><t xml:space="preserve">%s'
                    '</t></is></c>' % (col, number, escape(values[number])))
        written += 1
        if cell:
            return block.replace(cell.group(0), new_cell)
        # Insert in column order: before the first cell whose column sorts after
        # ours. Excel tolerates unordered cells; other readers do not.
        for existing in re.finditer(r'<c r="([A-Z]+)%s"' % number, block):
            if column_index(existing.group(1)) > index:
                return (block[:existing.start()] + new_cell
                        + block[existing.start():])
        return re.sub(r"</row>$", new_cell + "</row>", block)

    xml = re.sub(r'<row r="(\d+)".*?</row>', patch_row, xml, flags=re.S)

    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = xml.encode("utf-8") if item.filename == target \
                else zin.read(item.filename)
            zout.writestr(item, data)
    return written, skipped


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--from", dest="old", required=True)
    parser.add_argument("--to", dest="new", required=True)
    parser.add_argument("--xlsx", "--matrix", required=True)
    parser.add_argument("--sheet", default="Comments")
    parser.add_argument("--col", default="I",
                        help="column to fill (default I, 'How/where comment "
                             "adjudicated'). Use K for the client inputs "
                             "column instead.")
    parser.add_argument("--id-col", default="A")
    parser.add_argument("--map", default="register-map.yaml")
    parser.add_argument("--lexicon", default="lexicon.yaml")
    parser.add_argument("--too-broad", type=int, default=120)
    parser.add_argument("--locators", type=int, default=3)
    parser.add_argument("--out", help="default <name>-annotated.xlsx")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace cells that already have content")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    old_lines = closure.load(project, args.old)
    new_lines = closure.load(project, args.new)

    findings = closure.load_yaml(
        os.path.join(project, args.map)).get("findings", [])
    lexicon_path = os.path.join(project, args.lexicon)
    lexicon = {}
    if os.path.exists(lexicon_path):
        lexicon = {k.lower(): v
                   for k, v in (closure.load_yaml(lexicon_path) or {}).items()}

    verdicts = {}
    for finding in findings:
        terms = closure.expand(finding.get("terms", []), lexicon)
        old_hits = closure.hits(old_lines, terms)
        new_hits = closure.hits(new_lines, terms)
        state, detail = closure.classify(
            old_hits, new_hits, args.too_broad,
            is_absence=bool(finding.get("absence")),
            has_terms=bool(finding.get("terms")))
        verdicts[finding["id"]] = {
            "state": state, "detail": detail or "", "terms": terms,
            "old": old_hits, "new": new_hits,
        }

    xlsx = args.xlsx if os.path.isabs(args.xlsx) \
        else os.path.join(project, args.xlsx)
    rows = matrix_reader.read_sheet(xlsx, args.sheet)

    # Excel row numbers, not list positions — read_sheet drops blank rows, so
    # the two diverge and writing to the wrong row is silent and catastrophic.
    values, table, unmapped = {}, [], []
    # The row's own number, not its position in a list the reader compacted.
    # enumerate() was correct only for gapless sheets; one deleted row in Excel
    # and every value after it wrote one row high, into the wrong comment.
    for position, row in ((row.get("__row__", n), row)
                          for n, row in enumerate(rows[1:], start=2)):
        rid = row.get(args.id_col, "").strip()
        if not rid:
            continue
        if rid not in verdicts:
            unmapped.append(rid)
            continue
        text = evidence(verdicts[rid], args.old, args.new, args.locators)
        values[str(position)] = text
        table.append((rid, verdicts[rid]["state"], text))

    out = args.out or os.path.join(
        project, os.path.splitext(os.path.basename(xlsx))[0] + "-annotated.xlsx")
    if not os.path.isabs(out):
        out = os.path.join(project, out)
    if os.path.abspath(out) == os.path.abspath(xlsx):
        sys.exit("refusing to write over the source matrix")

    written, skipped = annotate(xlsx, out, args.sheet, values, args.col,
                                args.overwrite)

    csv_path = os.path.splitext(out)[0] + ".csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ID", "State", f"Column {args.col.upper()}"])
        writer.writerows(table)

    print(f"wrote {out}\n      {csv_path}")
    print(f"  column {args.col.upper()}: {written} cell(s) filled"
          + (f", {skipped} left alone (already had content — --overwrite to "
             f"replace)" if skipped else ""))
    counts = {}
    for _, state, _ in table:
        counts[state] = counts.get(state, 0) + 1
    print("  " + "  ".join(f"{s}={n}" for s, n in sorted(counts.items())))
    if unmapped:
        print(f"\n  {len(unmapped)} matrix row(s) are not in {args.map} and were "
              f"left blank —\n  re-run matrix.py: " + " ".join(unmapped[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
