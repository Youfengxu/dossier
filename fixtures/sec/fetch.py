#!/usr/bin/env python3
"""Fetch SEC comment-letter exchanges where the staff re-adjudicated its own comments.

    export DOSSIER_SEC_UA="Your Name your@email"     # required, see below
    ./fetch.py                  # default slice
    ./fetch.py --letters 12     # a wider slice
    ./fetch.py --check          # verify the pinned digest, write nothing

WHY THIS CORPUS. A regulator raises numbered comments; a company replies claiming
it addressed them; the regulator then writes AGAIN and says, comment by comment,
which responses were good enough. That third letter is the label, produced by the
party who raised the point, in a setting where the company has real money riding
on being judged compliant.

    We note your response to prior comment 7.                 -> accepted
    We note your response to prior comment 1, which we reissue. -> not accepted
    We note your response to prior comment 11 and re-issue in part. -> partial

THE FRAME DOES THE BALANCING, which is the useful discovery here. Across all
comment letters, genuine negatives run at a few percent — a corpus where
answering "addressed" to everything scores well. But restricting the frame to
letters that engage with PRIOR comments changes the base rate completely: within
that frame, measured over the default slice, roughly 30% of comments are reissued
in whole or in part. No oversampling, no reweighting, no thumb on the scale —
just asking the question of the documents where it was actually asked.

BOTH NUMBERS MATTER AND BOTH ARE REPORTED. ~30% within this frame; a few percent
across all comment letters. Quoting the first without the second would make a
tool look calibrated on a population it never saw.

WHAT THIS FIXTURE DOES NOT GIVE YOU. The first-round letter and the company's
reply are IDENTIFIED for every exchange — accession numbers are recorded so the
chain is traceable — but their text is not extracted. Document naming and layout
vary per filing, and roughly a third of what EDGAR files as a comment letter is
something else entirely (notices that a filing will not be reviewed, closing
letters), with no metadata distinguishing them. So this ships as obligation-
reference plus adjudication, and the full text of the other two legs is left to
whoever needs it, with the accessions to start from.

SEC ASKS AUTOMATED TRAFFIC TO IDENTIFY ITSELF, and the request is reasonable:
scripted access is explicitly permitted, undeclared scripted access is not. Set
DOSSIER_SEC_UA to a name and a contact address. There is deliberately no default
— a fixture that ships someone else's email and quietly sends it on your behalf
is worse than one that refuses to start.
"""

import argparse
import collections
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEARCH = "https://efts.sec.gov/LATEST/search-index?{}"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{:010d}.json"

# "reissue", "re-issue" and "reiterate" are all in use, and the hyphenated form
# is common. A pattern without the optional hyphen labels every re-issued comment
# as accepted — silently, and in the direction that flatters a tool. This was
# written without it first and produced exactly that.
REISSUE = re.compile(r"re-?issu\w*|reiterat\w*", re.I)
PARTIAL = re.compile(r"\bin part\b|with respect to|to the extent|partially", re.I)
MENTION = re.compile(r"[^.]*?prior comment[s]?\s+((?:\d+[\s,and&]*)+)[^.]*\.", re.I)
RANK = {"addressed": 0, "partial": 1, "not_addressed": 2}

PINNED = "47c2bb05579c3a7b"


def agent():
    ua = os.environ.get("DOSSIER_SEC_UA", "").strip()
    if not ua or "@" not in ua:
        sys.exit(
            "DOSSIER_SEC_UA is not set.\n\n"
            "The SEC permits scripted access and asks that it identify itself.\n"
            "Set a name and a contact address, for example:\n\n"
            '    export DOSSIER_SEC_UA="Jane Smith jane@example.org"\n\n'
            "No default is shipped on purpose: sending someone else's address on\n"
            "your behalf would be worse than refusing to start.")
    return ua


def get(url, ua, pause=0.2):
    request = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    time.sleep(pause)                      # SEC's ceiling is 10/s; this is well under
    return body.decode("utf-8", "replace")


def classify(text):
    """Label each prior comment by the sentence naming it, not by the mention.

    A number appearing is not a verdict. "We note your response to prior comment
    7." accepts it; the same opening followed by "which we reissue" does not.
    Where a comment is named more than once, the harshest verdict wins — staff
    acknowledge first and reissue later in the same letter.
    """
    found = {}
    for match in MENTION.finditer(text):
        sentence = " ".join(match.group(0).split())
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        if REISSUE.search(sentence):
            verdict = "partial" if PARTIAL.search(sentence) else "not_addressed"
        else:
            verdict = "addressed"
        for number in numbers:
            if number not in found or RANK[verdict] > RANK[found[number][0]]:
                found[number] = (verdict, sentence)
    return found


def chain_for(cik, before, ua):
    """The prior comment letter and the company's reply, by accession.

    Identified, not extracted. Pairing on metadata alone is unreliable — the
    submissions feed leaves fileNumber empty on CORRESP — so this records the
    nearest preceding filing of each type and says so, rather than asserting a
    pairing it has not verified.
    """
    try:
        feed = json.loads(get(SUBMISSIONS.format(int(cik)), ua))
    except Exception:
        return {}, {}
    recent = feed.get("filings", {}).get("recent", {})
    rows = list(zip(recent.get("form", []), recent.get("filingDate", []),
                    recent.get("accessionNumber", [])))
    prior = lambda form: next(((d, a) for f, d, a in rows
                               if f == form and d < before), (None, None))
    up_date, up_acc = prior("UPLOAD")
    co_date, co_acc = prior("CORRESP")
    return ({"date": up_date, "accession": up_acc},
            {"date": co_date, "accession": co_acc})


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--letters", type=int, default=8)
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-06-30")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    ua = agent()

    query = urllib.parse.urlencode({"q": '"we note your response to prior comment"',
                                    "forms": "UPLOAD",
                                    "startdt": args.start, "enddt": args.end})
    found = json.loads(get(SEARCH.format(query), ua))
    frame_total = found["hits"]["total"]["value"]
    print(f"frame: {frame_total} documents in {args.start}..{args.end} engage "
          f"with prior comments\n")

    rows, seen, letters = [], set(), 0
    for hit in found["hits"]["hits"]:
        if not hit["_id"].endswith(".txt"):
            continue                       # the .pdf twin of the same letter
        accession, document = hit["_id"].split(":")
        if accession in seen:
            continue
        seen.add(accession)
        source = hit["_source"]
        cik = source["ciks"][0].lstrip("0")
        try:
            text = get(ARCHIVE.format(cik=cik, acc=accession.replace("-", ""),
                                      doc=document), ua)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                # Do NOT swallow this. A rejected user-agent 403s every document
                # and the run then reports "nothing usable fetched", which reads
                # as an empty corpus rather than as a refused identity.
                sys.exit(
                    f"\nSEC refused the request (403) for {accession}.\n\n"
                    f"DOSSIER_SEC_UA is currently {ua!r}.\n"
                    f"The SEC wants a real name and a working contact address —\n"
                    f'for example "Jane Smith jane@example.org". A placeholder\n'
                    f"passes the format check here and is refused there.")
            print(f"  {accession}: HTTP {exc.code}", file=sys.stderr)
            continue
        except Exception as exc:
            print(f"  {accession}: {type(exc).__name__}", file=sys.stderr)
            continue
        labels = classify(text)
        if not labels:
            continue                       # matched the phrase, adjudicates nothing
        upload, corresp = chain_for(cik, source["file_date"], ua)
        letters += 1
        for number, (verdict, sentence) in sorted(labels.items()):
            rows.append({
                "id": f"{accession}-{number}",
                "cik": cik,
                "round2_accession": accession,
                "round2_date": source["file_date"],
                "comment_number": number,
                "staff_sentence": sentence,
                "verdict": verdict,
                "round1_accession": upload.get("accession") or "",
                "round1_date": upload.get("date") or "",
                "response_accession": corresp.get("accession") or "",
                "response_date": corresp.get("date") or "",
            })
        spread = collections.Counter(v for v, _ in labels.values())
        print(f"  {accession}  {dict(spread)}")
        if letters >= args.letters:
            break

    if not rows:
        sys.exit("nothing usable fetched")

    material = "\n".join(f"{r['id']}|{r['verdict']}" for r in sorted(
        rows, key=lambda r: r["id"]))
    got = hashlib.sha256(material.encode()).hexdigest()
    if args.update:
        print(f"\ndigest: {got[:16]}")
        return 0
    if not got.startswith(PINNED):
        print(f"\nDIGEST MISMATCH\n  pinned {PINNED}\n  now    {got[:16]}\n"
              f"\nSearch ranking moves, so a different slice is expected rather than\n"
              f"alarming. Re-pin with --update once you have read the new labels.",
              file=sys.stderr)
        if args.check:
            return 1
    elif args.check:
        print(f"\ndigest {got[:16]} matches the pin")
        return 0

    with open(os.path.join(HERE, "comments.csv"), "w", newline="",
              encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    spread = collections.Counter(r["verdict"] for r in rows)
    negative = spread["not_addressed"] + spread["partial"]
    share = 100 * negative / len(rows)
    with open(os.path.join(HERE, "labels.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "digest": got[:16],
            "frame": "UPLOAD letters engaging with prior comments",
            "frame_documents": frame_total,
            "letters_used": letters,
            "comments": len(rows),
            "spread": dict(spread),
            "negative_share_within_frame": round(share, 1),
            "caution": ("This share holds WITHIN the multi-round frame. Across all "
                        "comment letters the negative rate is a few percent, so a "
                        "result quoted from this fixture must be read against the "
                        "population it did not sample."),
        }, handle, indent=1)

    print(f"\n  {letters} letters, {len(rows)} adjudicated comments")
    for verdict, count in spread.most_common():
        print(f"    {verdict:<16} {count}")
    print(f"\n  negative share WITHIN this frame: {share:.0f}%")
    print(f"  across all comment letters it is a few percent — quote both")
    print("\nwrote comments.csv and labels.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
