#!/usr/bin/env python3
"""Extract atomic obligations from a requirements document.

Two passes, in this order for a reason:

  1. DETERMINISTIC candidate detection — find every sentence carrying an RFC 2119
     modal (SHALL / MUST / SHOULD / MAY / SHALL NOT ...). Cheap, exhaustive, and
     it establishes the denominator: you know how many candidates exist before
     any model sees them, so you can tell later whether the model dropped any.

  2. MODEL normalisation — one bounded call per candidate, splitting compound
     sentences into single obligations and typing the modality. Each call is
     independent and small; nothing accumulates.

Pass 1 alone is useful, and `--no-model` stops there. That is the baseline the
model pass has to beat.

    ./obligations.py --project . --doc requirements
    ./obligations.py --project . --doc requirements --no-model
    ./obligations.py --project . --doc requirements --model qwen3.6-35b-a3b

Writes obligations.yaml in the project directory. Every obligation carries the
locator it came from, so a coverage matrix can always be traced back to the line
of the requirements document that imposed it.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Client, LLMError, load_doc            # noqa: E402

PROMPT_VERSION = "obligations-1"

# RFC 2119 keywords. NOT-forms first so they win the match.
MODALS = [
    (r"\bSHALL\s+NOT\b", "SHALL NOT"), (r"\bMUST\s+NOT\b", "MUST NOT"),
    (r"\bSHOULD\s+NOT\b", "SHOULD NOT"), (r"\bMAY\s+NOT\b", "MAY NOT"),
    (r"\bSHALL\b", "SHALL"), (r"\bMUST\b", "MUST"),
    (r"\bSHOULD\b", "SHOULD"), (r"\bMAY\b", "MAY"),
    (r"\bREQUIRED\b", "SHALL"), (r"\bIS\s+REQUIRED\s+TO\b", "SHALL"),
]

# A pre-numbered obligation, e.g. "**R-001.**" or "R-001." or "3.2.1"
NUMBERED = re.compile(r"^\**([A-Z]{1,3}-\d{1,4}|\d+\.\d+(?:\.\d+)?)\.?\**\s")

# Tender documents rarely put a modal in every obligation. The dominant form is
# a STEM carrying the modal — "The Architecture shall include:" — followed by an
# enumerated list, where each item is an obligation inheriting that modality.
# Treating only modal-bearing sentences as candidates finds the five stems and
# misses the eighty actual requirements underneath them.
STEM = re.compile(r"\b(SHALL|MUST|SHOULD|MAY)\b[^.:]*:\s*$", re.I)
LIST_ITEM = re.compile(r"^\(?([a-z]{1,2}|[ivx]{1,4}|\d{1,2})[.)]\s+(\S.*)$", re.I)

SYSTEM = """You normalise requirement sentences into atomic obligations.

An atomic obligation states ONE thing one party must do. A sentence containing
two duties becomes two obligations. Do not invent obligations, do not soften or
strengthen wording, and do not merge separate duties.

Reply with JSON only:
{"obligations":[{"text":"...","modality":"SHALL|SHALL NOT|SHOULD|MAY","subject":"who must act"}]}

Rules:
- "text" quotes or minimally rewrites the source. Never add requirements.
- "modality" is the RFC 2119 keyword actually used in the sentence.
- "subject" is the party bound, as named in the text ("the Contractor",
  "the deliverable", "the architecture"). Use "unspecified" if absent.
- A sentence that states a fact, definition or context rather than a duty
  returns {"obligations":[]}. Background prose is not an obligation."""


def validate(obj):
    if not isinstance(obj, dict) or "obligations" not in obj:
        return "top level must be an object with an 'obligations' array"
    items = obj["obligations"]
    if not isinstance(items, list):
        return "'obligations' must be an array"
    allowed = {"SHALL", "SHALL NOT", "MUST", "MUST NOT", "SHOULD",
               "SHOULD NOT", "MAY", "MAY NOT"}
    for item in items:
        if not isinstance(item, dict):
            return "each obligation must be an object"
        if not item.get("text"):
            return "each obligation needs a non-empty 'text'"
        if item.get("modality") not in allowed:
            return f"'modality' must be one of {sorted(allowed)}"
    return None



CHUNK_SYSTEM = """You extract obligations from a requirements document.

You will be given one section. List EVERY distinct obligation it imposes.

Requirements documents rarely put a modal verb in each obligation. The dominant
form is a stem carrying the modal — "The Architecture shall include:" — followed
by an enumerated list, where each item and sub-item is its own obligation. Expand
those: emit one obligation per list item, restating the stem so each stands alone.

Reply with JSON only:
{"obligations":[{"text":"...","modality":"SHALL|SHOULD|MAY","label":"a.i or 3.2 or ''","scope":"one of the scopes listed by the user"}]}

Rules:
- One obligation per duty. Do not merge list items, do not summarise a list.
- "text" must stand alone without the surrounding document.
- "modality" is inherited from the governing stem when the item has no modal.
- "label" is the item's own numbering if it has one, else "".
- "scope" names WHICH DELIVERABLE discharges this obligation, chosen from the
  list the user gives. One requirements document usually governs several
  deliverables; an obligation on the roadmap is not a defect in the architecture.
  Use "process" for engagement obligations (meetings, schedules, submissions)
  that no technical deliverable discharges.
- Headings, definitions, background and commercial boilerplate are not
  obligations. Return {"obligations":[]} for a section that imposes none."""


def validate_chunk(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get("obligations"), list):
        return "top level must be an object with an 'obligations' array"
    for item in obj["obligations"]:
        if not isinstance(item, dict) or not item.get("text"):
            return "each obligation needs a non-empty 'text'"
        if not item.get("scope"):
            return "each obligation needs a 'scope'"
        if item.get("modality", "SHALL").upper() not in (
                "SHALL", "SHALL NOT", "MUST", "MUST NOT", "SHOULD",
                "SHOULD NOT", "MAY", "MAY NOT"):
            return "'modality' must be an RFC 2119 keyword"
    return None


def make_chunks(lines, size=45):
    """Windows that break on blank lines and carry their heading."""
    chunks, current, heading, start = [], [], "(front matter)", 1
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        bare = stripped.lstrip("#").strip()
        # A markdown "#" prefix IS a heading. Requiring a leading number as
        # well made every unnumbered heading invisible — and a fixture whose
        # headings all happen to be numbered will never show you that.
        is_heading = stripped.startswith("#") or (
            0 < len(bare) < 90 and
            re.match(r"^(\d+(\.\d+)*\s+\S|Annex\s|Appendix\s)", bare))
        if is_heading:
            if current:
                chunks.append({"heading": heading, "start": start,
                               "text": "\n".join(current)})
            current, heading, start = [], bare, number
            continue
        current.append(line)
        if len(current) >= size and not stripped:
            chunks.append({"heading": heading, "start": start,
                           "text": "\n".join(current)})
            current, start = [], number + 1
    if current:
        chunks.append({"heading": heading, "start": start,
                       "text": "\n".join(current)})
    return [c for c in chunks if c["text"].strip()]

def split_sentences(block):
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z(])", block)
    return [p.strip() for p in parts if p.strip()]


def find_candidates(lines):
    """Deterministic pass. Returns candidates with section and line locators.

    Works on PARAGRAPHS, not lines. Documents wrap at ~80 characters, so a
    line-by-line scan hands the model fragments like "Every interface SHALL
    carry a unique identifier and appear in a" — and a well-behaved model
    correctly refuses to guess the rest, so the obligation is silently lost.
    Blank lines, headings and table rows end a paragraph; nothing else does.
    """
    section, candidates = "(front matter)", []
    para, para_start = [], 0

    def flush():
        nonlocal para, para_start
        if not para:
            return
        block = " ".join(p.strip() for p in para)
        match = NUMBERED.match(block)
        ref = match.group(1) if match else None
        # Map a sentence back to the line it starts on, so locators stay precise
        # even though the modal was found in the joined paragraph.
        offsets, running = [], 0
        for offset_line in para:
            offsets.append(running)
            running += len(offset_line.strip()) + 1
        for sentence in split_sentences(block):
            found = next((label for pattern, label in MODALS
                          if re.search(pattern, sentence)), None)
            if not found:
                continue
            position = block.find(sentence)
            line_offset = sum(1 for o in offsets if o <= position) - 1
            candidates.append({"ref": ref, "section": section,
                               "line": para_start + max(line_offset, 0),
                               "modality_hint": found, "raw": sentence})
        para, para_start = [], 0

    stem, stem_line = None, 0
    item, item_start = [], 0

    def flush_item():
        nonlocal item, item_start
        if item and stem:
            body = " ".join(t.strip() for t in item)
            candidates.append({
                "ref": None, "section": section, "line": item_start,
                "modality_hint": stem_modality,
                "raw": f"{stem} {body}",
                "stem": stem, "stem_line": stem_line})
        item, item_start = [], 0

    stem_modality = "SHALL"
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        bare = stripped.lstrip("#").strip()
        is_heading = stripped.startswith("#") or (
            0 < len(bare) < 80 and re.match(r"^\d+(\.\d+)*\s+\S", bare))

        if STEM.search(stripped) and len(stripped) < 200:
            flush(); flush_item()
            stem, stem_line = stripped.rstrip(), number
            found = next((label for pattern, label in MODALS
                          if re.search(pattern, stripped, re.I)), "SHALL")
            stem_modality = found
            continue

        if stem:
            match = LIST_ITEM.match(stripped)
            if match:
                flush_item()
                item, item_start = [match.group(2)], number
                continue
            if not stripped or is_heading:
                flush_item()
                if is_heading:
                    section, stem = bare, None
                continue
            if item:
                item.append(stripped)
                continue
            stem = None                       # prose resumed; stem is spent

        if not stripped or is_heading or stripped.startswith("|"):
            flush()
            if is_heading:
                section = bare
            continue
        if not para:
            para_start = number
        para.append(line)

    flush(); flush_item()
    return candidates


def emit_yaml(path, doc, obligations, mode):
    with open(path, "w", encoding="utf-8") as out:
        out.write("# Obligations extracted from the requirements document.\n"
                  "# Generated by obligations.py — regenerate rather than edit,\n"
                  "# except to correct an extraction error (then note it below).\n"
                  f"#\n# source : {doc['slug']} ({doc['path']})\n"
                  f"# sha256 : {doc['source_sha256'][:16]}\n"
                  f"# mode   : {mode}\n"
                  f"# count  : {len(obligations)}\n\n")
        out.write("obligations:\n")
        for item in obligations:
            out.write(f"  - id: {item['id']}\n")
            out.write(f"    modality: {item['modality']}\n")
            out.write(f"    subject: {json.dumps(item.get('subject','unspecified'))}\n")
            out.write(f"    text: {json.dumps(item['text'])}\n")
            out.write(f"    source_ref: {json.dumps(item.get('ref') or '')}\n")
            out.write(f"    scope: {json.dumps(item.get('scope','unscoped'))}\n")
            out.write(f"    section: {json.dumps(item['section'])}\n")
            out.write(f"    locator: {doc['slug']}:{item['line']}\n\n")



def run_chunk_mode(args, project, doc, lines):
    chunks = make_chunks(lines)
    if args.limit:
        chunks = chunks[:args.limit]
    print(f"chunk mode: {len(chunks)} sections")
    client = Client(project, model=args.model, url=args.url,
                    prompt_version="obligations-chunk-1")
    obligations, counter = [], 0
    for index, item in enumerate(chunks, 1):
        user = (f"SCOPES (choose one per obligation): {args.scopes}\n\n"
                f"SECTION: {item['heading']}\n\n{item['text']}\n\n"
                "List every obligation this section imposes.")
        try:
            reply = client.ask(CHUNK_SYSTEM, user, validate=validate_chunk,
                               label=f"chunk:{item['start']}")
        except LLMError as exc:
            print(f"  ! {exc}", file=sys.stderr)
            continue
        for entry in reply["obligations"]:
            counter += 1
            obligations.append({
                "id": f"O-{counter:03d}",
                "modality": entry.get("modality", "SHALL").upper(),
                "subject": "unspecified", "text": entry["text"],
                "ref": entry.get("label") or None,
                "scope": entry.get("scope", "unscoped"),
                "section": item["heading"], "line": item["start"]})
        print(f"  {index}/{len(chunks)} {item['heading'][:44]:46} "
              f"-> {len(obligations)} obligations")
    print(f"\n{client.summary()}")
    out_path = os.path.join(project, args.out)
    emit_yaml(out_path, doc, obligations, f"chunk / {args.model}")
    print(f"\nwrote {os.path.relpath(out_path, project)}: "
          f"{len(obligations)} obligations from {len(chunks)} sections")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True, help="slug of the requirements document")
    parser.add_argument("--model", default="qwen3.6-35b-a3b")
    parser.add_argument("--url", default="http://localhost:8085/v1/chat/completions")
    parser.add_argument("--no-model", action="store_true",
                        help="deterministic pass only — the baseline")
    parser.add_argument("--out", default="obligations.yaml")
    parser.add_argument("--limit", type=int, default=0, help="cap candidates (testing)")
    parser.add_argument("--scopes", default="architecture,process",
                        help="comma-separated deliverables this requirements "
                             "document governs; chunk mode tags each obligation")
    parser.add_argument("--mode", choices=("sentence", "chunk"), default="sentence",
                        help="sentence: one call per modal sentence (precise, needs "
                             "tidy layout). chunk: one call per section (robust to "
                             "nested lists and multi-line stems)")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    try:
        doc, lines = load_doc(project, args.doc)
    except LLMError as exc:
        sys.exit(str(exc))
    if doc["role"] not in ("requirements", "reference"):
        print(f"note: {args.doc} has role '{doc['role']}', not 'requirements'",
              file=sys.stderr)

    if args.mode == "chunk":
        return run_chunk_mode(args, project, doc, lines)

    candidates = find_candidates(lines)
    if args.limit:
        candidates = candidates[:args.limit]
    print(f"deterministic pass: {len(candidates)} candidate sentences "
          f"carrying an RFC 2119 modal")
    if not candidates:
        sys.exit("no modal verbs found — is this a requirements document?")

    obligations, counter = [], 0
    if args.no_model:
        for cand in candidates:
            counter += 1
            obligations.append({"id": f"O-{counter:03d}",
                                "modality": cand["modality_hint"],
                                "subject": "unspecified", "text": cand["raw"],
                                "ref": cand["ref"], "section": cand["section"],
                                "line": cand["line"]})
        mode = "deterministic only"
    else:
        client = Client(project, model=args.model, url=args.url,
                        prompt_version=PROMPT_VERSION)
        dropped = 0
        for index, cand in enumerate(candidates, 1):
            user = (f"Section: {cand['section']}\n"
                    f"Sentence:\n{cand['raw']}\n\n"
                    "Return the atomic obligations this sentence imposes.")
            try:
                reply = client.ask(SYSTEM, user, validate=validate,
                                   label=f"obl:{cand['ref'] or cand['line']}")
            except LLMError as exc:
                print(f"  ! {exc}", file=sys.stderr)
                dropped += 1
                continue
            items = reply["obligations"]
            if not items:
                dropped += 1
            for item in items:
                counter += 1
                obligations.append({"id": f"O-{counter:03d}",
                                    "modality": item["modality"],
                                    "subject": item.get("subject", "unspecified"),
                                    "text": item["text"], "ref": cand["ref"],
                                    "section": cand["section"], "line": cand["line"]})
            if index % 10 == 0 or index == len(candidates):
                print(f"  {index}/{len(candidates)} candidates → "
                      f"{len(obligations)} obligations")
        mode = f"model {args.model}"
        print(f"\n{client.summary()}")
        print(f"{dropped} candidate(s) yielded no obligation "
              f"(context or definition rather than a duty)")

    out_path = os.path.join(project, args.out)
    emit_yaml(out_path, doc, obligations, mode)
    print(f"\nwrote {os.path.relpath(out_path, project)}: "
          f"{len(obligations)} obligations from {len(candidates)} candidates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
