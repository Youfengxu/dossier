#!/usr/bin/env python3
"""Score a claim-index run against the fixture's ground truth.

Two numbers, and the second is the one that decides whether the tool is usable:

  recall     did it find the planted contradictions
  precision  of what it reported, how much was real

Precision is the harder constraint. A contradiction report sends a reviewer to
read two passages in full; a false one costs several minutes and a little
credibility, and a handful of them and the tool gets switched off. So the bar
here is deliberately asymmetric — a run that finds one of two defects cleanly is
more useful than one that finds both amid six false alarms.

    ./score-claims.py --project fixtures/floodtwin --claims claims-v1.json

Matching is by ANCHOR: a ground-truth entry names a substring of the document,
and a finding matches if either of its quotes contains that substring, or if
both its line numbers fall inside the entry's stated line span. Line numbers
alone would rot the moment the fixture is edited.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import closure  # noqa: E402
import inventory  # noqa: E402

# Ground-truth classes a claim index can reach: D3 (a document asserting
# something untrue about itself), D5 (two components claiming one capability),
# D6 (a capability nobody owns). All three are pairs of statements that cannot
# both hold.
#
# D6 was initially excluded here as "a graph property, synthesize.py's job" —
# and the first scored run promptly reported a D6 defect the claim index had
# found legitimately, by pairing two deferral statements. Scoring it as a false
# positive punished the tool for being right. A class is reachable if the tool
# reaches it, not if the design predicted it would.
REACHABLE = {"D3", "D5", "D6"}


def normalise(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def matches(finding, entry):
    anchor = normalise(entry.get("anchor"))
    quotes = normalise(finding["a"]["quote"]) + " || " + \
        normalise(finding["b"]["quote"])
    if anchor and len(anchor) >= 12 and anchor in quotes:
        return True
    span = entry.get("lines")
    if span and isinstance(span, list) and len(span) == 2:
        return all(span[0] <= f["start"] <= span[1]
                   for f in (finding["a"], finding["b"]))
    # No fuzzy fallback. A vocabulary-overlap rule was tried and matched one
    # finding to two unrelated defects at once, reporting 200% precision — a
    # scorer loose enough to flatter the tool measures nothing. An entry with
    # neither an anchor nor a line span is simply not auto-scorable, and saying
    # so is more useful than guessing.
    return False


# Said of an index written before claim.py recorded its sections, and of one
# written before it kept the pairs it could not judge.
UNSAID = ("the index does not say how many sections its claims were taken "
          "from:\n  a MISSED below may be a defect in a section it never read")
UNJUDGED_UNSAID = ("the index does not say whether every candidate pair was "
                   "judged:\n  a MISSED below may be a pair nobody judged")
NOT_JUDGED = ("a candidate pair that matches it was never judged: this is "
              "not a verdict on it")


def a_claim(value):
    return isinstance(value, dict) and isinstance(value.get("quote"), str) \
        and type(value.get("start")) is int


def unjudged(data):
    """The candidate pairs the index says nobody judged, or None when it does
    not say: an index written before claim.py kept them (2026-10-04), or one
    whose record of them cannot be read."""
    pairs = data.get("unjudged") if isinstance(data, dict) else None
    if not isinstance(pairs, list) or not all(
            isinstance(pair, dict) and a_claim(pair.get("a"))
            and a_claim(pair.get("b")) for pair in pairs):
        return None
    return pairs


def unread(data):
    """What a score over this index does not rest on, as the lines to print
    beside it, and [] when it rests on the whole of it.

    A defect is scored MISSED when no reported finding matches it. That is a
    miss of the tool's only where the tool looked: not where the defect's
    claim sits in a section the index was never answered on, or in one a
    pass failed on, and not where the pair that matches it was a candidate
    and its judging call failed. The index records all three. The score is
    left as it is, of what the index reports, and each is said beside it.

    An index written before 2026-10-04 does not say how many sections its
    claims came from, or which pairs went unjudged. That is said too: not
    known is not all.
    """
    said = []
    read = data.get("sections") if isinstance(data, dict) else None
    if not inventory.is_answers(read):
        said.append(UNSAID)
    elif not read["answered"]:
        said.append(f"NOTHING WAS READ: the index was answered on 0 of "
                    f"{read['of']} sections:\n  a MISSED below is a defect "
                    f"nobody looked for")
    elif read["not_read"]:
        said.append(f"the index was answered on {read['answered']} of "
                    f"{read['of']} sections:\n  a MISSED below may be a "
                    f"defect in one of the others")
    if inventory.is_answers(read) and read["short"]:
        said.append(f"{len(read['short'])} section(s) of the index answered "
                    f"in fewer than the {read['runs']} passes asked for:\n  "
                    f"a MISSED below may be a claim one more pass would have "
                    f"returned")
    pairs = unjudged(data)
    if pairs is None:
        said.append(UNJUDGED_UNSAID)
    elif pairs:
        said.append(f"{len(pairs)} candidate pair(s) could not be judged, "
                    f"the call failed:\n  a MISSED below may be one of them, "
                    f"and says so where a pair matches it")
    return said


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--claims", required=True)
    parser.add_argument("--ground-truth", default="ground-truth.yaml")
    parser.add_argument("--include-tension", action="store_true")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    truth = closure.load_yaml(os.path.join(project, args.ground_truth))
    data = json.load(open(os.path.join(project, args.claims)))

    wanted = {"contradiction"} | ({"tension"} if args.include_tension else set())
    findings = [f for f in data["findings"] if f["verdict"] in wanted]

    targets = [d for d in truth.get("defects", [])
               if d.get("class") in REACHABLE and d.get("expect") == "defect"]
    decoys = [d for d in truth.get("decoys", [])
              if d.get("class") in REACHABLE]

    # One finding satisfies at most one entry, and one entry is satisfied by at
    # most one finding. Without this a single report can claim several defects.
    found, missed, unscorable = [], [], []
    claimed = set()
    for entry in targets:
        if not entry.get("anchor") and not entry.get("lines"):
            unscorable.append(entry)
            continue
        hit = next((f for f in findings
                    if id(f) not in claimed and matches(f, entry)), None)
        if hit:
            found.append((entry, hit))
            claimed.add(id(hit))
        else:
            missed.append(entry)

    # Precision counts a finding as correct if it matches ANY planted defect,
    # not only one in the recall target set. Finding a real problem filed under
    # a class this tool was not built for is still finding a real problem.
    all_defects = [d for d in truth.get("defects", [])
                   if d.get("expect") == "defect"]
    false_positives = [f for f in findings if id(f) not in claimed
                       and not any(matches(f, d) for d in all_defects)]
    other_class = [f for f in findings if id(f) not in claimed
                   and any(matches(f, d) for d in all_defects)]
    decoy_hits = [f for f in false_positives
                  if any(matches(f, d) for d in decoys)]

    print(f"claim index: {len(data['claims'])} claims, "
          f"{len(findings)} reported finding(s)")
    scorable = len(targets) - len(unscorable)
    print(f"reachable ground truth: {scorable} scorable defect(s), "
          f"{len(decoys)} decoy(s)"
          + (f", {len(unscorable)} not auto-scorable" if unscorable else "")
          + "\n")

    correct = len(found) + len(other_class)
    recall = len(found) / scorable if scorable else 0.0
    precision = correct / len(findings) if findings else 0.0
    for line in unread(data):
        # The score is of the index, not of the document.
        print(f"  {line}")
    print(f"  recall     {len(found)}/{scorable}   {recall:6.1%}")
    print(f"  precision  {correct}/{len(findings)}   {precision:6.1%}"
          if findings else "  precision  n/a (nothing reported)")
    if decoy_hits:
        print(f"  decoys reported as defects: {len(decoy_hits)}")

    for entry, hit in found:
        print(f"\nFOUND    {entry['id']}  {entry['title'][:60]}")
        print(f"         {hit['a']['start']} + {hit['b']['start']}  "
              f"({hit['confidence']} confidence)")
    for entry in unscorable:
        print(f"\nUNSCORED {entry['id']}  {entry['title'][:56]}"
              f"\n         no anchor or line span in the ground truth")
    not_judged = unjudged(data) or []
    for entry in missed:
        print(f"\nMISSED   {entry['id']}  {entry['title'][:60]}")
        if any(matches(pair, entry) for pair in not_judged):
            print(f"         {NOT_JUDGED}")
    for finding in other_class:
        print(f"\nOTHER    {finding['subject'][:40]}  "
              f"{finding['a']['start']} + {finding['b']['start']}"
              f"  (real defect, outside the recall target set)")
    for finding in false_positives:
        print(f"\nEXTRA    {finding['subject'][:40]}  "
              f"{finding['a']['start']} + {finding['b']['start']}")
        print(f"         {finding['reason'][:100]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
