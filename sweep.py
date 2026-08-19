#!/usr/bin/env python3
"""Deterministic discovery sweeps over a frozen corpus. No model involved.

Two classes, both of which found real defects in a live review by hand and
neither of which needs inference:

  xref   D2 — dangling obligations. Identifiers referenced but never elaborated,
              and pointers ("Appendix E", "Section 15") that resolve nowhere.
  vocab  D9 — vocabulary gaps. Purpose-bearing terms from the requirements side
              that are absent, or nearly absent, from the deliverable.

    ./sweep.py xref  --project ~/reviews/some-engagement --doc arch-v5
    ./sweep.py vocab --project ~/reviews/some-engagement --doc arch-v5
    ./sweep.py xref  --project . --doc arch-v6 --compare arch-v5

Both read parsed/MANIFEST.json and refuse to run on an unfrozen corpus, because
a locator into text that can still move is not a locator.

IMPORTANT: a zero count here is a LEAD, not a finding. Absence claims require a
synonym sweep and a read of the surrounding passage — see the absence() contract
in the dossier design. This tool deliberately reports counts and line numbers
only; it never says "absent".
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

# The optional hyphen before the digits is not cosmetic. One real architecture
# numbers most gap classes as ATT-G01 / ORC-G07 but numbers one class as
# CM-G-01, and a pattern requiring digits immediately after G silently misses
# all thirteen of them. Identifier grammar is rarely as consistent as a document
# believes it is, so match both and let the counts expose the inconsistency.
DEFAULT_ID_PATTERNS = [
    r"\b[A-Z]{2,4}-G-?\d{1,3}\b",      # BM-G02, ATT-G12, ORC-G07, CM-G-01
    r"\bIF-[A-Z]{2,5}-\d{1,3}\b",      # IF-NAR-004, IF-ORC-019
    r"\bND-\d{1,3}\b",                 # not-defined register
    r"\b(?:CF|PG)-\d{1,3}\b",
]


def load(project, slug):
    manifest_path = os.path.join(project, "parsed", "MANIFEST.json")
    if not os.path.exists(manifest_path):
        sys.exit("corpus not frozen — run freeze.py first")
    manifest = json.load(open(manifest_path))
    for doc in manifest["documents"]:
        if doc["slug"] == slug:
            path = os.path.join(project, doc["parsed"])
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            return doc, lines
    sys.exit(f"no document {slug!r}; have: "
             + ", ".join(d["slug"] for d in manifest["documents"]))


def header(slug, doc, lines):
    """One provenance line, identical whichever sweep printed it.

    Two of the four sweeps printed source_sha256 (the .docx) and two printed
    text_sha256 (the extracted text they actually read), so the same document
    announced two different hashes depending on which sweep you ran. On a tool
    whose whole claim is a hash-pinned corpus and locators that do not move,
    that reads as two different documents. Show the hash of the text the
    findings point into, and name the source hash as the source.
    """
    return (f"== {slug} ({len(lines)} lines, text {doc.get('text_sha256','')[:12]}"
            f", source {doc.get('source_sha256','')[:12]}) ==")


def cmd_xref(args, project):
    doc, lines = load(project, args.doc)
    patterns = [re.compile(p) for p in DEFAULT_ID_PATTERNS]

    hits = defaultdict(list)                       # id -> [line numbers]
    for number, line in enumerate(lines, 1):
        for pattern in patterns:
            for match in pattern.findall(line):
                hits[match].append(number)

    by_prefix = defaultdict(list)
    for identifier in hits:
        by_prefix[re.match(r"[A-Z]+(?:-[A-Z]+)?", identifier).group(0)].append(identifier)

    print(header(args.doc, doc, lines))
    print(f"\n-- identifier classes --")
    for prefix in sorted(by_prefix):
        ids = by_prefix[prefix]
        singles = [i for i in ids if len(hits[i]) == 1]
        print(f"  {prefix:8} {len(ids):4} distinct  {sum(len(hits[i]) for i in ids):5} "
              f"occurrences  {len(singles):4} appear once only")

    singletons = sorted(i for i in hits if len(hits[i]) == 1)
    if singletons:
        print(f"\n-- referenced exactly once ({len(singletons)}) --")
        print("   Mentioned but never elaborated. Each is a candidate dangling")
        print("   obligation; confirm by reading the line before raising anything.")
        for identifier in singletons[:args.limit]:
            line_no = hits[identifier][0]
            print(f"  {identifier:14} {args.doc}:{line_no}  "
                  f"{lines[line_no - 1].strip()[:88]}")
        if len(singletons) > args.limit:
            print(f"  … {len(singletons) - args.limit} more (raise --limit)")

    # Pointers to named structural targets that may not exist.
    # Heading forms differ by source. Extracted .docx gives bare "Appendix C" or
    # "Section 9"; markdown gives "## Appendix C — ..." or "## 9. Deployment
    # topology". Missing a form is worse than it sounds: every real target then
    # reads as unresolved and the check drowns in false positives.
    headings = set()
    for line in lines:
        candidate = line.lstrip("#").strip()
        if not line.startswith("#") and len(candidate) > 80:
            continue                                   # body prose, not a heading
        match = re.match(r"(Appendix\s+[A-Z])\b", candidate)
        if match:
            headings.add(match.group(1))
        match = re.match(r"(?:Section\s+)?(\d{1,2})(?:\.\d+)*[.\s]", candidate)
        if match:
            headings.add("Section " + match.group(1))

    referenced = Counter()
    where = {}
    for number, line in enumerate(lines, 1):
        for match in re.finditer(r"\b(Appendix\s+[A-Z]|Section\s+\d{1,2})\b", line):
            target = match.group(1).strip()
            referenced[target] += 1
            where.setdefault(target, number)

    unresolved = [t for t in referenced if t not in headings]
    print(f"\n-- structural pointers --")
    print(f"  {len(referenced)} distinct targets referenced, "
          f"{len(headings)} present as headings")
    if unresolved:
        print(f"\n-- referenced but no matching heading ({len(unresolved)}) --")
        for target in sorted(unresolved):
            line_no = where[target]
            print(f"  {target:14} x{referenced[target]:<3} first at {args.doc}:{line_no}"
                  f"  {lines[line_no - 1].strip()[:70]}")

    if args.compare:
        other_doc, other_lines = load(project, args.compare)
        other_hits = set()
        for line in other_lines:
            for pattern in patterns:
                other_hits.update(pattern.findall(line))
        lost = sorted(other_hits - set(hits))
        print(f"\n-- present in {args.compare}, absent from {args.doc} "
              f"({len(lost)}) --")
        counts = Counter(re.match(r"[A-Z]+(?:-[A-Z]+)?", i).group(0) for i in lost)
        for prefix, count in sorted(counts.items()):
            print(f"  {prefix:8} {count:4} identifiers no longer present")
        if not lost:
            print("  (none — this document family may not use identifiers)")

        # Headings, not just identifiers. A document that carries no ID register
        # can still lose a whole section between revisions, and comparing
        # identifier classes will report nothing at all. Net line count is no
        # help either: most revisions GROW while quietly dropping something.
        def headings_of(source):
            """Headings in either document family.

            Markdown gives "## Summary". Extracted .docx gives a bare short line
            like "12.4 Orchestration" or "Appendix C". Detecting only one form
            silently reports no structural change at all for the other — and
            extract.py's own "##########" banner must not count as a heading.
            """
            found = []
            for line in source:
                stripped = line.strip()
                if stripped.startswith("##########"):
                    continue
                if stripped.startswith("#"):
                    found.append(re.sub(r"\s+", " ", stripped.lstrip("#").strip()))
                    continue
                if 0 < len(stripped) < 80 and re.match(
                        r"^(\d+(\.\d+)*[.\s]\s*\S|Appendix\s+[A-Z]\b|Annex\s+[A-Z0-9]\b)",
                        stripped):
                    # A table of contents repeats every heading with the page
                    # number welded on ("...summary11"). Left in, it doubles the
                    # heading set and buries a real deletion in churn.
                    if re.search(r"[^\s\d]\d{1,4}$", stripped):
                        continue
                    found.append(re.sub(r"\s+", " ", stripped))
            # Same heading text can legitimately recur; compare as a multiset of
            # distinct strings, not of occurrences.
            return list(dict.fromkeys(found))

        old_h, new_h = headings_of(other_lines), headings_of(lines)
        dropped = [h for h in old_h if h not in set(new_h)]
        added = [h for h in new_h if h not in set(old_h)]
        print(f"\n-- headings: {len(old_h)} -> {len(new_h)} "
              f"({len(dropped)} removed, {len(added)} added) --")
        if dropped:
            print(f"\n-- sections present in {args.compare}, GONE from "
                  f"{args.doc} ({len(dropped)}) --")
            for heading in dropped[:args.limit or len(dropped)]:
                print(f"  {heading[:96]}")
    return 0


def cmd_vocab(args, project):
    doc, lines = load(project, args.doc)
    terms_path = args.terms or os.path.join(project, "purpose-terms.txt")
    if not os.path.exists(terms_path):
        sys.exit(f"no terms file at {terms_path}")

    text_lower = "\n".join(lines).lower()
    print(header(args.doc, doc, lines))
    print(f"   terms: {os.path.relpath(terms_path, project)}\n")

    rows = []
    for raw in open(terms_path, encoding="utf-8"):
        term = raw.split("#", 1)[0].strip()
        if not term:
            continue
        count = text_lower.count(term.lower())
        first = None
        if count:
            for number, line in enumerate(lines, 1):
                if term.lower() in line.lower():
                    first = number
                    break
        rows.append((count, term, first))

    rows.sort(key=lambda r: (r[0], r[1].lower()))
    width = max(len(r[1]) for r in rows) if rows else 10
    for count, term, first in rows:
        marker = "  <-- zero" if count == 0 else ""
        where = f"{args.doc}:{first}" if first else "-"
        print(f"  {count:5}  {term:{width}}  {where}{marker}")

    zeros = [r for r in rows if r[0] == 0]
    print(f"\n  {len(zeros)} of {len(rows)} terms absent.")
    print("  A zero is a LEAD, not a finding: sweep synonyms and read the passage")
    print("  before it goes anywhere near the register.")
    return 0


# D4 — a quantity used but never pinned down. The fixture defines the bar
# exactly, and the decoy is what makes it worth having:
#
#   GT-D4-001  stability factor k — no value, no owner, no register entry.
#              A SILENT omission, which RFO Annex A calls the more serious kind.
#   DC-005     threshold theta_surge — no value EITHER, but it carries ND-02 and
#              AS-G01 with a named owner. A disclosed gap is not a defect.
#
# So all three must be absent. Disclosure in any form clears it, which is why
# this reads the register identifiers rather than only the prose.
QUANTITY = re.compile(
    r"\b(?:factor|threshold|coefficient|parameter|constant|weight|rate|"
    r"budget|horizon|window|denominator|exponent)\s+"
    r"([A-Za-z\u03b1-\u03c9][A-Za-z0-9_\u03b1-\u03c9]{0,14})\b")
SYMBOLIC = re.compile(r"\b([\u03b1-\u03c9][A-Za-z0-9_]{0,12})\b")
# Underscore notation is how technical documents actually write symbols once
# the greek is stripped by extraction. arch-v5 carries exactly one greek
# character in 690k and thirty-one uses of p_like; the fixture's theta_surge is
# the same shape with a greek head. Without this the sweep sees nothing on the
# document it was built for.
UNDERSCORE = re.compile(r"\b([a-zA-Z]{1,4}_[a-zA-Z][a-zA-Z0-9_]{0,14})\b")
# A quantity is used in COMPUTATION; a field name is merely listed. Both are
# snake_case, and a technical document is full of the second: edge_state,
# edge_weight_request and ann_retrieval_score are schema fields, while beta_l in
# "sigmoid(beta_l . x(i,j,t))" is an unvalued coefficient. Requiring a
# mathematical context is what separates them.
MATHY = re.compile(r"[=*^]|\u00b7|\bsum\b|\bsigmoid\b|\bsqrt\b|\blog\b|"
                   r"\bmin\b|\bmax\b|\bexp\b|lVert|\([ijklmnt],", re.I)
ASSIGNED = re.compile(r"=\s*[-+]?\d|\bset to\b|\bdefaults? to\b|"
                      r"\bis\s+[-+]?\d|\bvalue of\b|\bequals\b", re.I)
DISCLOSED = re.compile(r"\b(?:ND-\d{1,3}|[A-Z]{2,5}-G-?\d{1,3}|"
                       r"[A-Z]{2,4}-\d{1,3})\b")
OWNED = re.compile(r"\bowner\b|\blead\b|\bowned by\b|\bresponsible\b", re.I)
NOT_NOTATION = {"in", "is", "of", "to", "at", "on", "or", "an", "a", "it", "be",
                "by", "as", "we", "no", "so", "if", "up", "the", "and", "for"}


def cmd_quantity(args, project):
    doc, lines = load(project, args.doc)
    window = max(1, args.window)

    seen = {}
    for number, line in enumerate(lines, 1):
        matches = list(QUANTITY.finditer(line)) + list(SYMBOLIC.finditer(line))
        if MATHY.search(line):
            matches += list(UNDERSCORE.finditer(line))
        for match in matches:
            name = match.group(1)
            # A quantity is a SYMBOL, not the next English word. "precipitation
            # rate above drainage capacity" matched "above"; "applies rate
            # limiting" matched "limiting". Require it to look like notation:
            # one or two characters, or carrying an underscore, digit or greek
            # letter. That keeps k, theta_surge, beta_l, p_add and drops prose.
            # Short English words are not notation. "rate in excess of" and
            # "window is torn down" both match the noun cue and neither names a
            # quantity.
            if name.lower() in NOT_NOTATION:
                continue
            if not (len(name) <= 2
                    or "_" in name
                    or any(c.isdigit() for c in name)
                    or any("\u03b1" <= c <= "\u03c9" for c in name)):
                continue
            seen.setdefault(name, []).append(number)

    print(header(args.doc, doc, lines) + "\n")
    print(f"-- quantities named in the text: {len(seen)} --")

    findings = []
    for name, where in sorted(seen.items()):
        # Look at every line the quantity appears on, plus a window either
        # side: a value or an owner stated one line down still counts as
        # stated. DC-003 is the fixture's warning about exactly this — an
        # enumeration on the next line is not an absence.
        context = []
        for line_no in where:
            lo, hi = max(0, line_no - 1 - window), min(len(lines), line_no + window)
            context.extend(lines[lo:hi])
        blob = "\n".join(context)
        if ASSIGNED.search(blob) or DISCLOSED.search(blob) or OWNED.search(blob):
            continue
        findings.append((name, where))

    print(f"-- neither valued, owned, nor in a register: {len(findings)} --")
    print("   Each is a candidate D4: a quantity the document uses and never")
    print("   pins down. Disclosure in any form clears it, so what remains is")
    print("   the silent kind. Confirm by reading the line before raising.\n")
    for name, where in findings[:args.limit]:
        first = where[0]
        print(f"  {name:<18} x{len(where):<3} {args.doc}:{first}  "
              f"{lines[first - 1].strip()[:78]}")
    if len(findings) > args.limit:
        print(f"  … {len(findings) - args.limit} more (raise --limit)")
    return 0


# SUPERSEDED BY undefined.py. Kept because the negative result is worth having,
# not because this should be run: frequency over n-grams gave 3,370 candidates
# on a real architecture, three tightenings reached 593, and it can never see
# "REDACTED-08" — 77 uses, no definition — because one word is not an n-gram.
# The model-screened version reaches 18 of 26 register findings. Use that.
#
# Undefined term — the largest class hiding inside "framing".
#
# 15 of the 26 framing findings on a live review are one shape: a term the
# document leans on and never defines. "REDACTED-08" appears 73 times with no
# definition, "REDACTED-10" 19 times, "REDACTED-09" 5. None of
# those need knowledge from outside the document, which is what "framing" was
# supposed to mean — they were filed there because nothing detected them.
#
# Deliberately frequency-first. A term used once and left undefined is a
# reviewer's judgement call; a term used seventy-three times and never defined
# is a defect whatever the reader's background.
DEFINITION = (
    r"(?:\bis\b|\bare\b|\bmeans\b|\brefers to\b|\bdenotes\b|"
    r"\bis defined as\b|\bis the\b|\bdefinition\b|:)")
GLOSSARY_HEADING = re.compile(r"glossary|definitions|terminology|nomenclature",
                              re.I)
COMMON = set("""does do did has have had was were being not no nor but so yes
the a an and or of to in for on with by is are be been shall
must should may not that this these those it its as at from any all each every
per which where when who whom whose if then than such other same both either
than into over under more most also can will would could we you they there here
one two three first second next last such via using used use within across
between during before after while about above below only just more less many few
data model system platform component service module layer document section
architecture interface state event time run set list type name value case
number level order group part item work team plan phase step mode view""".split())


def cmd_term(args, project):
    print("NOTE: superseded by undefined.py, which reaches 18/26 of the "
          "register's\n      framing findings where this reaches few. "
          "Kept for the record.\n")
    doc, lines = load(project, args.doc)
    text = "\n".join(lines)
    lowered = text.lower()

    # Any 2-3 word phrase the document uses repeatedly. No part-of-speech
    # tagging, so the COMMON list carries the weight: a phrase made only of
    # ordinary words is not jargon and is not what a reviewer asks about.
    words = re.findall(r"[A-Za-z][A-Za-z0-9'\-]*", text)
    counts = {}
    for size in (2, 3):
        for i in range(len(words) - size + 1):
            gram = words[i:i + size]
            # Reject if ANY word is ordinary, not only if all of them are.
            # "and replay", "does not", "ownership and" all survived the weaker
            # test and swamped the output at 3,370 candidates. A technical term
            # contains no conjunctions and no auxiliaries.
            if any(w.lower() in COMMON for w in gram):
                continue
            if any(len(w) < 3 for w in gram):
                continue
            phrase = " ".join(gram).lower()
            counts[phrase] = counts.get(phrase, 0) + 1

    # An architecture document defines its components STRUCTURALLY — "Narrative
    # Lifecycle" is defined by section 10 existing, not by a sentence saying
    # "Narrative Lifecycle is ...". Without this the output is dominated by the
    # document's own component names, which are the best-defined things in it.
    headings = " ".join(l.lower() for l in lines
                        if l.strip().startswith("#")
                        or re.match(r"^\s*(\d+(\.\d+)*[.\s]|Appendix|Annex)", l))
    known = set()
    comp_path = os.path.join(project, "components.yaml")
    if os.path.exists(comp_path):
        raw = open(comp_path, encoding="utf-8").read().lower()
        known = {m.group(1).strip() for m in
                 re.finditer(r"[-\s](?:name|alias(?:es)?):\s*\[?\s*\"?([^\"\n\]]+)",
                             raw)}

    glossary = any(GLOSSARY_HEADING.search(l) for l in lines)
    candidates = []
    for phrase, n in counts.items():
        if n < args.min_uses:
            continue
        # Defined anywhere? "X is …", "X means …", or "X:" as a definition list
        # entry. One definition clears the term however many times it is used.
        if re.search(re.escape(phrase) + r"\s*" + DEFINITION, lowered):
            continue
        if phrase in headings:
            continue                      # defined by having its own section
        if any(phrase in k or k in phrase for k in known if len(k) > 4):
            continue                      # a named component, not loose jargon
        candidates.append((n, phrase))

    candidates.sort(reverse=True)
    print(header(args.doc, doc, lines) + "\n")
    print(f"-- repeated phrases: {len(counts)}, "
          f"used >={args.min_uses} times: "
          f"{sum(1 for _, n in counts.items() if n >= args.min_uses)} --")
    print(f"-- of those, NO definition anywhere in the document: "
          f"{len(candidates)} --")
    print("   A term the document leans on and never defines. Frequency is the"
          "\n   signal: used once is a judgement call, used seventy times is a"
          "\n   defect. Glossary present in this document: "
          f"{'yes' if glossary else 'NO'}.\n")
    for n, phrase in candidates[:args.limit]:
        where = next((i for i, l in enumerate(lines, 1)
                      if phrase in l.lower()), 0)
        print(f"  {n:>4}x  {phrase[:44]:<46} {args.doc}:{where}")
    if len(candidates) > args.limit:
        print(f"  … {len(candidates) - args.limit} more (raise --limit)")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("xref", "vocab", "quantity", "term"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--compare", help="xref: report identifiers lost vs this doc")
    parser.add_argument("--terms", help="vocab: terms file (default purpose-terms.txt)")
    parser.add_argument("--min-uses", type=int, default=5,
                        help="term: uses before an undefined phrase is worth "
                             "reporting (default 5)")
    parser.add_argument("--window", type=int, default=1,
                        help="lines either side to accept a value or owner in "
                             "(default 1; DC-003 is the fixture's reminder that "
                             "the definition is often the next line)")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    project = os.path.abspath(os.path.expanduser(args.project))
    if args.command == "term":
        return cmd_term(args, project)
    if args.command == "quantity":
        return cmd_quantity(args, project)
    return cmd_xref(args, project) if args.command == "xref" else cmd_vocab(args, project)


if __name__ == "__main__":
    sys.exit(main())
