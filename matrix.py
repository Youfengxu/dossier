#!/usr/bin/env python3
"""Build a register-map from a comment matrix spreadsheet.

When the client and the vendor agree a feedback matrix, its IDs become the only
ones that matter — they are what the vendor writes against and what the status
column tracks. A register-map keyed on anything else is speaking a private
language, and every finding it reports has to be translated by hand before it
can be sent.

This reads the matrix, derives distinctive `terms` for each row, and writes a
register-map.yaml keyed on the matrix's own IDs.

    ./matrix.py --project . --xlsx ~/Downloads/matrix.xlsx --sheet "Comments" \
        --doc deliverable-v1 --model Qwen3-Coder-Next-UD-Q4_K_M --url http://gx10:8085/v1/chat/completions

THE MODEL PROPOSES, THE CORPUS DISPOSES. Terms decide whether a finding can be
tracked across revisions at all, and a term chosen badly fails silently: too
common and every revision looks changed, absent and every revision looks
untouched. So the model only nominates candidates from the row's own wording,
and each is then counted against the frozen document. A candidate matching
nothing is dropped; one matching more of the document than --too-broad is
dropped; a row left with no surviving term is written out with an empty list and
counted, so it is visibly unfinished rather than quietly useless.

Rows whose substance is that something is MISSING are marked `absence: true` —
for those, matching nothing is the finding rather than a broken query, and only
the model can tell the two apart from the comment text.

Nothing here reads the spreadsheet's content into anything but the local
endpoint. The comment text is client material; it goes to the model named on the
command line and nowhere else.
"""

import argparse
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Client, LLMError  # noqa: E402
import closure  # noqa: E402

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
PROMPT_VERSION = "matrix-1"

SYSTEM = """You extract search terms from a review comment about a technical document.

Reply with JSON only:
{"terms":["...","..."],"class":"compliance|regression|coherence|mechanism|framing","absence":true|false,"title":"short title, max 12 words"}

TERMS are the strings a reader would grep for to find the passages this comment
is about. Take them ONLY from the comment's own wording. Good terms are:
  - identifiers quoted in the comment (IF-NAR-004, BM-G02, ORC-G01)
  - type, field or function names (MembershipUpdate, ExposureEvent, get_state)
  - distinctive multi-word phrases the comment quotes from the document
Bad terms are ordinary words that would match hundreds of lines: "risk",
"scope", "commit", "attention", "analysis", "interface", "data", "model".
Prefer 2 to 5 terms. Fewer good terms beats more weak ones. If the comment
quotes nothing distinctive, return the most specific noun phrases it contains.

CLASS is the kind of reasoning that produced the comment:
  compliance  the requirements ask for X and the deliverable lacks X
  regression  something present in an earlier revision is gone or worse
  coherence   the document contradicts itself, or a capability has no owner
  mechanism   the described design does not do what it claims when worked through
  framing     needs knowledge from outside the document — an agreed decision, a meeting

ABSENCE is true when the comment's substance is that something is MISSING or
NEVER DEFINED — so finding zero occurrences would be the answer, not a failure.
It is false when the comment is about text that exists but is wrong, unclear or
contradictory."""


def validate(obj):
    if not isinstance(obj, dict):
        return "reply must be a JSON object"
    if not isinstance(obj.get("terms"), list) or not obj["terms"]:
        return "'terms' must be a non-empty array of strings"
    if any(not isinstance(t, str) or not t.strip() for t in obj["terms"]):
        return "every term must be a non-empty string"
    if obj.get("class") not in ("compliance", "regression", "coherence",
                               "mechanism", "framing"):
        return "'class' must be one of the five listed"
    if not isinstance(obj.get("absence"), bool):
        return "'absence' must be true or false"
    if not isinstance(obj.get("title"), str) or not obj["title"].strip():
        return "'title' must be a non-empty string"
    return None


def read_sheet(path, sheet_name):
    """Return [{col_letter: value}] for the named sheet. Stdlib only."""
    z = zipfile.ZipFile(path)
    shared = []
    try:
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(
            "{%s}t" % NS["m"])) for si in sst.findall("m:si", NS)]
    except KeyError:
        pass

    book = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rel_target = {r.get("Id"): r.get("Target") for r in rels}
    target = None
    names = []
    for sheet in book.findall(".//m:sheet", NS):
        names.append(sheet.get("name"))
        if sheet.get("name") == sheet_name:
            rid = sheet.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rel_target.get(rid)
    if target is None:
        sys.exit(f"no sheet {sheet_name!r}; have: " + ", ".join(names))
    part = "xl/" + target.lstrip("/").replace("xl/", "", 1)

    def value(cell):
        if cell.get("t") == "inlineStr":
            return "".join(t.text or "" for t in cell.iter("{%s}t" % NS["m"]))
        node = cell.find("m:v", NS)
        if node is None:
            return ""
        if cell.get("t") == "s":
            return shared[int(node.text)]
        return node.text or ""

    rows = []
    for row in ET.fromstring(z.read(part)).findall(".//m:row", NS):
        cells = {}
        for cell in row.findall("m:c", NS):
            cells[re.sub(r"\d", "", cell.get("r"))] = value(cell)
        if any(v.strip() for v in cells.values()):
            rows.append(cells)
    return rows


def yaml_escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--doc", required=True,
                        help="frozen slug to validate candidate terms against")
    parser.add_argument("--id-col", default="A")
    parser.add_argument("--comment-col", default="D")
    parser.add_argument("--theme-col", default="B")
    parser.add_argument("--section-col", default="C")
    parser.add_argument("--expectation-col", default="E")
    parser.add_argument("--model", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--lexicon", default="lexicon.yaml")
    parser.add_argument("--too-broad", type=int, default=120)
    parser.add_argument("--out", default="register-map.yaml")
    parser.add_argument("--limit", type=int, help="first N rows, for a smoke test")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    lines = closure.load(project, args.doc)

    lexicon_path = os.path.join(project, args.lexicon)
    lexicon = {}
    if os.path.exists(lexicon_path):
        lexicon = {k.lower(): v
                   for k, v in (closure.load_yaml(lexicon_path) or {}).items()}

    rows = read_sheet(os.path.expanduser(args.xlsx), args.sheet)
    header = rows[0] if rows else {}
    # Every row that has an ID, whether or not it has comment text yet. A row
    # the client has numbered but not yet written is a row that exists; leaving
    # it out of the map makes it invisible to closure and to check-map, and it
    # reappears as a surprise the day someone fills the cell in. Carried through
    # with no terms instead, so it is counted and visibly unfinished.
    body = [r for r in rows[1:] if r.get(args.id_col, "").strip()]
    if args.limit:
        body = body[:args.limit]
    print(f"{args.sheet}: {len(body)} rows with an ID "
          f"(header: {header.get(args.id_col)} / {header.get(args.comment_col)})")

    kwargs = {}
    if args.model:
        kwargs["model"] = args.model
    if args.url:
        kwargs["url"] = args.url
    client = Client(project, prompt_version=PROMPT_VERSION, max_tokens=600,
                    **kwargs)

    entries, empty, failed, blank = [], [], [], []
    stats = {"proposed": 0, "kept": 0, "absent": 0, "broad": 0,
             "retried": 0, "rescued": 0, "widened": 0}
    for index, row in enumerate(body, 1):
        rid = row[args.id_col].strip()
        comment = row.get(args.comment_col, "").strip()

        # No comment text means nothing to extract terms from. Emit the row and
        # move on rather than asking the model to invent something from a theme.
        if not comment:
            blank.append(rid)
            entries.append({
                "id": rid, "class": "unclassified", "title": "",
                "absence": False, "terms": [],
                "theme": row.get(args.theme_col, "").strip(),
                "section": row.get(args.section_col, "").strip(),
            })
            continue

        context = "\n".join(filter(None, [
            f"Theme: {row.get(args.theme_col,'').strip()}",
            f"Section: {row.get(args.section_col,'').strip()}",
            f"Comment: {comment}",
            f"Expectation: {row.get(args.expectation_col,'').strip()}",
        ]))
        try:
            reply = client.ask(SYSTEM, context, validate=validate, label=rid)
        except LLMError as error:
            # Still emit the row. A row dropped on a transport error would leave
            # a map that looks complete and silently never asks about that
            # finding — the same failure that hid two findings in a live
            # register for weeks. An empty term list is visibly unfinished.
            print(f"  {rid}: CALL FAILED — {str(error)[:80]}")
            failed.append(rid)
            entries.append({
                "id": rid, "class": "unclassified", "title": "",
                "absence": False, "terms": [],
                "theme": row.get(args.theme_col, "").strip(),
                "section": row.get(args.section_col, "").strip(),
            })
            continue

        def sift(terms, absence):
            """Split candidates by what the frozen document says about them."""
            kept, rejected = [], []
            for term in terms:
                count = len(closure.hits(lines, closure.expand([term], lexicon)))
                if count == 0 and not absence:
                    rejected.append((term, "matches nothing"))
                elif count > args.too_broad:
                    rejected.append((term, f"matches {count} lines, too common"))
                else:
                    kept.append(term)
            return kept, rejected

        stats["proposed"] += len(reply["terms"])
        kept, rejected = sift(reply["terms"], reply["absence"])

        # One corrective retry, told exactly what failed and why. The model
        # cannot know a term's frequency in a document it has never seen, so a
        # first attempt that picks package names is a reasonable guess rather
        # than a mistake — it just needs the counts fed back. This mirrors the
        # schema retry in llm.py: measure, report the specific failure, ask once
        # more, then stop and let a human decide.
        if not kept and rejected:
            complaint = "; ".join(f'"{t}" {why}' for t, why in rejected)
            retry_context = (
                f"{context}\n\nYour previous terms were all unusable against "
                f"the actual document: {complaint}.\n"
                f"Propose different terms. Prefer identifiers, type or field "
                f"names, and distinctive quoted phrases over package or section "
                f"names, which recur throughout the document.")
            try:
                reply = client.ask(SYSTEM, retry_context, validate=validate,
                                   label=f"{rid}-retry")
                stats["proposed"] += len(reply["terms"])
                stats["retried"] += 1
                kept, rejected = sift(reply["terms"], reply["absence"])
                if kept:
                    stats["rescued"] += 1
            except LLMError:
                pass

        # closure.py judges the terms COLLECTIVELY — it counts the lines matching
        # any of them. Terms that each pass the threshold alone can blow through
        # it together, which is how a row survives generation and then arrives as
        # TOO BROAD in the very tool it was generated for. Drop the widest term
        # until the union fits, so what is written here is what closure can use.
        while len(kept) > 1:
            union = len(closure.hits(lines, closure.expand(kept, lexicon)))
            if union <= args.too_broad:
                break
            widest = max(kept, key=lambda t: len(
                closure.hits(lines, closure.expand([t], lexicon))))
            kept.remove(widest)
            rejected.append((widest, "union of terms too broad"))
            stats["widened"] += 1

        for term, why in rejected:
            stats["broad" if "too" in why else "absent"] += 1
        stats["kept"] += len(kept)

        if not kept:
            empty.append(rid)
        entries.append({
            "id": rid, "class": reply["class"], "title": reply["title"].strip(),
            "absence": reply["absence"], "terms": kept,
            "theme": row.get(args.theme_col, "").strip(),
            "section": row.get(args.section_col, "").strip(),
        })
        if index % 10 == 0 or index == len(body):
            print(f"  {index}/{len(body)}  kept {stats['kept']} of "
                  f"{stats['proposed']} candidate terms")

    out_path = os.path.join(project, args.out)
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"""# Register map generated from {os.path.basename(args.xlsx)}
# sheet: {args.sheet}   validated against: {args.doc}
#
# Generated by matrix.py — regenerate rather than hand-edit, EXCEPT to fix terms.
# Terms are the one thing worth correcting by hand: the model proposed them from
# each comment's wording and they were then counted against the frozen document,
# but only a reader knows whether a surviving term is about the right passage.
#
# `terms: []` means nothing the model proposed survived validation. Those rows
# cannot be tracked across revisions until someone supplies a term — closure.py
# will report them, it will not silently skip them.

findings:
""")
        for entry in entries:
            terms = ", ".join(f'"{yaml_escape(t)}"' for t in entry["terms"])
            absence = ", absence: true" if entry["absence"] else ""
            out.write(f'  - {{id: {entry["id"]}, class: {entry["class"]}'
                      f'{absence}, title: "{yaml_escape(entry["title"])}", '
                      f'terms: [{terms}]}}')
            if entry["section"]:
                out.write(f'  # {entry["section"][:60]}')
            out.write("\n")

    print(f"\nwrote {out_path}")
    print(f"  {len(entries)} findings of {len(body)} rows, "
          f"{stats['kept']} terms kept of {stats['proposed']} proposed "
          f"({stats['absent']} matched nothing, {stats['broad']} too broad)")

    # Two different failures, deliberately not merged. A row the model answered
    # for but whose every candidate term failed validation needs a human to pick
    # a better string. A row whose call never returned needs the run repeating —
    # the cache means a re-run only pays for these.
    if empty:
        print(f"\n  {len(empty)} row(s) answered but left with NO usable term "
              f"— pick a string by hand:\n  " + " ".join(empty))
    if failed:
        print(f"\n  {len(failed)} row(s) the endpoint never answered — re-run "
              f"to retry just these:\n  " + " ".join(failed))
    if blank:
        print(f"\n  {len(blank)} row(s) are numbered but have no comment text "
              f"yet — carried with no\n  terms so they stay visible; re-run "
              f"once the client fills them in:\n  " + " ".join(blank))
    print(f"\n  model calls: {client.stats}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
