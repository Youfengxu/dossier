#!/usr/bin/env python3
"""Build a slice enriched for "Closed — Acceptable Alternate Action".

    ./enrich.py --scan A-12-001:A-12-150     # fetch and cache a contiguous range
    ./enrich.py --rate                       # status distribution over everything cached
    ./enrich.py --build                      # write the enriched register + labels

WHY. The default fixture is one issuance letter, and it happens to carry four
alternate-action rows out of twenty-four. Four is enough to notice a blind spot and
nowhere near enough to choose a model against it: at n=4 a model scoring 2/4
against another's 1/4 is a coin flip, and screening six models would select noise.

A probe of 28 records sampled sparsely across four years returned **zero** in 51
labelled rows, so the Colgan letter is unrepresentative and the true rate is low.
That is the whole reason this scans rather than samples.

WHY CONTIGUOUS RANGES. Recommendations are issued in letters, and a letter's
recommendations share an addressee, a subject and a date. Scanning a contiguous
range therefore captures whole letters, which is what makes matched controls
possible: for every alternate-action row, `--build` takes its letter's other
adjudicated rows as controls. Positives and negatives then share an author and a
topic, so a model cannot separate them on register instead of substance — the
property the original fixture bought by using a single letter, kept here while
drawing from many.

POLITENESS. One request per recommendation, 0.4s apart, cached in raw/ so a rerun
costs nothing. The cache is the reason this is safe to interrupt: kill it, rerun it,
and it resumes.

ON THE STATUSES fetch.py DOES NOT MAP. Commit d97329e claimed
"Closed - Unacceptable Action - No Response Received" was being dropped for no
reason but an incomplete map. That was wrong, and measuring it is what showed it:
**32 of those 39 rows carry no claim text at all**, median zero characters. The
addressee never responded, so there is no claim to judge and no way to be right or
wrong about one. Excluding them is correct.

"Open - Acceptable Response" is the genuinely open question — 24 rows, every one
carrying a claim, median 1,221 characters, and NTSB has judged the response
acceptable without closing the recommendation. That is real labelled data being
left on the floor. It is a weaker label than a closed one, and it should be added
deliberately or not at all, not by widening a dict without saying so.
"""

import argparse
import collections
import csv
import importlib.util
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
API = "https://data.ntsb.gov/carol-main-public/api/Query/GetSrRecord/{}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TARGET = "Closed - Acceptable Alternate Action"


def base():
    """fetch.py's status map and verdict vocabulary, not a second copy."""
    spec = importlib.util.spec_from_file_location("fetch", os.path.join(HERE, "fetch.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get(srid, pause=0.4):
    path = os.path.join(RAW, f"{srid}.json")
    if os.path.exists(path):
        return json.load(open(path))
    request = urllib.request.Request(API.format(srid), headers={"User-Agent": UA})
    try:
        record = json.load(urllib.request.urlopen(request, timeout=45))
    except Exception:
        record = None                       # a gap in the numbering, cached as such
    os.makedirs(RAW, exist_ok=True)
    json.dump(record, open(path, "w"))
    time.sleep(pause)
    return record


def expand(spec):
    start, end = spec.split(":")
    prefix, year, first = start.split("-")
    last = int(end.split("-")[-1])
    return [f"{prefix}-{year}-{n:03d}" for n in range(int(first), last + 1)]


def rows(fetch):
    """Every adjudicated (recommendation, addressee) pair in the cache."""
    for name in sorted(os.listdir(RAW)):
        record = json.load(open(os.path.join(RAW, name)))
        if not record:
            continue
        names = fetch.status_names(record)
        letter = ((record.get("Notations") or [{}])[0].get("ReportNumber") or "?")
        for addressee in record.get("Addressees") or []:
            status = names.get(str(addressee.get("Status")), "")
            yield {"srid": record.get("SridCleaned") or record.get("Srid"),
                   "letter": letter, "status": status,
                   "verdict": fetch.VERDICT.get(status),
                   "addressee": addressee.get("AddressAcronym") or "",
                   "record": record, "raw_addressee": addressee}


CONTROLS_PER_POSITIVE = 2


def escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build(fetch):
    """Every alternate-action row, plus matched controls from its own letter.

    MATCHED, not random. A control drawn from another letter differs in addressee,
    subject and decade as well as in outcome, so a model could separate the classes
    on register instead of substance and score well having understood nothing. Taking
    controls from the positive's own letter holds all of that constant — the property
    the single-letter fixture had for free, reconstructed across many letters.

    Controls prefer a failure class. The metric this fixture exists to support is a
    PAIR — does the model recognise compliance by another route, AND does it still
    catch genuine failure — because a model that answers "addressed" to everything
    scores perfectly on the first half and is worthless.
    """
    by_letter = collections.defaultdict(list)
    for row in rows(fetch):
        if row["verdict"]:
            by_letter[row["letter"]].append(row)

    chosen, seen = [], set()
    for letter, group in sorted(by_letter.items()):
        positives = [r for r in group if r["status"] == TARGET]
        if not positives:
            continue
        pool = [r for r in group if r["status"] != TARGET]
        # Failures first: they are the half of the metric that stops a yes-man
        # from winning.
        pool.sort(key=lambda r: (r["verdict"] != "not_addressed", r["srid"]))
        wanted = CONTROLS_PER_POSITIVE * len(positives)
        for row in positives + pool[:wanted]:
            key = (row["srid"], row["addressee"])
            if key not in seen:
                seen.add(key)
                chosen.append(row)

    out = os.path.join(HERE, "enriched")
    os.makedirs(out, exist_ok=True)
    document, register, labels = [
        "# Addressee responses to safety recommendations", "",
        "Each section is the addressee's own account of what it did. Nothing here",
        "is a ruling on whether that was enough.", ""], [], {}
    for row in chosen:
        addressee = row["raw_addressee"]
        claim = fetch.latest_claim(addressee)
        text = " ".join((claim or {}).get("ResponseSummary", "").split())
        if not text:
            continue
        rid = f"{row['srid']}-{row['addressee']}" if row["addressee"] else row["srid"]
        obligation = " ".join((row["record"].get("Subject") or "").split())
        document += [f"## {rid}", ""] + [text, ""]
        register.append({"id": rid, "addressee": row["addressee"],
                         "obligation": obligation})
        labels[rid] = {"verdict": row["verdict"], "ntsb_status": row["status"],
                       "letter": row["letter"]}

    with open(os.path.join(out, "claims.md"), "w") as handle:
        handle.write("\n".join(document) + "\n")
    with open(os.path.join(out, "obligations.csv"), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "addressee", "obligation"])
        writer.writeheader(); writer.writerows(register)
    with open(os.path.join(out, "labels.json"), "w") as handle:
        json.dump({"source": "NTSB CAROL, GetSrRecord, matched-control enrichment",
                   "controls_per_positive": CONTROLS_PER_POSITIVE,
                   "labels": labels}, handle, indent=1)
    with open(os.path.join(out, "corpus.yaml"), "w") as handle:
        handle.write("# Generated by enrich.py --build.\n"
                     "documents:\n  - path: claims.md\n    slug: claims\n"
                     "    role: draft\n")

    spread = collections.Counter(v["ntsb_status"] for v in labels.values())
    print(f"\n  enriched/ written: {len(labels)} rows from "
          f"{len({v['letter'] for v in labels.values()})} letters")
    for status, n in spread.most_common():
        mark = "   <-- target" if status == TARGET else ""
        print(f"    {n:>3}  {status}{mark}")
    chars = sum(len(l) for l in document)
    print(f"  document: {chars:,} chars (~{chars // 4:,} tokens)")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scan", action="append", default=[],
                        metavar="A-12-001:A-12-150",
                        help="contiguous range to fetch and cache; repeatable")
    parser.add_argument("--rate", action="store_true",
                        help="status distribution across everything cached")
    parser.add_argument("--build", action="store_true",
                        help="write enriched/ — positives plus matched controls")
    parser.add_argument("--pause", type=float, default=0.4)
    args = parser.parse_args()
    if not (args.scan or args.rate or args.build):
        print(__doc__.strip())
        return 0

    fetch = base()
    for spec in args.scan:
        ids = expand(spec)
        fresh = [i for i in ids if not os.path.exists(os.path.join(RAW, f"{i}.json"))]
        print(f"  {spec}: {len(ids)} ids, {len(fresh)} not yet cached")
        for n, srid in enumerate(ids, 1):
            get(srid, args.pause)
            if n % 25 == 0:
                print(f"    {n}/{len(ids)}", flush=True)

    seen = collections.Counter()
    letters = collections.defaultdict(collections.Counter)
    for row in rows(fetch):
        seen[row["status"]] += 1
        letters[row["letter"]][row["status"]] += 1
    labelled = sum(n for s, n in seen.items() if fetch.VERDICT.get(s))
    target = seen[TARGET]
    cached = len(os.listdir(RAW)) if os.path.isdir(RAW) else 0

    print(f"\n  {cached} records cached, {sum(seen.values())} addressee rows, "
          f"{labelled} adjudicated")
    for status, n in seen.most_common():
        mark = "   <-- target" if status == TARGET else ""
        print(f"    {n:>4}  {status or '(blank)'}{mark}")
    if labelled:
        print(f"\n  alternate-action: {target}/{labelled} adjudicated "
              f"= {target / labelled:.1%}")
    usable = [l for l, c in letters.items() if c[TARGET]]
    print(f"  letters carrying at least one: {len(usable)}")
    if args.build:
        return build(fetch)
    if target < 30:
        print(f"\n  {target} is not yet enough to rank models on this class. Scan "
              f"another range;\n  the cache makes it resumable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
