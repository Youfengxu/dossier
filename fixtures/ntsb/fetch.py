#!/usr/bin/env python3
"""Fetch one NTSB safety-recommendation letter and its adjudicated outcomes.

    ./fetch.py                 # the default slice: 25 recommendations, one letter
    ./fetch.py --check         # verify the pinned digest without rewriting
    ./fetch.py --update        # print the digest, to re-pin after an upstream change

WHY THIS CORPUS. Every other fixture here is requirements-versus-deliverable. This
one is the other shape: an obligation set, a party claiming they satisfied it, and
a THIRD PARTY ruling on whether the claim was good enough. The ruling is the part
that is hard to find in public and impossible to fake.

    obligation    the recommendation text, issued to a named addressee
    claim         the addressee's own correspondence saying what they did
    adjudication  NTSB's status for that addressee — acceptable, unacceptable,
                  or still open with the response judged inadequate

The default slice is deliberate. All 25 recommendations come from ONE issuance
letter to ONE addressee, so positives and negatives share an author, a subject
and a decade. A corpus that draws its two classes from different sources lets a
tool separate them on register rather than on substance, and it will — that is
cheaper than reading.

CLASS BALANCE, WHICH IS THE REASON TO PREFER THIS SLICE. Measured over the 25:
11 map to not_addressed and 13 to addressed, with one genuinely ambiguous. Most
compliance corpora run 5-10% negative, where answering "addressed" to everything
scores well and measures nothing.

ONE HONEST WEAKNESS, stated because the numbers above get quoted. There is no
revised document. NTSB tracks correspondence and the artefacts it cites, not a
redlined text, so this fixture exercises "does the claim hold up" and NOT "what
changed between two drafts". For that, use a fixture with two revisions.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://data.ntsb.gov/carol-main-public/api/Query/GetSrRecord/{}"

# A browser user-agent is required; the API returns nothing useful without one.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Colgan Air 3407, issued to the FAA as A-10-010 through A-10-034.
SLICE = [f"A-10-{n:03d}" for n in range(10, 35)]

# NTSB's status vocabulary mapped onto the review scale. The three "acceptable"
# variants all mean the addressee satisfied the recommendation — by the method
# asked for, by another route, or beyond it — so all three are `addressed`.
# `Closed - Reconsidered` means NTSB withdrew or reframed the recommendation
# rather than judging the response, so it is NOT a verdict on the claim and is
# excluded from the labelled set rather than being forced onto the scale.
VERDICT = {
    "Closed - Acceptable Action": "addressed",
    "Closed - Acceptable Alternate Action": "addressed",
    "Closed - Exceeds Recommended Action": "addressed",
    "Closed - Unacceptable Action": "not_addressed",
    "Open - Unacceptable Response": "not_addressed",
    "Closed - Reconsidered": None,
    "Closed - No Longer Applicable": None,
    "Open - Await Response": None,
    "Open - Initial Response Received": None,
    "Open - Acceptable Response": None,
}

PINNED = "2f56013d7aa3922f"          # first 16 of the sha256 over the slice


def fetch(sid, pause=0.7):
    request = urllib.request.Request(
        API.format(sid), headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        body = json.load(response)
    time.sleep(pause)
    return body


def status_names(record):
    for group in record.get("Lookups") or []:
        if group.get("Column") == "Status":
            return {o["Value"]: o["DisplayText"] for o in group["Options"]}
    return {}


def latest_claim(addressee):
    """The addressee's most recent word on what it did.

    Correspondence FROM the NTSB is adjudication, not claim, and including it
    would hand the answer to anything reading this field.
    """
    theirs = [c for c in (addressee.get("Correspondence") or [])
              if not c.get("IsFromNtsb") and (c.get("ResponseSummary") or "").strip()]
    theirs.sort(key=lambda c: c.get("CorrespondenceDate") or "")
    return theirs[-1] if theirs else None


def collect(ids, quiet=False):
    rows, raw = [], {}
    for sid in ids:
        try:
            record = fetch(sid)
        except Exception as exc:
            print(f"  {sid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        raw[sid] = record
        names = status_names(record)
        for addressee in record.get("Addressees") or []:
            status = names.get(str(addressee.get("Status")), "")
            claim = latest_claim(addressee)
            rows.append({
                "id": sid,
                "addressee": addressee.get("AddressAcronym") or addressee.get("AddresseeName"),
                "obligation": " ".join((record.get("Subject") or "").split()),
                "claim": " ".join((claim.get("ResponseSummary") or "").split()) if claim else "",
                "claim_date": (claim or {}).get("CorrespondenceDate", "")[:10],
                "claims_total": sum(1 for c in (addressee.get("Correspondence") or [])
                                    if not c.get("IsFromNtsb")),
                "ntsb_status": status,
                "verdict": VERDICT.get(status, None),
            })
        if not quiet:
            print(f"  {sid}: {rows[-1]['ntsb_status']}")
    return rows, raw


def digest(raw):
    """Hash over the fields we depend on, not the whole payload.

    The full record carries volatile decoration; pinning it would fail on a
    cosmetic upstream edit and teach whoever hits it to ignore the check.
    """
    material = []
    for sid in sorted(raw):
        record = raw[sid]
        names = status_names(record)
        for addressee in record.get("Addressees") or []:
            material.append(f"{sid}|{addressee.get('AddressAcronym')}|"
                            f"{names.get(str(addressee.get('Status')), '')}")
    return hashlib.sha256("\n".join(material).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="verify the pinned digest, write nothing")
    parser.add_argument("--update", action="store_true",
                        help="print the digest so it can be re-pinned")
    args = parser.parse_args()

    print(f"fetching {len(SLICE)} recommendations from one issuance letter\n")
    rows, raw = collect(SLICE, quiet=args.check)
    if not rows:
        sys.exit("nothing fetched")

    got = digest(raw)
    if args.update:
        print(f"\ndigest: {got[:16]}")
        return 0
    if not got.startswith(PINNED):
        print(f"\nDIGEST MISMATCH — adjudications upstream have changed."
              f"\n  pinned {PINNED}\n  now    {got[:16]}"
              f"\n\nThat is the check working, not a bug: these statuses move as"
              f"\nrecommendations are reconsidered. Re-read the labels before"
              f"\ntrusting them, then re-pin with --update.", file=sys.stderr)
        if args.check:
            return 1
    else:
        print(f"\ndigest {got[:16]} matches the pin")
    if args.check:
        return 0

    labelled = [r for r in rows if r["verdict"]]
    import csv
    with open(os.path.join(HERE, "recommendations.csv"), "w", newline="",
              encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "id", "addressee", "obligation", "claim", "claim_date", "claims_total"])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in writer.fieldnames})
    with open(os.path.join(HERE, "labels.json"), "w", encoding="utf-8") as handle:
        json.dump({"digest": got[:16],
                   "source": "NTSB CAROL, GetSrRecord",
                   "labels": {r["id"]: {"verdict": r["verdict"],
                                        "ntsb_status": r["ntsb_status"]}
                              for r in labelled},
                   "excluded": {r["id"]: r["ntsb_status"]
                                for r in rows if not r["verdict"]}},
                  handle, indent=1)

    import collections
    spread = collections.Counter(r["verdict"] for r in labelled)
    print(f"\n  {len(rows)} recommendations, {len(labelled)} labelled")
    for verdict, count in spread.most_common():
        print(f"    {verdict:<16} {count}")
    excluded = [r for r in rows if not r["verdict"]]
    if excluded:
        print(f"    excluded         {len(excluded)}  "
              f"({', '.join(sorted({r['ntsb_status'] for r in excluded}))})")
    print("\nwrote recommendations.csv and labels.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
