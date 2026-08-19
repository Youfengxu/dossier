#!/usr/bin/env python3
"""Score a pipeline run against a fixture's ground truth.

Recall alone is a trap. A tool that flags everything scores 100% recall and is
useless, so this reports **precision too** — which requires knowing the correct
verdict for every obligation, not just for the defective ones.

Three stages, scored independently because they fail differently:

  obligations   did extraction find every requirement? (recall only — spurious
                obligations are rare and visible)
  coverage      of the obligations flagged as problems, how many really were?
                and of the real problems, how many were flagged?
  verify        did adversarial attack keep the true findings and kill the
                false ones? Measured as the change in precision it produces.

    ./score.py --project fixtures/floodtwin
    ./score.py --project fixtures/floodtwin --coverage coverage.csv --findings findings.csv

Exits non-zero if any headline number falls below --min, so it can gate a commit.
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter

FLAGGED = ("unmet", "partial", "unverifiable")
WORST = {"met": 0, "unverifiable": 1, "partial": 2, "unmet": 3}


def load_ground_truth(path):
    try:
        import yaml
    except ImportError:
        sys.exit("score.py needs PyYAML (the tools themselves do not)")
    return yaml.safe_load(open(path, encoding="utf-8"))


def obligation_refs(path):
    """id -> source_ref, from the emitted obligations.yaml."""
    refs, current = {}, None
    for line in open(path, encoding="utf-8"):
        if line.strip().startswith("- id:"):
            current = line.split(":", 1)[1].strip()
        match = re.match(r'\s*source_ref:\s*"(.*)"', line)
        if match and current:
            refs[current] = match.group(1)
    return refs


def rate(hit, total):
    return f"{hit}/{total}" + (f"  {100*hit/total:5.1f}%" if total else "     —")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--ground-truth", default="ground-truth.yaml")
    parser.add_argument("--obligations", default="obligations.yaml")
    # No default. It used to be "coverage.csv", a file the fixture does not
    # contain and .gitignore excludes, so the command the README offers as the
    # way to check your install scored nothing, printed "coverage (not run)",
    # and exited ok. A check that passes without checking is worse than no
    # check; if the file is absent, say which one and fail.
    parser.add_argument("--coverage",
                        help="coverage CSV to score (e.g. cov-embed-12.csv)")
    parser.add_argument("--findings", default="findings.csv")
    parser.add_argument("--doc", default="deliverable-v1")
    parser.add_argument("--requirements", default="requirements.md")
    parser.add_argument("--min", type=float, default=0.0,
                        help="fail if coverage recall or precision falls below this")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    gt = load_ground_truth(os.path.join(project, args.ground_truth))
    expected = (gt.get("expected_coverage") or {}).get(args.doc)
    if not expected:
        sys.exit(f"ground truth has no expected_coverage for {args.doc!r}")

    print(f"=== {gt.get('fixture','fixture')} / {args.doc} ===\n")
    failures = []

    # -- stage 1: obligation extraction ------------------------------------
    obligations_path = os.path.join(project, args.obligations)
    refs = {}
    if os.path.exists(obligations_path):
        refs = obligation_refs(obligations_path)
        found = {r for r in refs.values() if r}
        wanted = set(re.findall(r"^\*\*([A-Z]-\d+)\.\*\*",
                                open(os.path.join(project, args.requirements),
                                     encoding="utf-8").read(), re.M))
        missing = sorted(wanted - found)
        print(f"obligations   recall  {rate(len(wanted & found), len(wanted))}"
              + (f"   MISSED {', '.join(missing)}" if missing else ""))
    else:
        print("obligations   (not run)")

    # -- stage 2: coverage --------------------------------------------------
    coverage_path = os.path.join(project, args.coverage) if args.coverage else None
    by_ref = {}
    if coverage_path and os.path.exists(coverage_path) and refs:
        for row in csv.DictReader(open(coverage_path, encoding="utf-8")):
            ref = refs.get(row["obligation"])
            if not ref:
                continue
            if row["verdict"] == "not_applicable":
                by_ref.setdefault(ref, "not_applicable")
                continue
            if WORST[row["verdict"]] > WORST.get(by_ref.get(ref, "met"), 0):
                by_ref[ref] = row["verdict"]
            by_ref.setdefault(ref, "met")

        matrix = Counter()
        exact = 0
        wrong_flags, missed = [], []
        skipped = 0
        for ref, want in sorted(expected.items()):
            got = by_ref.get(ref)
            if got is None:
                continue
            if got == "not_applicable":
                skipped += 1
                continue
            exact += (got == want)
            want_flag, got_flag = want in FLAGGED, got in FLAGGED
            matrix[(want_flag, got_flag)] += 1
            if got_flag and not want_flag:
                wrong_flags.append((ref, got))
            if want_flag and not got_flag:
                missed.append((ref, want))

        tp = matrix[(True, True)]
        fp = matrix[(False, True)]
        fn = matrix[(True, False)]
        tn = matrix[(False, False)]
        scored = tp + fp + fn + tn
        recall = tp / (tp + fn) if tp + fn else 0.0
        precision = tp / (tp + fp) if tp + fp else 0.0

        print(f"\ncoverage      scored {scored} of {len(expected)} requirements"
              + (f"  ({skipped} permissive, not judged)" if skipped else ""))
        print(f"              recall     {rate(tp, tp + fn)}   "
              f"(real problems that got flagged)")
        print(f"              precision  {rate(tp, tp + fp)}   "
              f"(flags that were real problems)")
        print(f"              exact verdict match  {rate(exact, scored)}")
        print(f"              confusion  tp={tp} fp={fp} fn={fn} tn={tn}")
        if wrong_flags:
            print("\n  FALSE POSITIVES — flagged, but the deliverable satisfies these:")
            for ref, got in wrong_flags:
                print(f"    {ref}  got '{got}', expected '{expected[ref]}'"
                      f"   {(gt_note(gt, ref) or '')}")
        if missed:
            print("\n  MISSED — real problems the coverage run called met:")
            for ref, want in missed:
                print(f"    {ref}  expected '{want}'")
        if recall < args.min:
            failures.append(f"coverage recall {recall:.2f} < {args.min}")
        if precision < args.min:
            failures.append(f"coverage precision {precision:.2f} < {args.min}")
    elif args.coverage:
        # Named a file that is not there. Silence here is how the README's own
        # verification step reported ok while scoring nothing.
        failures.append(f"coverage file not found: {args.coverage}")
        print(f"\ncoverage      MISSING — {args.coverage}")
    else:
        print("\ncoverage      (not scored — pass --coverage <file>)")
        print("              available here: " + ", ".join(sorted(
            f for f in os.listdir(project) if f.startswith("cov-")
            and f.endswith(".csv")) or ["none"]))

    # -- stage 3: verification ---------------------------------------------
    findings_path = os.path.join(project, args.findings)
    if os.path.exists(findings_path) and refs:
        kept_tp = kept_fp = killed_tp = killed_fp = 0
        for row in csv.DictReader(open(findings_path, encoding="utf-8")):
            ref = refs.get(row["obligation"])
            if ref not in expected:
                continue
            real = expected[ref] in FLAGGED
            survives = row["status"] in ("confirmed", "contested")
            if survives and real:
                kept_tp += 1
            elif survives and not real:
                kept_fp += 1
            elif not survives and real:
                killed_tp += 1
            else:
                killed_fp += 1
        total = kept_tp + kept_fp + killed_tp + killed_fp
        if total:
            after = kept_tp / (kept_tp + kept_fp) if kept_tp + kept_fp else 0.0
            before = ((kept_tp + killed_tp) / total)
            print(f"\nverify        attacked {total} candidates")
            print(f"              kept    {kept_tp} real, {kept_fp} false")
            print(f"              killed  {killed_fp} false, {killed_tp} REAL "
                  f"{'  <-- lost findings' if killed_tp else ''}")
            print(f"              precision {before:.2f} -> {after:.2f}")
            if killed_tp:
                failures.append(f"verification killed {killed_tp} real finding(s)")
    else:
        print("\nverify        (not run)")

    if failures:
        print("\nFAILED:")
        for item in failures:
            print("  " + item)
        return 1
    print("\nok")
    return 0


def gt_note(gt, ref):
    for group in ("defects", "decoys"):
        for item in gt.get(group) or []:
            if item.get("obligation") == ref:
                return f"[{item['id']} {item.get('expect','')}]"
    return ""


if __name__ == "__main__":
    sys.exit(main())
