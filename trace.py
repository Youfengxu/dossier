#!/usr/bin/env python3
"""Coverage matrix: does the deliverable discharge each obligation?

This is the command the failed opencode session should have been. Instead of
one unanswerable question over a 10,000-line document, it asks N answerable
ones — for each obligation, retrieve the passages most likely to bear on it and
ask a single bounded question about those passages only.

Per obligation the model sees a few thousand tokens, never the whole document,
and never the previous questions. Cost is linear in obligations; context never
grows; an interrupted run resumes from cache.

    ./trace.py --project . --obligations obligations.yaml --doc deliverable-v1
    ./trace.py --project . --doc deliverable-v1 --limit 5      # smoke test
    ./trace.py --project . --doc deliverable-v1 --unmet-only   # just the gaps

Writes coverage.csv. Verdicts:

    met           discharged, with a verbatim quote proving it
    partial       partly discharged; the quote shows what is present
    unmet         nothing in the retrieved passages discharges it
    unverifiable  addressed in a way that cannot be checked from the text

THE QUOTE IS CHECKED. A met/partial verdict whose quote does not appear verbatim
in the passages supplied is rejected and retried, then downgraded. The model is
not permitted to invent the evidence for its own verdict.
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Client, Embedder, LLMError, cosine, load_doc   # noqa: E402

PROMPT_VERSION = "trace-6"
STOPWORDS = set("""a an the and or of to in for on with by is are be been shall
must should may not that this these those it its as at from any all each every
per which where when who whom whose if then than such other same both either""".split())

SYSTEM = """You judge whether a deliverable discharges a single obligation.

You will be given ONE obligation and the passages from the deliverable most
likely to bear on it. Judge ONLY from those passages. You cannot see the rest of
the document; if the passages do not discharge the obligation, that is "unmet",
not a reason to speculate about what might appear elsewhere.

Reply with JSON only:
{"verdict":"met|partial|unmet|unverifiable","quote":"...","reason":"..."}

Rules:
- "quote" MUST be copied verbatim, character for character, from the passages
  supplied. Do not paraphrase, do not join fragments, do not fix typos. If you
  have no verbatim quote, the verdict cannot be "met" or "partial".
- "met": the passages clearly discharge the whole obligation.
- "partial": part is discharged AND a specific, nameable part is not. Quote the
  part that is discharged, and say in "reason" exactly which part is missing.
  "The passages do not show the rest" is NOT partial — see below.
- "unmet": nothing supplied discharges it. Use "" for quote.

NOT SEEING SOMETHING IS NOT THE SAME AS IT BEING ABSENT. You are shown a few
passages, not the document. If your reason would be "the passages supplied do
not include X", the honest verdict is "unmet" — a later, wider search will
correct it. Do not hedge to "partial" to cover the possibility that the rest of
the document contains it. Hedging produces findings that evaporate on contact.
- "unverifiable": the passages claim it is addressed but locate the evidence
  elsewhere, or address it in terms that cannot be checked. Quote the claim.
- "reason": one sentence. State what is missing, not what is present.

QUOTE SHORT. The shortest span that proves the point, at most two sentences. A
long quote is not stronger evidence and it truncates the reply.

MODALITY MATTERS. The obligation's RFC 2119 keyword is given to you.
- SHALL / MUST is a duty. Absence is "unmet".
- MAY is a PERMISSION, not a duty. If the deliverable did not exercise the
  permission, the obligation is "met" — there was nothing to do. It is "unmet"
  only if the permission WAS exercised and its attached condition was not.
  "The passages show no sign they did this" is met for a MAY, not unmet.
- SHOULD is a recommendation: a duty unless a reason not to is given."""


CHUNK_SYSTEM = """You judge whether ONE passage of a deliverable contributes to ONE obligation.

Answer only about THIS passage. Most passages will be "none" simply because a
document covers many topics — but do not let that expectation decide the answer.
Read the passage and say what it actually supplies. A first pass at this
prompt, biased toward "none", returned "none" for every passage of every
obligation and made the whole run useless.

This is a narrow, POSITIVE question. You are not being asked whether the
deliverable discharges the obligation — you cannot see the deliverable, only
this passage, and something else may well carry the rest. Never answer about
what is missing. Every other passage is asked the same question separately, and
absence is computed from all the answers rather than guessed from any one.

Reply with JSON only:
{"verdict":"discharges|contributes|none","quote":"...","reason":"..."}

- "discharges": this passage alone satisfies the whole obligation.
- "contributes": this passage carries part of it, or bears on it directly.
- "none": this passage has nothing to do with this obligation. Use "" for quote.

JUDGE MEANING, NOT VOCABULARY. A passage discharges an obligation when it says
the same thing in different words. "coastal overtopping of sea defences" answers
an obligation about "tidal surge overtopping of coastal defences"; "sustained
precipitation rate in excess of drainage capacity" answers one about "prolonged
rainfall intensity exceeding drainage capacity". Requiring the obligation's own
wording turns every paraphrase into a false gap, and a deliverable that answers
in its own vocabulary is the normal case, not the exception.

"quote" MUST be copied verbatim from the passage, character for character, and
is required for "discharges" and "contributes". The shortest span that proves
the point, at most two sentences.

MODALITY. SHALL/MUST is a duty. MAY is a permission — a passage showing the
permission was simply not exercised is "none", not evidence of a gap. SHOULD is
a duty unless the passage gives a reason not to.

"reason": one sentence, stating what this passage supplies."""


def validate_chunk_factory(haystack):
    """Same verbatim-quote contract as the retrieval path, different verdicts."""
    normalised = re.sub(r"\s+", " ", haystack).lower()

    def validate(obj):
        if not isinstance(obj, dict):
            return "reply must be a JSON object"
        if obj.get("verdict") not in ("discharges", "contributes", "none"):
            return "'verdict' must be discharges, contributes or none"
        quote = (obj.get("quote") or "").strip()
        if obj["verdict"] in ("discharges", "contributes"):
            if not quote:
                return "'discharges' and 'contributes' require a verbatim quote"
            if re.sub(r"\s+", " ", quote).lower() not in normalised:
                return ("the quote does not appear verbatim in the passage; "
                        "copy an exact span or answer 'none'")
        if not (obj.get("reason") or "").strip():
            return "'reason' must not be empty"
        return None
    return validate


def validate_factory(haystack):
    normalised = re.sub(r"\s+", " ", haystack).lower()

    def validate(obj):
        if not isinstance(obj, dict):
            return "reply must be a JSON object"
        verdict = obj.get("verdict")
        if verdict not in ("met", "partial", "unmet", "unverifiable"):
            return "'verdict' must be met, partial, unmet or unverifiable"
        quote = (obj.get("quote") or "").strip()
        if verdict in ("met", "partial"):
            if not quote:
                return f"verdict '{verdict}' requires a verbatim quote"
            needle = re.sub(r"\s+", " ", quote).lower()
            if needle not in normalised:
                return ("the quote does not appear verbatim in the passages "
                        "supplied; copy an exact span or answer 'unmet'")
        if not (obj.get("reason") or "").strip():
            return "'reason' must not be empty"
        return None
    return validate


def chunk(lines, max_lines=60, max_chars=3000):
    """Split into heading-delimited chunks, capped so no chunk dominates.

    Capped by CHARACTERS as well as lines. A line-only cap silently assumes
    every document wraps at ~80 characters: contracts and other unwrapped prose
    run to several hundred characters per line, and a 60-line window then holds
    25,000 characters — one "passage" that is most of the document, useless to
    retrieve and too large to send. Found on a contract corpus, invisible on
    every wrapped one.
    """
    chunks, current, heading, start = [], [], "(front matter)", 1
    def flush(end):
        if current:
            chunks.append({"heading": heading, "start": start, "end": end,
                           "text": "\n".join(current)})
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        bare = stripped.lstrip("#").strip()
        is_heading = stripped.startswith("#") or (
            0 < len(bare) < 80 and re.match(r"^(\d+(\.\d+)*\s+\S|Appendix\s+[A-Z])", bare))
        if is_heading and current:
            flush(number - 1)
            current, heading, start = [], bare, number
        elif is_heading:
            heading, start = bare, number
        else:
            current.append(line)
            if len(current) >= max_lines or \
                    sum(len(x) for x in current) >= max_chars:
                flush(number)
                current, start = [], number + 1
    flush(len(lines))
    return [c for c in chunks if c["text"].strip()]



SECTION_ID = re.compile(r"^(\d+)(?:\.\d+)?\s+\S")


def section_of(heading):
    match = SECTION_ID.match(heading.strip())
    return match.group(1) if match else None


def section_index(chunks):
    """Group chunks under their top-level section, keeping document order.

    A package in a real architecture runs to hundreds of lines across a dozen
    chunks. Ranking those chunks independently scatters the evidence: the model
    receives three paragraphs from the middle of the Network Model and never its
    purpose or boundary, then reports that networks are not addressed.
    """
    groups = {}
    for index, item in enumerate(chunks):
        key = section_of(item["heading"])
        if key is None:
            continue
        groups.setdefault(key, []).append(index)
    return groups


def section_context(chunks, indices, limit=1400):
    """Opening text of a section — usually its purpose-and-boundary."""
    return " ".join(chunks[i]["text"] for i in indices[:2])[:limit]


def terms(text):
    return [w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
            if w not in STOPWORDS]


def retrieve(obligation, chunks, idf, k=4):
    wanted = Counter(terms(obligation))
    scored = []
    for index, item in enumerate(chunks):
        present = Counter(terms(item["text"]))
        score = sum(idf.get(w, 0) * min(n, present[w])
                    for w, n in wanted.items() if present[w])
        scored.append((score, index))
    scored.sort(reverse=True)
    return [chunks[i] for score, i in scored[:k] if score > 0]



LEX_STOP = set("""the a an and or of to in for on with by is are be been shall must
should may not that this these those it its as at from any all each every per which
where when who whom whose if then than such other same both either architecture
platform system deliverable propose include support provide ensure using use used
able ability within across into over under more most also can will would could
document section proposed""".split())


def load_lexicon(path):
    """term -> [variants]. Absent file is fine; the gate then uses the term itself."""
    if not os.path.exists(path):
        return {}
    lex, current = {}, None
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.rstrip().endswith(":"):
            current = line.strip().rstrip(":").strip().strip('"')
            lex[current] = []
        elif current and line.strip().startswith("- "):
            lex[current].append(line.strip()[2:].strip().strip('"'))
    return lex


def absence_gate(text_lower, requirement, lexicon, min_hits=8):
    """Deterministic check on an 'unmet' verdict.

    CUAD measured what happens without one: precision of 'unmet' falls from 97%
    to 83% as a document grows from 8k to 52k characters, because the model
    asserts absence from material it was shown and did not finish reading. A
    real architecture is 500k characters.

    This does not overturn the verdict — it cannot know whether the hits are the
    same concept. It downgrades to 'unverifiable' and says where to look, which
    is the honest state: the model claimed absence, the document disagrees, and
    a human has to settle it.
    """
    words = [w for w in re.findall(r"[a-z][a-z0-9\-]{4,}", requirement.lower())
             if w not in LEX_STOP]
    if not words:
        return None
    scored = []
    for word in dict.fromkeys(words):
        variants = [word] + list(lexicon.get(word, []))
        hits = sum(text_lower.count(v.lower()) for v in variants)
        scored.append((hits, word))
    scored.sort()
    # The most diagnostic terms are the rarest ones present.
    diagnostic = [(n, w) for n, w in scored if n > 0][:3]
    if len(diagnostic) < 2:
        return None
    if sum(n for n, _ in diagnostic) < min_hits:
        return None
    return ", ".join(f"{w}x{n}" for n, w in diagnostic)


def load_obligations(path):
    """Reads the subset of YAML that obligations.py emits."""
    items, current = [], None
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- id:"):
            current = {"id": line.split(":", 1)[1].strip()}
            items.append(current)
        elif current is not None and ":" in line and line.startswith("    "):
            key, value = line.strip().split(":", 1)
            value = value.strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = value.strip('"')
            current[key] = value
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True, help="slug of the deliverable")
    parser.add_argument("--obligations", default="obligations.yaml")
    parser.add_argument("--model", default="qwen3.6-35b-a3b")
    parser.add_argument("--url", default="http://localhost:8085/v1/chat/completions")
    parser.add_argument("--out", default="coverage.csv")
    parser.add_argument("--passages", type=int, default=12)
    parser.add_argument("--no-screen-fallback", action="store_true",
                        help="report unmet on a vocabulary miss instead of "
                             "screening. Faster, and wrong exactly when the "
                             "deliverable answers in its own words")
    parser.add_argument("--rerank-url",
                        help="cross-encoder rerank endpoint, e.g. "
                             "http://k11:8085/v1/rerank. Off unless set")
    parser.add_argument("--rerank-model", default="bge-reranker-v2-m3")
    parser.add_argument("--rerank-depth", type=int, default=40)
    parser.add_argument("--exhaustive", action="store_true",
                        help="judge EVERY chunk against every obligation "
                             "instead of retrieving. Removes the absence "
                             "judgement; costs chunks x obligations calls "
                             "(arch-v5: ~19,800, about an hour at 16-way)")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--unmet-only", action="store_true")
    parser.add_argument("--include-permissive", action="store_true",
                        help="also judge MAY obligations (off by default: a "
                             "permission the deliverable never exercised owes "
                             "nothing, and judging it produces false unmets)")
    parser.add_argument("--scope", default=None,
                        help="only obligations tagged with this scope. One "
                             "requirements document governs several deliverables; "
                             "without this the matrix fills with false unmets")
    parser.add_argument("--retrieval", choices=("embed", "terms", "section"),
                        default="embed",
                        help="section: rank SECTIONS first, then pass each "
                             "winning section's opening context plus its best "
                             "chunks. Flat retrieval returns two or three "
                             "fragments of a 700-line package, which is not the "
                             "same as reading the package.")
    parser.add_argument("--sections", type=int, default=3,
                        help="section mode: how many sections to draw from")
    parser.add_argument("--lexicon", default="lexicon.yaml",
                        help="term variants; an absence verdict is checked "
                             "against the whole document using it")
    parser.add_argument("--no-absence-gate", action="store_true")
    parser.add_argument("--embed-url",
                        default="http://192.168.100.21:8085/v1/embeddings")
    parser.add_argument("--embed-model", default="Qwen3-Embedding-8B-Q4_K_M")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    obligations_path = os.path.join(project, args.obligations)
    if not os.path.exists(obligations_path):
        sys.exit(f"no {args.obligations} — run obligations.py first")
    obligations = load_obligations(obligations_path)
    if args.scope:
        before = len(obligations)
        obligations = [o for o in obligations if o.get("scope") == args.scope]
        print(f"scope '{args.scope}': {len(obligations)} of {before} obligations")
        if not obligations:
            sys.exit(f"no obligations with scope '{args.scope}'; seen: "
                     + ", ".join(sorted({o.get('scope','unscoped')
                                         for o in load_obligations(obligations_path)})))
    if args.limit:
        obligations = obligations[:args.limit]

    try:
        doc, lines = load_doc(project, args.doc)
    except LLMError as exc:
        sys.exit(str(exc))
    if doc["role"] == "draft":
        print(f"warning: {args.doc} is role 'draft' — diffable, not citable",
              file=sys.stderr)

    chunks = chunk(lines)
    document_freq = Counter()
    for item in chunks:
        document_freq.update(set(terms(item["text"])))
    total = len(chunks)
    idf = {w: (total / (1 + n)) ** 0.5 for w, n in document_freq.items()}

    embedder, chunk_vectors = None, None
    sections, sect_vectors = {}, {}
    if args.retrieval in ("embed", "section"):
        embedder = Embedder(project, model=args.embed_model, url=args.embed_url)
        if embedder.available():
            print(f"embedding {total} passages with {args.embed_model} …")
            chunk_vectors = embedder.embed([c["text"] for c in chunks])
            if args.retrieval == "section":
                sections = section_index(chunks)
                keys = sorted(sections, key=int)
                blurbs = [f"Section {k}. " + section_context(chunks, sections[k])
                          for k in keys]
                sect_vectors = dict(zip(keys, embedder.embed(blurbs)))
                print(f"  {len(keys)} sections indexed for two-stage retrieval")
        else:
            print("embedding endpoint unavailable — falling back to term overlap",
                  file=sys.stderr)
            embedder = None

    print(f"{len(obligations)} obligations x {total} passages in {args.doc} "
          f"({doc['source_sha256'][:12]})\n")

    lexicon = load_lexicon(os.path.join(project, args.lexicon))
    document_lower = "\n".join(lines).lower()
    if lexicon:
        print(f"lexicon: {len(lexicon)} terms with variants")

    client = Client(project, model=args.model, url=args.url,
                    prompt_version=PROMPT_VERSION)
    rows, tally = [], Counter()
    gated = 0

    for index, obligation in enumerate(obligations, 1):
        # Stop on a dead endpoint rather than grinding out a full matrix of
        # "unverifiable". Every LLMError becomes a row (see below), so a stack
        # that refuses every connection still produced a complete CSV, a
        # summary reading "0 failed", and a scorer that reported 100% recall on
        # it. A wrong answer that looks finished is worse than a crash.
        if index > 3 and client.dead():
            sys.exit(f"\naborting: {client.stats.get('transport', 0)} calls in a "
                     f"row failed to reach {client.url} and none succeeded.\n"
                     f"Check the endpoint and model, then re-run — nothing was "
                     f"written.")
        text = obligation.get("text", "")

        # A permission is not a duty. "The passages show no sign they did this"
        # is the expected state for a MAY, not a defect — and asking the model
        # nicely did not stop it reporting one, so the filter is structural.
        # The real question for a permission ("did they exercise it, and if so
        # was the condition met?") is a different question and needs asking
        # separately.
        if not args.include_permissive and \
                obligation.get("modality", "").upper().startswith("MAY"):
            tally["not_applicable"] += 1
            rows.append({
                "obligation": obligation["id"],
                "source_ref": obligation.get("source_ref", ""),
                "modality": obligation.get("modality", ""),
                "requirement": text, "verdict": "not_applicable", "quote": "",
                "locator": "", "reason": "permissive obligation — a permission "
                                         "the deliverable need not exercise",
                "requirement_locator": obligation.get("locator", "")})
            print(f"  . {obligation['id']:7} {'not_applicable':13} {text[:66]}")
            continue
        def screen_all(label):
            """Stage 1: ask every chunk the narrow positive question.

            Returns the passages that bear on this obligation, sufficient ones
            first, capped at --passages. Used by --exhaustive and, more
            importantly, as the fallback when retrieval finds nothing at all.
            """
            def one(hit):
                body = ("[" + hit["heading"] + " | " + args.doc + ":"
                        + str(hit["start"]) + "-" + str(hit["end"]) + "]\n"
                        + hit["text"])
                user = ("OBLIGATION " + obligation["id"] + " ("
                        + obligation.get("modality", "") + ")\n" + text
                        + "\n\nPASSAGE\n" + body
                        + "\n\nDoes this passage contribute to this obligation?")
                try:
                    return hit, client.ask(
                        CHUNK_SYSTEM, user,
                        validate=validate_chunk_factory(body),
                        label=label + ":" + obligation["id"] + ":"
                              + str(hit["start"]))
                except LLMError:
                    return hit, None

            strong, weak = [], []
            with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
                for hit, reply in pool.map(one, chunks):
                    if not reply or reply["verdict"] == "none":
                        continue
                    (strong if reply["verdict"] == "discharges" else weak).append(hit)
            return (strong + weak)[:args.passages]

        if embedder is not None and args.retrieval == "section":
            query = embedder.embed([text])[0]
            scored = sorted(((cosine(query, sect_vectors[key]), key)
                             for key in sect_vectors), reverse=True)
            picked, seen = [], set()
            for _, key in scored[:args.sections]:
                members = sections[key]
                # the section's own opening always travels with it
                for i in members[:1]:
                    if i not in seen:
                        seen.add(i); picked.append(chunks[i])
                inner = sorted(((cosine(query, chunk_vectors[i]), i)
                                for i in members), reverse=True)
                for _, i in inner[:max(1, args.passages // args.sections)]:
                    if i not in seen:
                        seen.add(i); picked.append(chunks[i])
            hits = picked[:args.passages + args.sections]
        elif args.exhaustive:
            hits = screen_all("chunk")
        elif embedder is not None:
            query = embedder.embed([text])[0]
            ranked = sorted(((cosine(query, v), i)
                             for i, v in enumerate(chunk_vectors)), reverse=True)
            order = [i for _, i in ranked]
            if args.rerank_url:
                # Cross-encoder second pass. The bi-encoder compares two
                # independently compressed vectors; this scores the pair
                # jointly, which is what handles paraphrase. Measured on CM-1:
                # requirements with ALL gold links inside the top 4 rise from
                # 10/19 to 14/19. Retrieval stays wide and cheap; only the
                # shortlist is scored.
                import urllib.request as _u
                head = order[:args.rerank_depth]
                try:
                    body = json.dumps({
                        "model": args.rerank_model, "query": text,
                        "documents": [chunks[i]["text"] for i in head]}).encode()
                    req = _u.Request(args.rerank_url, data=body,
                                     headers={"Content-Type": "application/json"})
                    with _u.urlopen(req, timeout=300) as resp:
                        res = json.load(resp)["results"]
                    order = [head[r["index"]] for r in
                             sorted(res, key=lambda r: -r["relevance_score"])] \
                        + order[args.rerank_depth:]
                except Exception:                           # noqa: BLE001
                    pass                    # reranker down: keep dense order
            hits = [chunks[i] for i in order[:args.passages]]
        else:
            hits = retrieve(text, chunks, idf, k=args.passages)
        if not hits and not args.exhaustive and not args.no_screen_fallback:
            # Retrieval found no passage sharing this obligation's vocabulary.
            # That is exactly when a deliverable answering in DIFFERENT WORDS
            # earns a false "unmet" — DESIGN 3.7 — so screen semantically
            # before concluding absence. Bounded: it runs only for the
            # obligations retrieval gave up on, not the whole matrix.
            print(f"    {obligation['id']}: retrieval found nothing — screening "
                  f"{len(chunks)} passages")
            hits = screen_all("fallback")
            tally["screen_fallback"] = tally.get("screen_fallback", 0) + 1

        if not hits and args.exhaustive:
            verdict = {"verdict": "unmet", "quote": "",
                       "reason": ("no passage of " + str(len(chunks)) + " in the "
                                  "deliverable bears on this obligation")}
        elif not hits:
            verdict = {"verdict": "unmet", "quote": "",
                       "reason": "no passage in the deliverable shares vocabulary "
                                 "with this obligation"}
        else:
            supplied = "\n\n".join(
                f"[{h['heading']} | {args.doc}:{h['start']}-{h['end']}]\n{h['text']}"
                for h in hits)
            user = (f"OBLIGATION {obligation['id']} ({obligation.get('modality','')})\n"
                    f"{text}\n\n"
                    f"PASSAGES FROM THE DELIVERABLE\n{supplied}\n\n"
                    "Does the deliverable discharge this obligation?")
            try:
                verdict = client.ask(SYSTEM, user, validate=validate_factory(supplied),
                                     label=f"trace:{obligation['id']}")
            except LLMError as exc:
                verdict = {"verdict": "unverifiable", "quote": "",
                           "reason": f"model failed to return a checkable verdict: {exc}"}

        # NEGATIVE RESULT, kept because the asymmetry is still useful.
        #
        # "clear" means the document never uses the obligation's vocabulary at
        # all — a genuinely high-confidence absence, and rare (2 of 36 on a real
        # architecture).
        #
        # "contested" means the vocabulary IS present, and it means almost
        # NOTHING. On this corpus the topic is nearly always discussed while the
        # required artefact is still missing: the Content Inject finding is
        # contested because "inject" appears 58 times, which is precisely the
        # finding — injects are scheduled, the interface is not specified. Do not
        # use contested to deprioritise; it demotes true positives.
        #
        # The lesson generalises: term presence is not obligation discharge. The
        # absence() discipline of sweeping synonyms and READING the passages
        # holds; automating it into a verdict does not.
        gate_state, gate_evidence = "", ""
        if verdict["verdict"] == "unmet" and not args.no_absence_gate:
            found = absence_gate(document_lower, text, lexicon)
            if found:
                gated += 1
                gate_state, gate_evidence = "contested", found
            else:
                gate_state = "clear"

        locator = ""
        quote = (verdict.get("quote") or "").strip()
        if quote:
            needle = re.sub(r"\s+", " ", quote).lower()
            for h in hits:
                if needle in re.sub(r"\s+", " ", h["text"]).lower():
                    locator = f"{args.doc}:{h['start']}-{h['end']}"
                    break

        tally[verdict["verdict"]] += 1
        rows.append({
            "obligation": obligation["id"],
            "source_ref": obligation.get("source_ref", ""),
            "modality": obligation.get("modality", ""),
            "requirement": text,
            "verdict": verdict["verdict"],
            "quote": quote,
            "locator": locator,
            "reason": verdict.get("reason", ""),
            "absence_gate": gate_state,
            "gate_evidence": gate_evidence,
            "requirement_locator": obligation.get("locator", ""),
        })
        mark = {"met": "  ", "partial": " ~", "unmet": " !",
                "unverifiable": " ?"}[verdict["verdict"]]
        print(f"{mark} {obligation['id']:7} {verdict['verdict']:13} "
              f"{text[:66]}")
        if index % 20 == 0:
            print(f"   … {index}/{len(obligations)}")

    if args.unmet_only:
        rows = [r for r in rows if r["verdict"] in ("unmet", "partial")]

    out_path = os.path.join(project, args.out)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{client.summary()}")
    clear = sum(1 for r in rows if r.get("absence_gate") == "clear")
    if gated or clear:
        print(f"  absence gate: {clear} unmet CLEAR (vocabulary absent "
              f"entirely — high confidence), {gated} contested "
              f"(vocabulary present, which proves nothing)")
    print("  " + "  ".join(f"{k}={tally[k]}" for k in
                           ("met", "partial", "unmet", "unverifiable"))
          + (f"  not_applicable={tally['not_applicable']}"
             if tally["not_applicable"] else ""))
    print(f"\nwrote {os.path.relpath(out_path, project)} ({len(rows)} rows)")
    print("Unmet and partial rows are finding CANDIDATES, not findings: each one "
          "needs\nthe quote read in context before it goes near a register.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
