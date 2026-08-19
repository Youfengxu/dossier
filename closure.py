#!/usr/bin/env python3
"""Did the new revision touch the passages your findings were about?

When a revision lands you have to answer one question per open finding: did they
address it? Reading the whole document again to find out is what makes review
cycles expensive, and it is the part a tool can genuinely take off you — not by
judging whether the fix is good, but by telling you where to look and, more
usefully, where there is nothing to look at.

For each finding in register-map.yaml, take the distinctive strings that finding
is about, collect every line carrying them in each revision, and compare:

    UNCHANGED     the passages are byte-identical. They did not touch it. The
                  strongest signal here, and it needs no judgement.
    STILL ABSENT  nothing in either revision, for a finding marked `absence:
                  true` — i.e. one whose substance is that something is
                  missing. Also a conclusion: still missing.
    REMOVED       present before, gone now. Fixed by deletion, or a regression.
    ADDED         absent before, present now. For an absence finding this is
                  closure.
    CHANGED       present in both, text differs. They touched it; read the diff.
    NO TERMS      the finding carries no search term, so it was never asked
                  about. Either its source row has no comment text yet, or no
                  candidate term survived validation.
    ABSENT        nothing in either revision, for a finding NOT marked as an
                  absence finding. This is a broken query — fix the terms.
    TOO BROAD     the terms match so much of the document that equality carries
                  no information about this finding. Tighten them.

A finding is marked `absence: true` in register-map.yaml. The distinction
matters because the same counts — zero and zero — mean opposite things: for
B4-style findings ("this component is never named") it is the answer, and for a
mistyped term it is a bug. Only the register knows which, so only the register
gets to say.

    ./closure.py --project . --from arch-v5 --to arch-v6
    ./closure.py --project . --from arch-v5 --to arch-v7 --show-unchanged
    ./closure.py --project . --from arch-v5 --to arch-v7 --class compliance

WHAT THIS DOES NOT DO. A CHANGED verdict says the text moved, not that the
finding is closed; a fix and a cosmetic reword look identical here. Only
UNCHANGED and ABSENT are conclusions, and they are conclusions of the form
"nothing happened" — which is exactly the claim that is expensive to establish
by hand and cheap to establish here.
"""

import argparse
import csv
import difflib
import json
import os
import re
import sys

STATES = ["UNCHANGED", "STILL ABSENT", "REMOVED", "ADDED", "CHANGED",
          "NO TERMS", "ABSENT", "TOO BROAD"]


def load(project, slug):
    manifest_path = os.path.join(project, "parsed", "MANIFEST.json")
    if not os.path.exists(manifest_path):
        sys.exit("corpus not frozen — run freeze.py first")
    manifest = json.load(open(manifest_path))
    for doc in manifest["documents"]:
        if doc["slug"] == slug:
            path = os.path.join(project, doc["parsed"])
            text = open(path, encoding="utf-8", errors="replace").read()
            return text.splitlines()
    sys.exit(f"no document {slug!r}; have: "
             + ", ".join(d["slug"] for d in manifest["documents"]))


def load_yaml(path):
    """Minimal loader; falls back to PyYAML when the file uses block style."""
    try:
        import yaml
        return yaml.safe_load(open(path, encoding="utf-8"))
    except ImportError:
        sys.exit("PyYAML required: pip3 install pyyaml")


def expand(terms, lexicon):
    """A term plus every variant the project lexicon knows for it.

    Without this, a UK-spelled finding term reports UNCHANGED against a
    US-spelled deliverable for the same reason the original sweep did: the
    passages were never found in either revision.

    The lexicon is keyed by single words but terms are usually phrases, so
    substitute variants WORD BY WORD inside the phrase as well as looking the
    whole term up. Looking up only the whole term silently reintroduces the bug
    the lexicon exists to fix: "community behaviour" is not a lexicon key, so it
    would never reach "community behavior" and would report absent against a
    document that uses the US spelling throughout.
    """
    out = set()
    for term in terms:
        low = term.lower()
        out.add(low)
        for variant in lexicon.get(low, []):
            out.add(variant.lower())
        parts = re.findall(r"\w+|\W+", low)
        for index, word in enumerate(parts):
            for variant in lexicon.get(word, []):
                out.add("".join(parts[:index] + [variant.lower()]
                                + parts[index + 1:]))
    return sorted(out)


def hits(lines, terms):
    """Line numbers and text for every line carrying any term."""
    found = {}
    for number, line in enumerate(lines, 1):
        low = line.lower()
        if any(term in low for term in terms):
            found[number] = line
    return found


def normalise(text):
    return re.sub(r"\s+", " ", text).strip()


def classify(old, new, too_broad, is_absence=False, has_terms=True):
    # A finding with no terms was never asked a question. That is a different
    # thing from one that was asked and found nothing, and it wants a different
    # response — supply a term, or wait for the client to write the comment —
    # so it gets a different state rather than being folded into ABSENT.
    if not has_terms:
        return "NO TERMS", None
    if len(old) > too_broad or len(new) > too_broad:
        return "TOO BROAD", None
    if not old and not new:
        # For a finding whose substance IS that something is missing, matching
        # nothing in either revision is the answer, not a broken query. The
        # register has to say which kind it is; the tool cannot tell from the
        # counts alone, and guessing would turn every mistyped term into a
        # confident "still missing".
        return ("STILL ABSENT", None) if is_absence else ("ABSENT", None)
    if old and not new:
        return "REMOVED", None
    if new and not old:
        return "ADDED", None

    old_blob = [normalise(t) for t in old.values()]
    new_blob = [normalise(t) for t in new.values()]
    if old_blob == new_blob:
        raw_same = list(old.values()) == list(new.values())
        return "UNCHANGED", None if raw_same else "reformatted only"

    diff = list(difflib.unified_diff(old_blob, new_blob, n=0, lineterm=""))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    dropped = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    return "CHANGED", f"+{added} -{dropped}"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--from", dest="old", required=True,
                        help="slug of the revision the findings were raised on")
    parser.add_argument("--to", dest="new", required=True,
                        help="slug of the revision to check")
    parser.add_argument("--map", default="register-map.yaml")
    parser.add_argument("--lexicon", default="lexicon.yaml")
    parser.add_argument("--class", dest="klass",
                        help="only findings of this class")
    parser.add_argument("--state", help="only findings in this state")
    parser.add_argument("--show-unchanged", action="store_true",
                        help="print the passages behind each UNCHANGED verdict")
    parser.add_argument("--too-broad", type=int, default=120,
                        help="matched-line count above which the signal is "
                             "unusable (default 120)")
    parser.add_argument("--out", help="write results to CSV")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    old_lines = load(project, args.old)
    new_lines = load(project, args.new)

    map_path = os.path.join(project, args.map)
    if not os.path.exists(map_path):
        sys.exit(f"no {args.map} in {project} — closure needs a finding register")
    findings = load_yaml(map_path).get("findings", [])

    lexicon_path = os.path.join(project, args.lexicon)
    lexicon = {}
    if os.path.exists(lexicon_path):
        lexicon = {k.lower(): v for k, v in (load_yaml(lexicon_path) or {}).items()}
    else:
        print(f"note: no {args.lexicon}; spelling variants will not be matched\n")

    rows = []
    for finding in findings:
        if args.klass and finding.get("class") != args.klass:
            continue
        terms = expand(finding.get("terms", []), lexicon)
        old_hits = hits(old_lines, terms)
        new_hits = hits(new_lines, terms)
        state, detail = classify(old_hits, new_hits, args.too_broad,
                                 is_absence=bool(finding.get("absence")),
                                 has_terms=bool(finding.get("terms")))
        rows.append({
            "id": finding["id"], "class": finding.get("class", ""),
            "title": finding.get("title", ""), "state": state,
            "detail": detail or "", "old_lines": len(old_hits),
            "new_lines": len(new_hits), "terms": "; ".join(terms),
            "_old": old_hits, "_new": new_hits,
        })

    if args.state:
        rows = [r for r in rows if r["state"] == args.state.upper()]

    print(f"closure  {args.old} -> {args.new}   "
          f"{len(rows)} findings\n" + "=" * 78)

    for state in STATES:
        group = [r for r in rows if r["state"] == state]
        if not group:
            continue
        print(f"\n{state}  ({len(group)})")
        print("-" * 78)
        for row in group:
            detail = f"  [{row['detail']}]" if row["detail"] else ""
            print(f"  {row['id']:<5} {row['class']:<11} "
                  f"{row['old_lines']:>4} -> {row['new_lines']:<4}"
                  f"{detail}")
            print(f"        {row['title']}")
            if state == "TOO BROAD":
                print(f"        terms: {row['terms']}")

    if args.show_unchanged:
        unchanged = [r for r in rows if r["state"] == "UNCHANGED"]
        if unchanged:
            print("\n" + "=" * 78)
            print("UNCHANGED passages — these are the lines they did not touch")
            print("=" * 78)
            for row in unchanged:
                print(f"\n{row['id']}  {row['title']}")
                for number, text in list(row["_new"].items())[:12]:
                    print(f"  {args.new}:{number}: {text.strip()[:110]}")
                if len(row["_new"]) > 12:
                    print(f"  ... {len(row['_new']) - 12} more")

    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    print("\n" + "=" * 78)
    print("  ".join(f"{s}={counts[s]}" for s in STATES if counts[s]))

    settled = counts["UNCHANGED"] + counts["STILL ABSENT"]
    if settled:
        print(f"\n{settled} finding(s) are settled without judgement: the text "
              f"did not move, or the\nmissing thing is still missing. Raise "
              f"those first.")
    if counts["ABSENT"]:
        print(f"\n{counts['ABSENT']} finding(s) match nothing in either "
              f"revision and are not marked as absence\nfindings. Those are "
              f"broken queries, not results — fix the terms, or add\n"
              f"`absence: true` if the finding really is that something is "
              f"missing.")
    if counts["NO TERMS"]:
        print(f"\n{counts['NO TERMS']} finding(s) carry no search term at all "
              f"and were never asked about.\nEither the source row has no "
              f"comment text yet, or no candidate term survived\nvalidation. "
              f"Neither is a result.")
    if counts["TOO BROAD"]:
        print(f"\n{counts['TOO BROAD']} finding(s) have terms too common to carry "
              f"a signal. Replace them with\nstrings from the finding's own "
              f"wording — an identifier, a quoted phrase.")

    if args.out:
        out_path = os.path.join(project, args.out)
        with open(out_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "id", "class", "title", "state", "detail",
                "old_lines", "new_lines", "terms"])
            writer.writeheader()
            for row in rows:
                writer.writerow({k: v for k, v in row.items()
                                 if not k.startswith("_")})
        print(f"\nwrote {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
