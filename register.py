#!/usr/bin/env python3
"""Merge closure and coverage into the review document you actually send.

The pipeline stops one step short of a work product. `closure.py` says which
findings the new revision touched; `trace.py` and `cluster-findings.py` say
which obligations it leaves undischarged. Both emit CSVs, and turning those into
"here is what you did and did not address" has been manual every time.

This does that merge and writes Markdown, ordered by how much judgement each
section needs — the ones that need none first:

    1  Not addressed          UNCHANGED / STILL ABSENT. The text did not move,
                              or the missing thing is still missing. No reading
                              required to assert these.
    2  Text removed           REMOVED. Fixed by deletion, or a regression.
    3  Touched — read these   CHANGED. Says nothing about whether the fix is good.
    4  Apparently addressed   ADDED, for absence findings.
    5  New candidate gaps     Coverage clusters that corroborate no open finding.

    ./register.py --project . --from deliverable-v1 --to deliverable-v2 \
        --coverage cov-deliverable-v2.csv --candidates candidates-deliverable-v2.csv

WHERE THE JUDGEMENT STILL LIVES. Section 3 is a reading list, not a verdict: a
real fix and a cosmetic reword are identical to every check here. Section 5 is
candidates, and on the live corpus roughly a quarter of flagged rows were false
positives. The document is a starting draft that shortens the work; it is not
the review, and the sections are labelled so nobody mistakes one for the other.
"""

import argparse
import csv
import os
import re
import sys

import closure

# What the vendor claims, against what the document shows. The pairs that matter
# are the contradictions: a row marked done whose text never moved, and a row
# marked untouched whose text did.
CLAIMED_DONE = ("completed", "complete", "done", "closed", "resolved")
CLAIMED_OPEN = ("new", "", "block identified", "blocked")
SETTLED = ("UNCHANGED", "STILL ABSENT")
MOVED = ("CHANGED", "ADDED", "REMOVED")


def adjudication(state, status):
    """Compare a claimed status against what closure found. None = no comment."""
    low = (status or "").strip().lower()
    if low in CLAIMED_DONE and state in SETTLED:
        return ("DISPUTED",
                "marked done, but the text carrying it did not move")
    if low in CLAIMED_DONE and state in MOVED:
        return ("SUPPORTED", "marked done, and the text did change — read it")
    if low == "in progress" and state in SETTLED:
        return ("NO MOVEMENT", "marked in progress, but nothing has changed yet")
    if low in CLAIMED_OPEN and state in MOVED:
        return ("UNCLAIMED CHANGE",
                "not marked done, but the text changed — may be fixed silently")
    return None

SECTIONS = [
    ("Not addressed", ["UNCHANGED", "STILL ABSENT"],
     "The passages carrying these findings are byte-identical between the two "
     "revisions, or the missing thing is still missing. Nothing here needs a "
     "judgement call — the text did not move."),
    ("Text removed — verify", ["REMOVED"],
     "Text carrying these findings is present in the old revision and absent "
     "from the new one. That is either the fix or a regression, and the two "
     "look identical from here."),
    ("Touched — read these", ["CHANGED"],
     "The relevant text changed. This says the revision engaged with the area; "
     "it says nothing about whether the change discharges the finding."),
    ("Apparently addressed", ["ADDED"],
     "Absent before, present now. For findings whose substance was that "
     "something was missing, this is the shape closure takes."),
    ("Signal unusable", ["TOO BROAD", "ABSENT", "NO TERMS"],
     "These findings' terms match too much of the document, match nothing in "
     "either revision, or do not exist because the source row has no comment "
     "text yet. Not results — fix the terms, or wait for the row to be written."),
]


def converge(coverage, synthesis, findings_map, lexicon):
    """Rank candidates by how many independent methods reached them.

    Measured on deliverable-v1 against a 90-finding human register: coverage tracing
    reaches 29, inventory synthesis reaches 27, and their UNION reaches 40 —
    because they share no machinery. Sixteen findings are reached by both.

    That overlap is the useful signal. A candidate two unrelated methods arrive
    at independently is worth reading before forty a single method produced, and
    DESIGN records convergence as the one ranking signal measured to work. Until
    now the two streams were handed over as separate flat lists and the overlap
    was invisible.
    """
    def hits(rows, field_names):
        out = {}
        for row in rows:
            blob = " ".join(str(row.get(f, "")) for f in field_names).lower()
            for finding in findings_map:
                terms = closure.expand(finding.get("terms", []), lexicon)
                if terms and any(t in blob for t in terms):
                    out.setdefault(finding["id"], []).append(row)
        return out

    cov = hits(coverage, ("requirement", "reason", "quote"))
    syn = hits(synthesis, ("requirement", "reason", "quote"))
    ranked = []
    for finding in findings_map:
        fid = finding["id"]
        in_cov, in_syn = fid in cov, fid in syn
        if not (in_cov or in_syn):
            continue
        ranked.append({
            "id": fid, "title": finding.get("title", ""),
            "class": finding.get("class", ""),
            "methods": ("coverage" if in_cov else "") +
                       ("+synthesis" if in_cov and in_syn else
                        ("synthesis" if in_syn else "")),
            "score": (2 if in_cov and in_syn else 1),
            "coverage": cov.get(fid, [])[:3],
            "synthesis": syn.get(fid, [])[:3],
        })
    ranked.sort(key=lambda r: (-r["score"], r["class"], r["id"]))
    return ranked

def read_csv(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def corroborating(finding_terms, coverage):
    """Coverage rows that mention this finding's vocabulary and are not met.

    Crude on purpose, and every match is printed. A finding that the revision
    left untouched AND that the RFO trace independently reports undischarged is
    a far stronger thing to put in front of a client than either alone — that is
    the one claim in this document supported by two methods that share no
    machinery.
    """
    hits = []
    for row in coverage:
        if row.get("verdict", "").lower() not in ("unmet", "partial"):
            continue
        haystack = (row.get("requirement", "") + " " +
                    row.get("reason", "")).lower()
        if any(term in haystack for term in finding_terms):
            hits.append(row)
    return hits


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--from", dest="old", required=True)
    parser.add_argument("--to", dest="new", required=True)
    parser.add_argument("--coverage", help="trace.py output for the new revision")
    parser.add_argument("--candidates", help="cluster-findings.py output")
    parser.add_argument("--synthesis",
                        help="synthesize.py --out CSV. Adds a convergence "
                             "section ranking candidates by how many "
                             "independent methods reached them")
    parser.add_argument("--map", default="register-map.yaml")
    parser.add_argument("--lexicon", default="lexicon.yaml")
    parser.add_argument("--matrix",
                        help="comment matrix .xlsx; adds the claim check")
    parser.add_argument("--sheet", default="Comments")
    parser.add_argument("--status-col", default="H")
    parser.add_argument("--adjudicated-col", default="I")
    parser.add_argument("--out", help="default review-<new>.md")
    parser.add_argument("--too-broad", type=int, default=120)
    parser.add_argument("--locators", type=int, default=4,
                        help="line references to cite per finding (default 4)")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    old_lines = closure.load(project, args.old)
    new_lines = closure.load(project, args.new)

    map_path = os.path.join(project, args.map)
    if not os.path.exists(map_path):
        sys.exit(f"no {args.map} in {project}")
    findings = closure.load_yaml(map_path).get("findings", [])

    lexicon_path = os.path.join(project, args.lexicon)
    lexicon = {}
    if os.path.exists(lexicon_path):
        lexicon = {k.lower(): v
                   for k, v in (closure.load_yaml(lexicon_path) or {}).items()}

    coverage = read_csv(os.path.join(project, args.coverage)
                        if args.coverage else None)
    candidates = read_csv(os.path.join(project, args.candidates)
                          if args.candidates else None)

    synthesis = read_csv(os.path.join(project, args.synthesis)
                         if args.synthesis else None)

    claims = {}
    if args.matrix:
        import matrix as matrix_reader
        path = os.path.expanduser(args.matrix)
        if not os.path.isabs(path):
            path = os.path.join(project, path)
        sheet = matrix_reader.read_sheet(path, args.sheet)
        for row in sheet[1:]:
            rid = row.get("A", "").strip()
            if rid:
                claims[rid] = {
                    "status": row.get(args.status_col, "").strip(),
                    "note": row.get(args.adjudicated_col, "").strip(),
                }

    rows = []
    for finding in findings:
        terms = closure.expand(finding.get("terms", []), lexicon)
        old_hits = closure.hits(old_lines, terms)
        new_hits = closure.hits(new_lines, terms)
        state, detail = closure.classify(
            old_hits, new_hits, args.too_broad,
            is_absence=bool(finding.get("absence")),
            has_terms=bool(finding.get("terms")))
        rows.append({
            "id": finding["id"], "class": finding.get("class", ""),
            "title": finding.get("title", ""), "state": state,
            "detail": detail or "", "terms": terms,
            "old": old_hits, "new": new_hits,
            "corroboration": corroborating(terms, coverage),
        })

    def sort_key(row):
        return (row["class"], row["id"][0], int(re.sub(r"\D", "", row["id"]) or 0))

    out_path = os.path.join(project, args.out or f"review-{args.new}.md")
    with open(out_path, "w", encoding="utf-8") as out:
        w = out.write
        w(f"# Review of `{args.new}` against `{args.old}`\n\n")
        w(f"{len(rows)} open findings checked against the new revision, plus "
          f"{len(coverage)} traced obligations"
          f"{' and ' + str(len(candidates)) + ' ranked clusters' if candidates else ''}.\n\n")

        counts = {}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        w("| State | Findings | Means |\n|---|---|---|\n")
        meaning = {
            "UNCHANGED": "text identical — not addressed",
            "STILL ABSENT": "still missing — not addressed",
            "REMOVED": "text gone — fix or regression",
            "CHANGED": "touched — needs reading",
            "ADDED": "now present",
            "TOO BROAD": "terms unusable",
            "ABSENT": "terms match nothing",
        }
        for state in closure.STATES:
            if counts.get(state):
                w(f"| {state} | {counts[state]} | {meaning.get(state,'')} |\n")

        settled = counts.get("UNCHANGED", 0) + counts.get("STILL ABSENT", 0)
        w(f"\n**{settled} of {len(rows)} findings can be asserted as not "
          f"addressed without reading anything.**\n")

        # The adjudication check leads, when there is a matrix to check against.
        # Every other section describes the document; this one describes the
        # disagreement, which is what the meeting is actually about.
        if claims:
            verdicts = []
            for row in rows:
                claim = claims.get(row["id"])
                if not claim:
                    continue
                result = adjudication(row["state"], claim["status"])
                if result:
                    verdicts.append((row, claim, result))

            checked = sum(1 for row in rows if row["id"] in claims)
            unmatched = len(claims) - checked
            disputed = [v for v in verdicts if v[2][0] == "DISPUTED"]
            w(f"\n---\n\n## Adjudication check ({len(verdicts)} of "
              f"{checked} rows)\n\n"
              "Each row's claimed status against what the revision actually "
              "shows. Rows where the claim and the document agree are omitted; "
              "only disagreements and confirmations that need reading appear.\n")
            if unmatched:
                w(f"\n{unmatched} matrix row(s) have no entry in the register "
                  f"map and were not checked — re-run `matrix.py`.\n")
            if disputed:
                w(f"\n**{len(disputed)} row(s) are marked done on text that did "
                  f"not move.** Those are the ones to take\nback to them, and "
                  f"the evidence is a line number rather than an opinion.\n")

            order = ["DISPUTED", "NO MOVEMENT", "UNCLAIMED CHANGE", "SUPPORTED"]
            for kind in order:
                group = [v for v in verdicts if v[2][0] == kind]
                if not group:
                    continue
                w(f"\n### {kind} ({len(group)})\n\n")
                w("| ID | Claimed | Closure | Finding |\n|---|---|---|---|\n")
                for row, claim, (_, why) in group:
                    w(f"| {row['id']} | {claim['status'] or '—'} | "
                      f"{row['state']} ({len(row['old'])}→{len(row['new'])}) | "
                      f"{row['title'][:60]} |\n")
                w(f"\n*{group[0][2][1]}.*\n")

        if synthesis and coverage:
            ranked = converge(coverage, synthesis, findings, lexicon)
            both = [r for r in ranked if r["score"] == 2]
            w(f"\n---\n\n## Convergent candidates ({len(both)} of "
              f"{len(ranked)})\n\n"
              "Reached independently by BOTH obligation tracing and inventory "
              "synthesis. The two share no machinery — one retrieves passages "
              "per requirement, the other reads every section and queries the "
              "result as a graph — so agreement between them is evidence "
              "rather than repetition. Read these first.\n\n")
            w("| Finding | Class | Methods |\n|---|---|---|\n")
            for row in both:
                w(f"| {row['id']} {row['title'][:52]} | {row['class']} | "
                  f"{row['methods']} |\n")
            single = [r for r in ranked if r["score"] == 1]
            if single:
                w(f"\n{len(single)} further candidate(s) were reached by one "
                  f"method only, and sit in the sections below.\n")

        for title, states, blurb in SECTIONS:
            group = sorted([r for r in rows if r["state"] in states], key=sort_key)
            if not group:
                continue
            w(f"\n---\n\n## {title} ({len(group)})\n\n{blurb}\n\n")
            for row in group:
                w(f"### {row['id']} — {row['title']}\n\n")
                w(f"*{row['class']}* · {row['state']}")
                if row["detail"]:
                    w(f" ({row['detail']})")
                w(f" · {len(row['old'])} → {len(row['new'])} lines\n\n")

                cited = row["new"] or row["old"]
                which = args.new if row["new"] else args.old
                for number, text in list(cited.items())[:args.locators]:
                    w(f"- `{which}:{number}` {text.strip()[:150]}\n")
                if len(cited) > args.locators:
                    w(f"- … {len(cited) - args.locators} more\n")

                if row["corroboration"]:
                    w(f"\n**Corroborated independently** — the RFO trace reports "
                      f"{len(row['corroboration'])} obligation(s) in this area "
                      f"undischarged:\n\n")
                    for hit in row["corroboration"][:3]:
                        w(f"- `{hit.get('obligation','')}` "
                          f"*{hit.get('verdict','')}* — "
                          f"{hit.get('requirement','')[:120]}\n")
                w("\n")

        if candidates:
            covered_terms = {t for r in rows for t in r["terms"]}
            novel = []
            for cluster in candidates:
                blob = (cluster.get("lead_requirement", "") + " " +
                        cluster.get("lead_reason", "")).lower()
                if not any(term in blob for term in covered_terms):
                    novel.append(cluster)
            w(f"\n---\n\n## New candidate gaps ({len(novel)} of "
              f"{len(candidates)} clusters)\n\n"
              "Coverage clusters whose vocabulary matches no open finding, so "
              "they are not simply restating the register. Candidates, not "
              "findings — on the live corpus about a quarter of flagged rows "
              "were false positives, and these have had no adversarial pass.\n\n")
            for cluster in novel:
                w(f"- **rank {cluster.get('rank','?')}** "
                  f"(strength {cluster.get('strength','?')}, "
                  f"{len(cluster.get('obligations','').split())} obligations) — "
                  f"{cluster.get('lead_requirement','')[:160]}\n")

        w("\n---\n\n*Generated by `register.py`. Sections 1 and 2 are "
          "assertions; section 3 is a reading list; the candidates are leads. "
          "Nothing here has been adjudicated by a human.*\n")

    print(f"wrote {out_path}")
    print("  " + "  ".join(f"{s}={counts[s]}" for s in closure.STATES
                           if counts.get(s)))
    if coverage:
        corroborated = sum(1 for r in rows if r["corroboration"])
        print(f"  {corroborated} finding(s) corroborated by the RFO trace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
