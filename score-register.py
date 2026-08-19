#!/usr/bin/env python3
"""Score a pipeline run against a human-written findings register.

The fixtures measure the pipeline against constructed or borrowed answer keys.
This measures it against the only answer key that matters: a register a reviewer
actually produced, for the document actually under review.

It answers two questions the fixtures cannot:

  RECALL BY CLASS   Of the findings a human made, how many did the run surface —
                    and, more usefully, which KINDS did it surface? A tool that
                    finds every "you did not include X" and no "your design
                    contradicts itself" has a shape, not just a score.

  UNMATCHED FLAGS   How many flagged obligations correspond to no register entry
                    at all? Each is either a false positive or something the
                    human missed, and only a human can say which. The count is
                    the honest upper bound on precision.

Needs a register map — one entry per finding, with its class and a few
distinctive match terms:

    findings:
      - id: B7
        class: compliance
        title: Sensor Fabric interface is not specified
        terms: ["sensor fabric"]

Classes are free text; they are grouped and reported as given. Matching is
deliberately crude and fully auditable: a register finding counts as surfaced
when any of its terms appears in a flagged row's requirement, reason or quote,
and the matching row is printed so a wrong match is visible rather than silent.

    ./score-register.py --project <dir> --map register-map.yaml \
        --coverage coverage-architecture.csv --findings findings-architecture.csv
"""

import argparse
import csv
import os
import re
import sys
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm                                                   # noqa: E402
from llm import Embedder, cosine                          # noqa: E402

FLAGGED = ("unmet", "partial", "unverifiable")


def load_map(path):
    try:
        import yaml
    except ImportError:
        sys.exit("score-register.py needs PyYAML")
    data = yaml.safe_load(open(path, encoding="utf-8"))
    return data["findings"], data.get("notes", "")


def haystack(row):
    return re.sub(r"\s+", " ", " ".join([
        row.get("requirement", ""), row.get("reason", ""),
        row.get("quote", "")])).lower()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--map", default="register-map.yaml")
    parser.add_argument("--coverage", default="coverage.csv")
    parser.add_argument("--findings", default=None,
                        help="optional verify output; restricts to surviving rows")
    parser.add_argument("--show-unmatched", type=int, default=10)
    parser.add_argument("--match", choices=("terms", "embed"), default="embed",
                        help="terms is keyword substring — fast and far too "
                             "loose: 'exposure' matches any row mentioning "
                             "exposure, not the finding about per-item scaling. "
                             "embed compares meaning and reports the score.")
    parser.add_argument("--threshold", type=float, default=0.62)
    parser.add_argument("--register", default=None,
                        help="the register markdown. Matching then uses each "
                             "finding's OWN text rather than a hand-written "
                             "summary — richer, and not tunable by me.")
    parser.add_argument("--embed-url",
                        default=llm.DEFAULT_EMBED_URL)
    parser.add_argument("--embed-model", default="Qwen3-Embedding-8B-Q4_K_M")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    findings, notes = load_map(os.path.join(project, args.map))

    # A finding's own wording is far better matching material than a summary I
    # wrote. B7's register entry names "content payload, asset agent or
    # receiving target"; my summary said "Sensor Fabric interface is not
    # specified", which shares no vocabulary with the obligations that found it.
    if args.register:
        body = open(os.path.join(project, args.register),
                    encoding="utf-8", errors="replace").read()
        text_by_id = {}
        for match in re.finditer(r"^\|\s*\**([BARE]\d{1,2})\**\s*\|(.+?)(?=\n\|)",
                                 body, re.M | re.S):
            text_by_id[match.group(1)] = re.sub(
                r"[|*`]", " ", match.group(2))[:1200]
        hit_count = 0
        for finding in findings:
            if finding["id"] in text_by_id:
                finding["register_text"] = text_by_id[finding["id"]]
                hit_count += 1
        print(f"register text loaded for {hit_count}/{len(findings)} findings")

    rows = [r for r in csv.DictReader(
        open(os.path.join(project, args.coverage), encoding="utf-8"))
        if r["verdict"] in FLAGGED]

    survivors = None
    if args.findings:
        path = os.path.join(project, args.findings)
        if os.path.exists(path):
            survivors = {r["obligation"] for r in csv.DictReader(
                open(path, encoding="utf-8"))
                if r["status"] in ("confirmed", "contested")}

    print(f"register: {len(findings)} findings")
    print(f"run     : {len(rows)} flagged obligations"
          + (f", {len(survivors)} surviving verification" if survivors else "")
          + "\n")

    embedder = row_vectors = None
    if args.match == "embed":
        embedder = Embedder(project, model=args.embed_model, url=args.embed_url)
        if not embedder.available():
            sys.exit("embedding endpoint unavailable; use --match terms and "
                     "treat the result as an upper bound")
        candidates = [r for r in rows
                      if survivors is None or r["obligation"] in survivors]
        row_vectors = embedder.embed([haystack(r)[:2000] for r in candidates])
        rows = candidates

    matched_rows = set()
    by_class = OrderedDict()
    detail = defaultdict(list)

    for finding in findings:
        cls = finding.get("class", "unclassified")
        by_class.setdefault(cls, {"found": 0, "total": 0})
        by_class[cls]["total"] += 1
        # MANY-TO-ONE. Several obligations converging on one gap is the
        # strongest signal the pipeline produces — three separate obligations
        # landing on a missing Sensor Fabric interface is corroboration, not
        # duplication. A one-best-row matcher reports the extras as unmatched
        # noise, which inverts the meaning.
        hits = []
        if embedder is not None:
            query = embedder.embed([
                finding.get("register_text")
                or (finding.get("title", "") + ". "
                    + " ".join(finding.get("terms", [])))])[0]
            for row, vector in zip(rows, row_vectors):
                value = cosine(query, vector)
                if value >= args.threshold:
                    hits.append((value, row))
            hits.sort(key=lambda x: -x[0])
        else:
            terms = [t.lower() for t in finding.get("terms", [])]
            for row in rows:
                if survivors is not None and row["obligation"] not in survivors:
                    continue
                if any(term in haystack(row) for term in terms):
                    hits.append((1.0, row))
        if hits:
            by_class[cls]["found"] += 1
            for _, row in hits:
                matched_rows.add(row["obligation"])
            best_score, best_row = hits[0]
            extra = f" +{len(hits)-1}" if len(hits) > 1 else ""
            detail[cls].append((finding["id"], "FOUND", finding.get("title", "")[:52],
                                f"{best_row['obligation']} {best_score:.2f}{extra}",
                                best_row["verdict"]))
        else:
            detail[cls].append((finding["id"], "miss ", finding.get("title", "")[:52],
                                "", ""))

    total_found = sum(v["found"] for v in by_class.values())
    total = sum(v["total"] for v in by_class.values())

    print(f"{'class':16} {'found':>10}")
    for cls, counts in by_class.items():
        share = 100 * counts["found"] / counts["total"] if counts["total"] else 0
        print(f"  {cls:14} {counts['found']:3}/{counts['total']:<3} {share:5.0f}%")
    print(f"  {'TOTAL':14} {total_found:3}/{total:<3} "
          f"{100*total_found/total if total else 0:5.0f}%")
    print(f"\n  {len(matched_rows)} of {len(rows)} flagged obligations support a "
          f"register finding\n  ({100*len(matched_rows)/len(rows) if rows else 0:.0f}% "
          f"corroborating; the rest are noise or novel)")

    for cls in by_class:
        print(f"\n-- {cls} --")
        for identifier, state, title, obligation, verdict in detail[cls]:
            tail = f"   <- {obligation} [{verdict}]" if obligation else ""
            print(f"  {state} {identifier:4} {title:54}{tail}")

    unmatched = [r for r in rows if r["obligation"] not in matched_rows
                 and (survivors is None or r["obligation"] in survivors)]
    print(f"\n-- flagged but matching NO register finding ({len(unmatched)}) --")
    print("   Each is a false positive or something the register missed. Only a")
    print("   human can say which; this count is the ceiling on precision.")
    for row in unmatched[:args.show_unmatched]:
        print(f"  {row['obligation']:7} [{row['verdict']:12}] "
              f"{row['requirement'][:66]}")
    if len(unmatched) > args.show_unmatched:
        print(f"  … {len(unmatched) - args.show_unmatched} more")

    if notes:
        print(f"\n{notes.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
