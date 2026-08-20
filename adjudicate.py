#!/usr/bin/env python3
"""Did the new revision address each comment? Scoped to the section it names.

A comment matrix already carries the answer to the hardest question in
this whole toolkit: WHERE is this comment about. Every other tool in here works
that out from scratch — deriving distinctive terms per finding, checking them
against the corpus, discarding the ones too common or too rare to carry signal.
None of that is needed when the reviewer has written "§6.10" in the row.

So, per row:

  1. slice that section out of both revisions, by heading text
  2. identical  -> NOT ADDRESSED. No model. The text did not move, and no
     judgement is involved in saying so.
  3. changed    -> the model sees the COMMENT and the DIFF, a few hundred
     characters, which is the regime where absence precision measured 97%
     rather than the 83% seen over long inputs
  4. inconclusive -> escalate to the whole section in the new revision

    ./adjudicate.py --project . --xlsx matrix.xlsx --sheet "Comments" \
        --from deliverable-v1 --to deliverable-v2 --out out/adjudicated.xlsx

Writes a COPY of the matrix with two new columns. Never the original: it is
jointly agreed with the vendor, and a tool that edits it in place can destroy
the other side's work.

PRINTS STRUCTURE ONLY — headings, line ranges, verdicts. The document body goes
to the local model and to the output file, never to the console, so running this
does not put client text in front of whatever is reading the terminal.
"""

import argparse
import difflib
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Client, Embedder, LLMError, content_lines, cosine, load_doc                  # noqa: E402
import matrix as matrix_reader                              # noqa: E402
import writeback                                            # noqa: E402

PROMPT_VERSION = "adjudicate-1"

SYSTEM = """You judge whether a revision addressed one review comment.

You are given the comment, and what changed in the section the comment is about.
Reply with JSON only:
{"verdict":"addressed|partial|not_addressed|unclear","rationale":"...","quote":"..."}

- "addressed": the change does what the comment asked.
- "partial": some of it, and you can name what is still missing.
- "not_addressed": the change does not bear on the comment. Text moving is not
  the same as the point being answered — a reworded paragraph that still omits
  what was asked for is not_addressed.
- "unclear": you cannot tell from what you were shown.

"quote" is a verbatim span from the NEW text supporting the verdict, or "".
"rationale" is one or two sentences a reviewer could send to the vendor: say
what was asked, what changed, and why that does or does not settle it. Name the
specific thing still missing where there is one.

Be strict. The default question is "would the person who wrote this comment
consider it closed?", not "did anything happen here"."""


def validate(obj):
    if not isinstance(obj, dict):
        return "reply must be a JSON object"
    if obj.get("verdict") not in ("addressed", "partial", "not_addressed",
                                  "unclear"):
        return "'verdict' must be addressed, partial, not_addressed or unclear"
    if not isinstance(obj.get("rationale"), str) or not obj["rationale"].strip():
        return "'rationale' must be a non-empty string"
    if not isinstance(obj.get("quote"), str):
        return "'quote' must be a string, possibly empty"
    return None


HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)[.\s]\s*(\S.*)$")


def index_headings(lines):
    """number -> [(title, start, end)], body-first.

    A heading resolves twice in these documents: once in the table of contents
    and once where the section actually is. The TOC copy has the page number
    glued to the title ("Detailed package documentation67") and almost no body
    beneath it. Keeping both and preferring the largest is more robust than
    trying to detect the contents page itself, which is not always labelled.
    """
    marks = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if len(stripped) > 120:
            continue
        match = HEADING.match(stripped)
        if match:
            marks.append((match.group(1), match.group(2).strip(), number))

    # A section runs to the next heading of the SAME OR HIGHER level, not the
    # next heading of any level. Ending at the next mark made §13 stop the
    # instant §13.1 began — every section with subsections sliced to zero
    # characters, and the diff compared nothing to nothing.
    out = {}
    for position, (num, title, start) in enumerate(marks):
        depth = num.count(".")
        end = len(lines)
        for later in marks[position + 1:]:
            if later[0].count(".") <= depth:
                end = later[2] - 1
                break
        out.setdefault(num, []).append((title, start, end))
    return out


def normalise_title(text):
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\d+$", "", text).lower()).strip()


# A page number run onto the end of a heading: the mark of a contents entry.
TOC_TITLE = re.compile(r"[A-Za-z)\]]\s*\d{1,4}$")


def pick(index, number, want_title=None):
    """The real occurrence of a section: the one with the most body.

    Falls back to the parent when the exact subsection is gone. v7 collapsed 92
    two-level subsections to 18, so a comment on §8.10 has no anchor — but §8
    still exists and still contains whatever became of it. Coarser, and vastly
    better than declaring the comment unverifiable.
    """
    candidates = index.get(number)
    while not candidates and "." in number:
        number = number.rsplit(".", 1)[0]
        candidates = index.get(number)
    if not candidates:
        return None
    if want_title:
        wanted = normalise_title(want_title)
        titled = [c for c in candidates if normalise_title(c[0]) == wanted]
        if titled:
            candidates = titled

    # Drop table-of-contents entries before choosing on size. A TOC line carries
    # the page number glued to the title — "Analytics, validation, and approved
    # use159" — which is the signature, and index_headings has always collected
    # both copies. Choosing purely on body size assumed the real section was
    # bigger; in a document whose contents page runs to several hundred lines,
    # the TOC block wins. §13 resolved to lines 91-449, the contents listing,
    # rather than to the 80-line section at 5758. The verdict was then reached by
    # diffing the table of contents, which never changes, and the locator printed
    # as evidence pointed a reader at the wrong page.
    real = [c for c in candidates if not TOC_TITLE.search(c[0].strip())]
    if real:
        candidates = real
    return max(candidates, key=lambda c: c[2] - c[1])


def follow(old_index, new_index, number):
    """Resolve a reference written against the OLD revision into the new one.

    Returns (hit, note). `hit` is (title, start, end) in the new document, or
    None when the section cannot be located honestly.

    A matrix cites the numbering of the document the reviewer read. Resolving the
    same number in both revisions assumes the numbering survived, and on a
    restructured document it does not: across one review tab, 15 of 38 references
    pointed at a different section in the new revision — a component chapter under
    one number in the old draft, an unrelated validation chapter under the same
    number in the new one. Comparing those pairs produces a verdict about two
    unrelated sections, and nothing in the output says so.

    The heading is the stable identifier, not the number. So: take the title from
    the old revision, and find THAT in the new one wherever it now sits. Only
    when the title has genuinely gone does this give up — and giving up is the
    right answer, because the alternative is a confident comparison of the wrong
    text.
    """
    old = pick(old_index, number)
    if not old:
        return None, f"§{number} is not in the older revision either"
    wanted = normalise_title(old[0])

    here = pick(new_index, number)
    if here and normalise_title(here[0]) == wanted:
        return here, ""

    for num, entries in sorted(new_index.items()):
        for title, start, end in entries:
            if normalise_title(title) == wanted and not TOC_TITLE.search(title.strip()):
                if num != number:
                    return (title, start, end), f"§{number} is now §{num}"
                return (title, start, end), ""

    return None, (f"§{number} named the section {old[0].strip()[:40]!r}, which is "
                  f"not in the new revision under that number or any other")


def section_refs(cell):
    """Section numbers a matrix cell names. '§3.3, Table' -> ['3.3']."""
    return re.findall(r"§\s*(\d+(?:\.\d+)*)", cell or "")


def endpoint_limit(chat_url, model):
    """Tokens this endpoint will accept, or None if it does not say.

    Asking is worth the one request. A prompt over the limit does not degrade —
    vLLM answers HTTP 400 and the row lands as "unclear (ERROR)", which reads
    like a model that could not decide. A whole-document diff of 76k characters
    against a server started with max_model_len=16384 failed every row that way,
    and the budget that caused it was a constant chosen with no reference to the
    server at all. llama.cpp does not report a limit, so None means "trust the
    operator's --whole-budget", not "unlimited".
    """
    base = chat_url.split("/chat/completions")[0].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/models", timeout=10) as response:
            for entry in json.load(response).get("data", []):
                if entry.get("id") == model and entry.get("max_model_len"):
                    return int(entry["max_model_len"])
    except Exception:                                          # noqa: BLE001
        pass
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--from", dest="old", required=True)
    parser.add_argument("--to", dest="new", required=True)
    parser.add_argument("--id-col", default="A")
    parser.add_argument("--section-col", default="C")
    parser.add_argument("--comment-col", default="D")
    parser.add_argument("--verdict-col", default="M")
    parser.add_argument("--rationale-col", default="N")
    parser.add_argument("--model")
    parser.add_argument("--url")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-diff", type=int, default=6000)
    # Reasoning counts against this budget on every server here, and the answer
    # is emitted last. A model that thinks past the limit returns a null content
    # channel, llm.py falls back to the reasoning text, and that text is prose,
    # not JSON — so the row lands as "unclear (ERROR)" and looks like a model
    # that could not decide rather than one that was cut off mid-sentence. A
    # muse-glimmer-30b pass lost 22 of 34 rows that way at 700, while the same
    # model answered a short prompt in 543 tokens. The default stays 700 because
    # gpt-oss-120b at reasoning_effort=low needs nothing more; raise it for
    # models that think longer.
    parser.add_argument("--max-tokens", type=int, default=700)
    # Rows whose "section" cell says "All", "Title" or nothing at all. They are
    # not noise — on the architecture tab they are 36 of 90 comments, and they
    # are skipped today only because there is no section to diff. What they need
    # is the same judgement against a wider window.
    parser.add_argument("--unreferenced", default="skip",
                        choices=("skip", "whole", "retrieve", "auto"),
                        help="how to judge rows with no section reference: "
                             "whole = diff the entire document; retrieve = the "
                             "changed sections closest to the comment; auto = "
                             "whole when it fits --whole-budget, else retrieve")
    parser.add_argument("--whole-budget", type=int, default=80000,
                        help="chars of whole-document diff before auto falls "
                             "back to retrieval")
    parser.add_argument("--top-k", type=int, default=6,
                        help="changed sections to show in retrieve mode")
    parser.add_argument("--embed-model", default="bge-m3")
    parser.add_argument("--embed-url",
                        default="http://localhost:8085/v1/embeddings")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _o, old_lines = load_doc(project, args.old)
    _n, new_lines = load_doc(project, args.new)
    old_index, new_index = index_headings(old_lines), index_headings(new_lines)
    print(f"{args.old}: {len(old_lines)} lines, {len(old_index)} numbered "
          f"headings\n{args.new}: {len(new_lines)} lines, "
          f"{len(new_index)} numbered headings\n")

    xlsx = args.xlsx if os.path.isabs(args.xlsx) \
        else os.path.join(project, args.xlsx)
    rows = matrix_reader.read_sheet(xlsx, args.sheet)

    kwargs = {}
    if args.model:
        kwargs["model"] = args.model
    if args.url:
        kwargs["url"] = args.url
    client = Client(project, prompt_version=PROMPT_VERSION,
                    max_tokens=args.max_tokens, **kwargs)

    work, wide, skipped = [], [], {}
    for position, row in enumerate(rows[1:], start=2):
        rid = row.get(args.id_col, "").strip()
        if not rid:
            continue
        refs = section_refs(row.get(args.section_col, ""))
        comment = row.get(args.comment_col, "").strip()
        if not comment:
            skipped[rid] = "no comment text"
            continue
        if not refs:
            if args.unreferenced == "skip":
                skipped[rid] = ("no section ref "
                                f"({row.get(args.section_col,'').strip()[:24] or 'blank'})")
                continue
            wide.append((position, rid, [], comment))
            continue
        work.append((position, rid, refs, comment))

    print(f"{len(work)} row(s) carry a section reference; "
          f"{len(wide)} without one; {len(skipped)} skipped\n")

    wide_one = None

    def one(item):
        position, rid, refs, comment = item
        pieces, missing, renumbered = [], [], []
        for ref in refs:
            # Follow the heading, not the number — the matrix cites the old
            # revision's numbering and a restructured document renumbers.
            old_hit = pick(old_index, ref)
            new_hit, moved = follow(old_index, new_index, ref)
            if moved:
                renumbered.append(moved)
            if not new_hit:
                missing.append(ref)
                continue
            old_body = "\n".join(old_lines[old_hit[1]:old_hit[2]]) if old_hit else ""
            new_body = "\n".join(new_lines[new_hit[1]:new_hit[2]])
            pieces.append((ref, old_hit, new_hit, old_body, new_body))

        if not pieces:
            # The reference cannot be resolved into the new revision — the
            # heading is gone, or the extraction lost the numbering it was
            # written against. Word auto-numbering is the usual cause: the
            # contents page carries "12Analytics…195" while the body heading
            # extracts as bare text, so no number in the body can be matched at
            # all.
            #
            # "unclear" was the old answer and it throws the row away. The row
            # is not unjudgeable, it is merely unlocatable, which is exactly the
            # condition the unreferenced path already handles — so hand it over
            # rather than reporting a non-verdict.
            if wide_one is not None:
                out = wide_one((position, rid, [], comment))
                return (out[0], out[1], out[2],
                        out[3] + f"  [§{', §'.join(missing)} could not be located "
                                 f"in {args.new}; judged against the wider "
                                 f"change set instead]",
                        out[4], out[5] + "+UNLOCATABLE")
            return position, rid, "unclear", (
                f"section {', '.join(missing)} named by the comment does not "
                f"exist in {args.new}"), "", "MISSING SECTION"

        # No model needed when nothing moved. This is the verdict worth having.
        if all(o == n for _, _, _, o, n in pieces):
            where = ", ".join(f"§{r} ({args.new}:{nh[1]}-{nh[2]})"
                              for r, _, nh, _, _ in pieces)
            return position, rid, "not_addressed", (
                f"The text of {where} is byte-identical to {args.old}. "
                f"Nothing in the section the comment names has changed."), \
                "", "UNCHANGED"

        diffs = []
        for ref, old_hit, new_hit, old_body, new_body in pieces:
            delta = list(difflib.unified_diff(
                old_body.splitlines(), new_body.splitlines(),
                fromfile=f"{args.old} §{ref}", tofile=f"{args.new} §{ref}",
                lineterm="", n=2))
            diffs.append("\n".join(delta))
        diff_text = "\n\n".join(diffs)

        escalated = ""
        if len(diff_text) > args.max_diff:
            diff_text = diff_text[:args.max_diff] + "\n[diff truncated]"
        user = (f"COMMENT ({rid})\n{comment}\n\n"
                f"WHAT CHANGED IN THE SECTION THIS COMMENT IS ABOUT\n{diff_text}")
        try:
            reply = client.ask(SYSTEM, user, validate=validate,
                               label=f"adj:{rid}")
        except LLMError as exc:
            return position, rid, "unclear", f"model call failed: {exc}", "", "ERROR"

        # Escalate: the diff did not settle it, so show the whole new section.
        if reply["verdict"] == "unclear":
            whole = "\n\n".join(
                f"[{args.new} §{r} lines {nh[1]}-{nh[2]}]\n{nb}"
                for r, _, nh, _, nb in pieces)[:args.max_diff * 3]
            user2 = (f"COMMENT ({rid})\n{comment}\n\n"
                     f"THE FULL SECTION AS IT NOW STANDS\n{whole}")
            try:
                reply = client.ask(SYSTEM, user2, validate=validate,
                                   label=f"adj-full:{rid}")
                escalated = "ESCALATED"
            except LLMError:
                pass

        where = ", ".join(f"§{r} ({args.new}:{nh[1]}-{nh[2]})"
                          for r, _, nh, _, _ in pieces)
        if renumbered:
            where += "; " + "; ".join(renumbered)
        return (position, rid, reply["verdict"],
                reply["rationale"] + f"  [{where}]", reply.get("quote", ""),
                escalated or "DIFF")

    # ---- rows with no section reference -----------------------------------
    #
    # "All" means the reviewer was talking about the document, not a place in
    # it. There is nothing to slice, so the window has to be the whole change —
    # and on a small deliverable that is exactly what fits: the MVP and roadmap
    # diffs are 64k and 76k characters. The architecture diff is 405k, which no
    # context here holds and no reader would want undifferentiated anyway, so
    # that case narrows to the changed sections nearest the comment.
    #
    # The narrowing is retrieval, and retrieval can miss. A verdict reached from
    # six sections out of two hundred is not the same claim as one reached from
    # the whole document, so the locator says which sections were read and the
    # summary counts the two modes separately. A reviewer can then tell "the
    # document does not address this" from "the parts I looked at did not".
    changed = []
    # Prepared whenever there is any work at all, not only when a row arrived
    # without a section reference. A row WITH a reference can still fail to
    # resolve — the heading may be gone, or the extraction may have lost the
    # numbering — and it then falls through to this machinery. Building it only
    # for `wide` left those rows raising NameError on a variable that was never
    # set, on exactly the documents most likely to need it.
    if wide or work:
        for number, entries in sorted(new_index.items()):
            for title, start, end in entries:
                new_body = "\n".join(new_lines[start:end])
                old_hit = pick(old_index, number)
                old_body = "\n".join(old_lines[old_hit[1]:old_hit[2]]) if old_hit else ""
                if old_body != new_body:
                    changed.append({"ref": number, "title": title,
                                    "start": start, "end": end,
                                    "old": old_body, "new": new_body})
        # Size the choice on ONE unified diff of the document, not on the
        # concatenated sections. Sections nest, so a parent carries its
        # children's text too: summing them gives 2.2M characters for an
        # architecture document whose actual diff is 405k, and would push every
        # deliverable into retrieval on arithmetic that counts the same
        # paragraph five times.
        # Chars, not tokens, because the diff is not tokenised here. 3.0 is
        # deliberately below the ~4.0 English average: diffs are dense in
        # punctuation, +/- markers and line numbers, all of which tokenise
        # worse than prose, and the cost of guessing high is a hard 400.
        budget = args.whole_budget
        limit = endpoint_limit(args.url or "", args.model or "")
        if limit:
            room = int((limit - args.max_tokens - 512) * 3.0)
            if room < budget:
                print(f"  endpoint reports max_model_len={limit:,} tokens; "
                      f"budget {budget:,} -> {max(room, 4000):,} chars")
                budget = max(room, 4000)
        # content_lines, not the raw lines: the extractor's path banner differs
        # whenever a source file moves, and a whole-document diff would then
        # open with a change that is in neither document.
        whole_diff = "\n".join(difflib.unified_diff(
            content_lines(old_lines), content_lines(new_lines),
            fromfile=args.old, tofile=args.new, lineterm="", n=2))
        mode = args.unreferenced
        if mode == "auto":
            mode = "whole" if len(whole_diff) <= budget else "retrieve"
        print(f"{len(wide)} unreferenced row(s): {len(changed)} changed "
              f"section(s); whole-document diff {len(whole_diff):,} chars "
              f"vs budget {budget:,} -> {mode} mode")

        vectors = None
        if mode == "retrieve":
            embedder = Embedder(project, model=args.embed_model,
                                url=args.embed_url)
            embedder.preflight()
            vectors = embedder.embed(
                [f"{c['ref']} {c['title']}\n{c['new'][:1500]}" for c in changed])

    def wide_one(item):
        position, rid, _refs, comment = item
        if not changed:
            return (position, rid, "not_addressed",
                    f"No section of {args.new} differs from {args.old}.",
                    "", "NO CHANGE")
        picked = changed
        if vectors is None:
            user = (f"WHAT CHANGED IN {args.new} (vs {args.old}) \u2014 the "
                    f"complete diff of the document\n{whole_diff}\n\n"
                    f"COMMENT ({rid})\n{comment}\n\n"
                    f"This comment names no single section. Judge it against "
                    f"the changes above.")
            try:
                reply = client.ask(SYSTEM, user, validate=validate,
                                   label=f"adj-wide:{rid}")
            except LLMError as exc:
                return (position, rid, "unclear",
                        f"model call failed: {exc}", "", "ERROR")
            return (position, rid, reply["verdict"],
                    reply["rationale"] + "  [judged against the whole "
                    f"{args.old}->{args.new} diff]",
                    reply.get("quote", ""), "WHOLE-DOC")
        if vectors is not None:
            query = Embedder(project, model=args.embed_model,
                             url=args.embed_url).embed([comment])[0]
            scored = sorted(range(len(changed)),
                            key=lambda i: -cosine(query, vectors[i]))
            picked = [changed[i] for i in scored[:args.top_k]]
            picked.sort(key=lambda c: c["start"])
        pieces = []
        for c in picked:
            delta = list(difflib.unified_diff(
                c["old"].splitlines(), c["new"].splitlines(),
                fromfile=f"{args.old} \u00a7{c['ref']}",
                tofile=f"{args.new} \u00a7{c['ref']}", lineterm="", n=2))
            if delta:
                pieces.append("\n".join(delta))
        diff_text = "\n\n".join(pieces)[:budget]
        scope = ("every changed section" if vectors is None else
                 f"the {len(picked)} changed sections closest to this comment")
        # Diff first, comment last, and it is worth more than it looks.
        #
        # In WHOLE mode the diff is byte-identical for every row in the batch, so
        # it is a shared prefix and a server with prefix caching prefills it once
        # rather than once per row. Measured on gpt-oss-120b under vLLM, 55 rows
        # against a 27k-token diff: first call 22s, then a median of 13s and a
        # best of 7s. The retrieval arm of the same run, whose prompts are far
        # SMALLER but share no prefix, ran a median of 29s. Whole mode was 2.2x
        # faster per row while sending 2.7x more tokens.
        #
        # In RETRIEVE mode the ordering buys nothing and cannot: each row gets a
        # different set of retrieved sections, so there is no shared prefix
        # beyond the system preamble — a couple of hundred tokens. Do not read a
        # low cache-hit count there as a tuning problem; it is the structure of
        # the work. The only thing that would make it cacheable is content shared
        # across rows, and there is none.
        #
        # The practical consequence is that whole mode is preferable on both
        # axes when the diff fits the window: it already caught three changes
        # that top-k retrieval could not see, and it is cheaper per row.
        user = (f"WHAT CHANGED IN {args.new} (vs {args.old}) \u2014 {scope}\n"
                f"{diff_text}\n\n"
                f"COMMENT ({rid})\n{comment}\n\n"
                f"This comment names no single section. Judge it against the "
                f"changes above.")
        try:
            reply = client.ask(SYSTEM, user, validate=validate,
                               label=f"adj-wide:{rid}")
        except LLMError as exc:
            return position, rid, "unclear", f"model call failed: {exc}", "", "ERROR"
        where = ", ".join(f"\u00a7{c['ref']}" for c in picked[:8])
        if len(picked) > 8:
            where += f", +{len(picked) - 8} more"
        note = "whole document" if vectors is None else "retrieved sections"
        return (position, rid, reply["verdict"],
                reply["rationale"] + f"  [judged against {note}: {where}]",
                reply.get("quote", ""),
                "WHOLE-DOC" if vectors is None else "RETRIEVED")

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        for index, out in enumerate(pool.map(one, work), 1):
            results.append(out)
            if index % 10 == 0 or index == len(work):
                print(f"  {index}/{len(work)} judged")
    if wide:
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            for index, out in enumerate(pool.map(wide_one, wide), 1):
                results.append(out)
                if index % 10 == 0 or index == len(wide):
                    print(f"  {index}/{len(wide)} unreferenced judged")

    counts = {}
    for _, _, verdict, _, _, how in results:
        counts[verdict] = counts.get(verdict, 0) + 1
    print("\n" + "=" * 70)
    for verdict in ("not_addressed", "partial", "addressed", "unclear"):
        if counts.get(verdict):
            print(f"  {verdict:<15} {counts[verdict]}")
    unchanged = sum(1 for r in results if r[5] == "UNCHANGED")
    print(f"\n  {unchanged} of those needed no model at all: the section is "
          f"byte-identical\n  to {args.old}, so 'not addressed' is a counted "
          f"fact rather than a judgement.")
    print("=" * 70)
    for _, rid, verdict, _, _, how in sorted(results, key=lambda r: r[1]):
        print(f"  {rid:<9} {verdict:<14} {how}")

    values = {str(p): v for p, _, v, _, _, _ in results}
    notes = {str(p): r for p, _, _, r, _, _ in results}
    out_path = args.out if os.path.isabs(args.out) \
        else os.path.join(project, args.out)
    written, _ = writeback.annotate(xlsx, out_path, args.sheet, values,
                                    args.verdict_col, True)
    writeback.annotate(out_path, out_path + ".tmp2", args.sheet, notes,
                       args.rationale_col, True)
    os.replace(out_path + ".tmp2", out_path)
    print(f"\nwrote {out_path}  ({written} verdicts in column "
          f"{args.verdict_col}, rationales in {args.rationale_col})")
    if skipped:
        print(f"\n{len(skipped)} row(s) not judged:")
        for rid, why in list(skipped.items())[:12]:
            print(f"  {rid:<9} {why}")
    print(f"\nmodel calls: {client.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
