#!/usr/bin/env python3
"""Fetch SEC comment-letter exchanges where the staff came back to its own comments.

    export DOSSIER_SEC_UA="Your Name your@email"     # required, see below
    ./fetch.py                  # default slice
    ./fetch.py --letters 12     # a wider slice
    ./fetch.py --check          # verify the pinned digest, write nothing
    ./fetch.py --update         # print the digest, to re-pin once the labels are read

WHY THIS CORPUS. A regulator raises numbered comments; a company replies claiming
it addressed them; the regulator then writes AGAIN, citing its earlier comments
by number and saying what it makes of the answers. That second letter is the
label, produced by the party who raised the point, in a setting where the company
has real money riding on being judged compliant.

    We note your response to prior comment 1, which we reissue.      -> not_addressed
    We note your response to prior comment 11 and re-issue in part.  -> partial
    We note your response to prior comment 7. Please revise ...      -> addressed

WHAT "ADDRESSED" DOES NOT MEAN. The staff do not write back about an answer they
accept: the comment is not mentioned again, and it is not a row here. Every row
is a comment the staff CAME BACK TO, and "addressed" is the mildest way of
coming back — cited and not reissued, nearly always with a further request
behind it. The three labels grade how the staff returned to a comment. They do
not say which answers satisfied it, and README.md gives the counts.

THE FRAME DOES THE BALANCING, which is the useful discovery here. Across all
comment letters, genuine negatives run at a few percent — a corpus where
answering "addressed" to everything scores well. But restricting the frame to
letters that engage with PRIOR comments changes the base rate completely: within
that frame, measured over the default slice, about a third of the comments cited
again are reissued in whole or in part. No oversampling, no reweighting, no thumb
on the scale — just asking the question of the documents where it was actually
asked.

BOTH NUMBERS MATTER AND BOTH ARE REPORTED. About a third within this frame; a few
percent across all comment letters. Quoting the first without the second would
make a tool look calibrated on a population it never saw.

WHAT THIS FIXTURE DOES NOT GIVE YOU. The first-round letter and the company's
reply are IDENTIFIED — accession numbers are recorded so the chain is traceable —
but their text is not extracted: document naming and layout vary per filing. The
first-round letter is the one the second letter names by date, confirmed by
reading the date printed on it; the reply is the first correspondence filed
between the two, found by form and date and not read. Where either is not found
the field is left empty, not guessed. So this ships as obligation-reference plus
adjudication, and the full text of the other two legs is left to whoever needs
it, with the accessions to start from.

SEC ASKS AUTOMATED TRAFFIC TO IDENTIFY ITSELF, and the request is reasonable:
scripted access is explicitly permitted, undeclared scripted access is not. Set
DOSSIER_SEC_UA to a name and a contact address. There is deliberately no default
— a fixture that ships someone else's email and quietly sends it on your behalf
is worse than one that refuses to start.
"""

import argparse
import collections
import csv
import datetime
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
EXTRACT = "filename2.txt"       # the text EDGAR extracts from a staff letter's PDF

MONTHS = ("January February March April May June July August September "
          "October November December").split()
MONTH = "|".join(MONTHS)
DATE = re.compile(r"\b(%s)\s+(\d{1,2}),?\s+(\d{4})\b" % MONTH)

# "reissue", "re-issue" and "reiterate" are all in use, and the hyphenated form
# is common. A pattern without the optional hyphen labels every re-issued comment
# as accepted — silently, and in the direction that flatters a tool. This was
# written without it first and produced exactly that. The optional "s" is the
# same failure a second time: one letter in the default slice says "We resissue
# prior comment 30 in full", and the pattern read that as accepted too.
REISSUE = re.compile(r"\bre-?s?issu\w*|\breiterat\w*", re.I)
PARTIAL = re.compile(
    r"\bin part\b|\bpartially\b|\bwith respect to\b|\bto the extent\b"
    r"|\bportions?\b|\bbullets?\b|\bremaining\b"
    r"|\b(?:first|second|third|last|final) (?:sentence|paragraph)\b", re.I)
WHOLE = re.compile(r"\bin full\b|\bin (?:its|their) entirety\b", re.I)

# A comment number, but not the head of "1,802,444 shares", "10%" or "2023". Some
# letters spell it: "We note your response to prior comment five and reissue it in
# part." Read with digits only, that letter has a reissue and no comment for it.
WORDS = ("one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()
NUM = r"(?:\d{1,3}(?![\d%%]|,\d{3}|\.\d)|(?:%s)\b)" % "|".join(WORDS)
# A comment of the earlier letter is cited as "prior comment 7", and now and then
# as "your response to comment 7": 6 of the 375 rows of a 40-letter slice. It
# carries reissues like the first: "We note your response to comment 4 and
# reissue it in part."
CITE = re.compile(
    r"\b(?:prior comments?|responses? to comments?)\s+(?:(?:nos?\.?|numbers?)\s+)?"
    r"(%(n)s(?:(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or|through)\s+"
    r"|\s*&\s*|\s*[-–]\s*)%(n)s)*)" % {"n": NUM}, re.I)
# "prior comment 15 of our letter dated March 14, 2023" names a comment of that
# letter, which is the first-round letter only if the dates agree.
ANOTHER = re.compile(
    r"\s*,?\s*(?:of|in|from)\s+(?:our|the)\s+"
    r"(?:(?:prior\s+|previous\s+)?(?:comment\s+)?letter\s+(?:dated|of)\s+)?"
    r"(%s)\s+(\d{1,2}),?\s+(\d{4})" % MONTH)
# "Unless we note otherwise, any references to prior comments are to comments in
# our November 29, 2023 letter."
NAMED = re.compile(
    r"references to prior comments are to comments in our\s+"
    r"(?:letter\s+dated\s+)?(%s)\s+(\d{1,2}),?\s+(\d{4})" % MONTH, re.I)
# A list number, as the extract sets one: "18.      We acknowledge". A wrapped
# line of running text starts "18. Please revise" — one space — and so does a
# heading taken from the filing, "3. Summary of significant accounting policies".
NUMBER = re.compile(r"^\s{0,12}(\d{1,3})\.(?:\s{2,}|\s*$)")
# In a sentence that names no comment, only the staff reissuing a comment counts:
# "we reissue", "we reiterate that", "the comment is reissued". "Tell us why the
# audit report was reissued" is about something else.
OURS = re.compile(r"\bwe\b[^.]*?\b(?:re-?s?issu(?:e|ing)|reiterat(?:e|ing))\b",
                  re.I)
CLOSING = re.compile(r"We remind you that|Refer to Rules 460 and 461"
                     r"|Please contact\b|Sincerely,|We urge all persons")
TITLE = re.compile(r"\b(?:Mr|Ms|Mrs|Messrs|Dr)\.$")
RANK = {"addressed": 0, "partial": 1, "not_addressed": 2}
# What a run sees and does not label. Each is counted in labels.json and printed
# with its sentence, so that "not labelled" never reads as "not there".
ELSEWHERE = "citation of another letter's comment"
NOT_OURS = "sentence with the word, not read as the staff reissuing a comment"
NOTHING_CITED = "reissue with no prior comment cited beside it"
REPLY = ("CORRESP", "DRSLTR")   # a reply to comments on a draft is a DRSLTR

PINNED = "50dd7efde5fa3081"


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
    for wait in (2, 4, 8, None):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            break
        except urllib.error.HTTPError as exc:
            # EDGAR's search answers 500 now and then and is fine a moment
            # later. Left alone, one of those inside the loop below drops a
            # letter and the slice moves for no reason of the SEC's.
            if exc.code not in (429, 500, 502, 503) or wait is None:
                raise
            time.sleep(wait)
    time.sleep(pause)                      # SEC's ceiling is 10/s; this is well under
    return body.decode("utf-8", "replace")


def iso(match):
    """A matched "March 13, 2024" as 2024-03-13."""
    return "%s-%02d-%02d" % (match.group(3), MONTHS.index(match.group(1)) + 1,
                             int(match.group(2)))


def squash(text):
    return " ".join(text.split())


def letter(raw):
    """The letter inside the <TEXT> wrapper EDGAR puts round an extract."""
    found = re.search(r"<TEXT>\n?(.*?)</TEXT>", raw, re.S)
    return found.group(1) if found else raw


def dated(text):
    """The date printed at the head of a staff letter."""
    found = DATE.search(squash(text))
    return iso(found) if found else None


def named(text):
    """The date of the letter this one says its "prior comments" belong to."""
    found = NAMED.search(squash(text))
    return iso(found) if found else None


def without_headers(text):
    """The letter without the header the extract prints at every page break.

    Two shapes, both from the staff's template. With its field names left in:

         Jane Doe
        FirstName  LastNameJane Doe
        Example Corp.
        Comapany
        May  8, 2024NameExample Corp.
        May 8,
        Page 2 2024 Page 2
        FirstName LastName

    and plain: the addressee, the company, the date and "Page 4" on four lines.
    A header lands wherever the page ends, mid-sentence included, and its
    "Corp." then reads as a full stop. So a block goes whole when every line of
    it is header, which is when no line has a word in lower case. On a letter's
    last page the extract weaves the header through the text; there only the
    lines carrying a field name go, and the text between them stays.
    """
    lines = text.split("\n")
    greeting = re.search(r"^\s*Dear (.+?):", text, re.M)
    addressee = squash(greeting.group(1)) if greeting else None
    drop = set()
    for i, line in enumerate(lines):
        if re.match(r"\s*FirstName\s+LastName\S", line):
            end = next((j for j in range(i + 1, min(i + 13, len(lines)))
                        if re.match(r"\s*FirstName\s+LastName\s*$", lines[j])),
                       None)
            if end and not any(re.search(r"(?<![A-Za-z])[a-z]{2,}", inside)
                               for inside in lines[i + 1:end]):
                drop.update(range(i, end + 1))
                if i and squash(lines[i - 1]) == addressee:
                    drop.add(i - 1)
        elif (re.match(r"\s*Page \d+\s*$", line) and i >= 3
              and DATE.fullmatch(squash(lines[i - 1]))):
            drop.update((i - 1, i))
            if squash(lines[i - 3]) == addressee:
                drop.update((i - 3, i - 2))
    return "\n".join(line for i, line in enumerate(lines) if i not in drop
                     and not re.search(r"FirstName|LastName|Comapany", line))


def outline(text):
    """{number: text} for the letter's numbered comments, or None.

    A comment opens on a line that starts with the next number in sequence, so
    a wrapped line reading "33. Please revise or advise." (a page number) opens
    nothing. Reading in sequence has a cost: one number the extract mangles
    would fold every later comment into the one before it, and a reissue in any
    of them would then be laid on prior comments it never mentioned. So when
    the number after a missing one does turn up, or there is no comment 1, the
    answer is None, and the caller sets the letter aside instead of labelling
    it.
    """
    lines = without_headers(text).split("\n")
    starts, want = [], 1
    for i, line in enumerate(lines):
        found = NUMBER.match(line)
        if found and int(found.group(1)) == want:
            starts.append(i)
            want += 1
    if not starts or any(found and int(found.group(1)) == want + 1
                         for found in map(NUMBER.match, lines[starts[-1] + 1:])):
        return None
    bodies = {}
    for k, i in enumerate(starts):
        stop = starts[k + 1] if k + 1 < len(starts) else len(lines)
        bodies[k + 1] = squash(" ".join([NUMBER.sub("", lines[i])]
                                        + lines[i + 1:stop]))
    last = bodies[len(bodies)]
    closing = CLOSING.search(last)
    if closing and not (CITE.search(last, closing.start())
                        or REISSUE.search(last, closing.start())):
        bodies[len(bodies)] = last[:closing.start()].rstrip()
    return bodies


def sentences(text):
    """Split at a full stop before a capital. "Mr. Wong" is not one."""
    out = []
    for piece in re.split(r'(?<=[.?!])\s+(?=[A-Z"“(])', text):
        if out and TITLE.search(out[-1]):
            out[-1] += " " + piece
        else:
            out.append(piece)
    return out


def numbers(group):
    """The comment numbers in "3, 4 and 7", "5 through 8" or "five and seven"."""
    out, previous, span = [], None, False
    for token in re.findall(r"\d+|through|[-–]|%s" % "|".join(reversed(WORDS)),
                            group.lower()):
        if token in ("through", "-", "–"):
            span = True
            continue
        number = int(token) if token.isdigit() else WORDS.index(token) + 1
        if span and previous is not None and previous < number <= previous + 30:
            out.extend(range(previous + 1, number + 1))
        else:
            out.append(number)
        previous, span = number, False
    return out


def cites(sentence, first):
    """(the first-round comments a sentence cites, whether it cites another
    letter's). "Prior comment 15 of our letter dated March 14, 2023" is a
    comment of the March letter, and counts here only if that is the letter
    this one names."""
    here, elsewhere = [], False
    for found in CITE.finditer(sentence):
        other = ANOTHER.match(sentence, found.end())
        if other and iso(other) != first:
            elsewhere = True
        else:
            here.extend(numbers(found.group(1)))
    return here, elsewhere


def ruling(body, first):
    """({prior comment: (verdict, the staff's words)}, notes) for one numbered
    comment.

    A cited comment is "addressed" until a sentence of the same numbered
    comment reissues it. That sentence need not be the one that cites it:

        We note your response to prior comment 24. We reissue the first bullet
        of the prior comment in part.

    A sentence that reissues and names comments reissues those. One that names
    none, and is the staff speaking of a comment, reissues every comment cited
    around it. One that names only another letter's comment reissues nothing
    here.

    The notes are what was seen and not turned into a label, as (what,
    sentence): a reader can check each, and none is counted as anything.
    """
    parts = sentences(body)
    cited = [cites(part, first) for part in parts]
    at = {}
    for i, (here, _) in enumerate(cited):
        for number in here:
            at.setdefault(number, i)
    out = {number: ("addressed", parts[i]) for number, i in at.items()}
    notes = [(ELSEWHERE, part) for part, (_, other) in zip(parts, cited) if other]
    for i, (here, elsewhere) in enumerate(cited):
        if not REISSUE.search(parts[i]) or (elsewhere and not here):
            continue
        if not here:
            ours = OURS.search(parts[i]) or re.search(r"\bcomments?\b", parts[i], re.I)
            if ours and not at:
                notes.append((NOTHING_CITED, parts[i]))
            elif at and not ours:
                notes.append((NOT_OURS, parts[i]))
            if not (ours and at):
                continue
        in_part = PARTIAL.search(parts[i]) and not WHOLE.search(parts[i])
        verdict = "partial" if in_part else "not_addressed"
        for number in here or sorted(at):
            if RANK[verdict] > RANK[out[number][0]]:
                out[number] = (verdict, quote(parts, at[number], i))
    return out, notes


def quote(parts, cited, reissued):
    """The sentence that cites a comment and the one that reissues it, in the
    order the letter has them, with "[...]" for anything left out between."""
    low, high = sorted((cited, reissued))
    if low == high:
        return parts[low]
    return parts[low] + (" " if high == low + 1 else " [...] ") + parts[high]


def classify(text):
    """Label each prior comment by the whole numbered comment that cites it.

    A number appearing is not a verdict, and neither is the sentence it appears
    in. This read one sentence at a time first, and called a comment accepted
    when the reissue came in the next one — the same direction as the pattern
    without its hyphen, and found the same way, by reading the letters.

    Returns ({number: label}, notes), or (None, []) for a letter whose
    numbering cannot be followed. Where a comment is cited in more than one
    place, the harshest verdict wins — staff acknowledge first and reissue later
    in the same letter.
    """
    bodies = outline(text)
    if bodies is None:
        return None, []
    first = named(text)
    found, notes = {}, []
    for within, body in sorted(bodies.items()):
        labels, more = ruling(body, first)
        notes += more
        for number, (verdict, sentence) in labels.items():
            if number not in found or RANK[verdict] > RANK[found[number]["verdict"]]:
                found[number] = {"verdict": verdict, "staff_sentence": sentence,
                                 "round2_comment": within, "staff_comment": body}
    return found, notes


def chain_for(cik, first, before, ua):
    """The first-round letter and the company's reply: (upload, reply, why not).

    The second-round letter says which letter its "prior comments" belong to,
    by date, and that pairs them. Nearness does not. What EDGAR files as a staff
    letter can also be a closing notice, and pairing with the nearest one before
    this put a one-paragraph "We have completed our review" on one of the
    default slice's eight exchanges, in place of the letter three weeks earlier
    that raised the comments. So each candidate is fetched and read. It is the
    first-round letter if the date printed on it is the date named and it has
    numbered comments. The feed's date alone does not settle that, because
    EDGAR's filing date can trail the letter's by a day. A letter that names no
    date, or a date no comment letter in the feed carries, is left unpaired, not
    guessed, and a candidate that could not be fetched is reported as unread,
    not as absent.

    The reply is identified, not read: the first correspondence the company
    filed after the first-round letter and before this one.
    """
    if not first:
        return {}, {}, "it names no letter"
    try:
        feed = json.loads(get(SUBMISSIONS.format(int(cik)), ua))
    except Exception as exc:
        return {}, {}, f"the filing index was not read ({type(exc).__name__})"
    recent = feed.get("filings", {}).get("recent", {})
    rows = list(zip(recent.get("form", []), recent.get("filingDate", []),
                    recent.get("accessionNumber", [])))
    day = datetime.date.fromisoformat
    try:
        near = sorted((abs((day(d) - day(first)).days), d, a)
                      for f, d, a in rows if f == "UPLOAD" and d < before)
    except ValueError:
        return {}, {}, f"it names {first}, which is not a date"
    unread = 0
    for gap, date, accession in near:
        if gap > 10:
            break
        try:
            text = letter(get(ARCHIVE.format(
                cik=cik, acc=accession.replace("-", ""), doc=EXTRACT), ua))
        except Exception:
            unread += 1
            continue
        comments = outline(text)
        if dated(text) != first or not comments:
            continue                       # another day's letter, or a notice
        replies = sorted((d, a) for f, d, a in rows
                         if f in REPLY and date <= d < before)
        reply = replies[0] if replies else (None, None)
        return ({"date": date, "accession": accession, "comments": len(comments)},
                {"date": reply[0], "accession": reply[1]}, "")
    if unread:
        return {}, {}, (f"{unread} staff letter(s) filed near {first} could "
                        f"not be read")
    return {}, {}, f"no comment letter dated {first} in its recent filings"


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
    aside = collections.Counter()          # what was not labelled, and why
    paired, raised, cited_again = 0, 0, 0
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
            text = letter(get(ARCHIVE.format(cik=cik, acc=accession.replace("-", ""),
                                             doc=document), ua))
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
            aside["letter not fetched"] += 1
            continue
        except Exception as exc:
            print(f"  {accession}: {type(exc).__name__}", file=sys.stderr)
            aside["letter not fetched"] += 1
            continue
        labels, notes = classify(text)
        if labels is None:
            # Not labelled, and not counted as anything: see outline().
            print(f"  {accession}  set aside — its comment numbering could "
                  f"not be followed")
            aside["letter whose numbering could not be followed"] += 1
            continue
        upload, corresp, unpaired = {}, {}, ""
        if labels:
            upload, corresp, unpaired = chain_for(cik, named(text),
                                                  source["file_date"], ua)
        # A cited number the first-round letter does not have is a misread
        # number or a wrong pairing. Either way it is not a label.
        beyond = [n for n in labels if upload and n > upload["comments"]]
        for number in beyond:
            del labels[number]
            notes.append(("cited number the first-round letter does not have",
                          f"prior comment {number}"))
        aside.update(what for what, _ in notes)
        if not labels:
            # The search matched the phrase and the reader found no comment to
            # label. That is a letter not read, and it is said, not skipped.
            print(f"  {accession}  set aside — no prior comment found in it")
            aside["letter in which no prior comment was found"] += 1
            for what, sentence in notes:
                print(f"      {what}: {sentence[:100]}")
            continue
        letters += 1
        if upload:
            paired += 1
            raised += upload["comments"]
            cited_again += len(labels)
        for number, label in sorted(labels.items()):
            rows.append({
                "id": f"{accession}-{number}",
                "cik": cik,
                "round2_accession": accession,
                "round2_date": source["file_date"],
                "comment_number": number,
                "staff_sentence": label["staff_sentence"],
                "verdict": label["verdict"],
                "round1_accession": upload.get("accession") or "",
                "round1_date": upload.get("date") or "",
                "response_accession": corresp.get("accession") or "",
                "response_date": corresp.get("date") or "",
                "round2_comment": label["round2_comment"],
                "staff_comment": label["staff_comment"],
            })
        spread = collections.Counter(l["verdict"] for l in labels.values())
        print(f"  {accession}  {dict(spread)}")
        if unpaired:
            print(f"      first-round letter not paired: {unpaired}")
        else:
            print(f"      follows {upload['accession']}, which raised "
                  f"{upload['comments']} comments; reply "
                  f"{corresp['accession'] or 'not found between the two letters'}")
        for what, sentence in notes:
            print(f"      {what}: {sentence[:100]}")
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
    aside = {what: count for what, count in sorted(aside.items()) if count}
    with open(os.path.join(HERE, "labels.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "digest": got[:16],
            "frame": "UPLOAD letters engaging with prior comments",
            "frame_documents": frame_total,
            "letters_examined": len(seen),
            "letters_used": letters,
            "comments": len(rows),
            "spread": dict(spread),
            "negative_share_within_frame": round(share, 1),
            "first_round": {
                "letters_paired": paired,
                "letters_not_paired": letters - paired,
                "comments_raised": raised,
                "of_which_cited_again": cited_again,
                "note": ("Counted over the paired letters. A row is a comment "
                         "the staff cited again; one it let go is not a row."),
            },
            "not_labelled": aside,
            "caution": ("This share holds WITHIN the multi-round frame. Across all "
                        "comment letters the negative rate is a few percent, so a "
                        "result quoted from this fixture must be read against the "
                        "population it did not sample."),
        }, handle, indent=1)

    print(f"\n  {letters} letters of {len(seen)} examined, {len(rows)} "
          f"adjudicated comments")
    for verdict in sorted(spread, key=RANK.get):
        print(f"    {verdict:<16} {spread[verdict]}")
    print(f"\n  negative share WITHIN this frame: {share:.0f}%")
    print(f"  across all comment letters it is a few percent — quote both")
    print(f"\n  first-round letter paired for {paired} of {letters} letters; "
          f"those raised {raised}\n  comments, of which {cited_again} are "
          f"cited again and are rows here")
    for what, count in aside.items():
        print(f"  not labelled: {count} × {what}")
    print("\nwrote comments.csv and labels.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
