#!/usr/bin/env python3
"""Bounded, cached, logged calls to a local OpenAI-compatible endpoint.

Everything the batch tools need and nothing they don't. The design constraints
all come from one observed failure: a 2.5-hour opencode session that spent
496,949 input tokens and produced nothing, because every turn resent the whole
accumulating conversation.

So:

  * every call is INDEPENDENT — no conversation, no accumulation. Cost is linear
    in the number of questions, not quadratic in the number of turns.
  * every call is CACHED by content hash, so a re-run after an interruption or a
    prompt tweak only pays for what actually changed.
  * every call is LOGGED to JSONL — model, prompt hash, latency, raw output. The
    cheap substitute for a tracing service.
  * every call is SCHEMA-CHECKED, with one corrective retry, then it gives up
    loudly rather than returning something malformed.

Stdlib only. No openai package, no httpx, nothing to install on a review machine.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

def _setting(name, fallback):
    """Config resolution, in one place, honoured by every tool.

    These were read by the `dossier` wrapper alone, so the documented escape
    hatch worked for wrapper subcommands and was silently ignored by every
    direct script invocation — which is what the fixture READMEs, the docstrings
    and half this project's own examples use. A stranger who set DOSSIER_MODEL
    and ran ./trace.py got someone else's LAN address and a GGUF filename they
    do not have. Environment first, then a file, then a default that points at
    the thing most people already have running.
    """
    value = os.environ.get(name)
    if value:
        return value
    for path in (os.path.join(os.path.expanduser("~"), ".config", "dossier",
                              "config"),
                 os.path.join(os.path.expanduser("~"), ".dossier")):
        try:
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == name:
                    return val.strip().strip('"\'')
        except OSError:
            continue
    return fallback


# Ollama, because it is what a stranger has running. The homelab this was built
# on is an example in the README, not the default — a default nobody else can
# reach is indistinguishable from a broken tool.
DEFAULT_URL = _setting("DOSSIER_CHAT_URL",
                       "http://localhost:11434/v1/chat/completions")
DEFAULT_MODEL = _setting("DOSSIER_MODEL", "qwen3:8b")
# Consecutive transport failures, with zero successes, before a run is
# treated as pointing at nothing. Small enough to fail fast, large enough
# to ride out a single blip on a warm endpoint.
DEAD_AFTER = 4
CACHE_DIR = ".dossier-cache"
LOG_FILE = ".dossier-log.jsonl"


def chat(url, model, system=None, user=None, *, messages=None, max_tokens=1200,
         temperature=0.0, timeout=300, json_object=True, reasoning_effort=None):
    """The one place a chat request is built. Returns (content, reasoning, stop).

    Every guard this project has paid for lives here, and nowhere else. That is
    the point of the function, not a side effect of it: the guards below were
    each learned from a failure, and a guard that lives in six copies is not a
    guard, it is a coincidence. One tool was found carrying four of them and
    another carrying two, identical in every other respect.

      response_format   Asking for JSON in the prompt is a request; this is a
                        constraint. One model read the same instruction another
                        obeyed and returned markdown with finish_reason "stop" —
                        a failure invisible to any caller checking for errors,
                        because there was no error.

      penalties at 0    Server-side sampling penalties wreck structured output:
                        every JSON object repeats its keys, so penalising seen
                        tokens pushes the model off "quotes" and "answer"
                        exactly when it needs them again. One endpoint serves a
                        presence penalty of 1.5 by default and produced 63,961
                        characters of degenerate JSON for a bounded question.

      stop returned     A reply cut at the token limit arrives NON-EMPTY with
                        its JSON unterminated, so "did it say anything" is the
                        wrong test. Callers need the stop reason to tell a
                        finished answer from a severed one.

      reasoning         Reasoning models spend the budget before they answer and
                        can return empty content beside a full reasoning
                        channel. That is a budget problem, not a refusal, and
                        the caller can only tell if it is handed both.
    """
    if messages is None:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user or ""})

    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    effort = reasoning_effort or os.environ.get("DOSSIER_REASONING_EFFORT")
    if effort:
        payload["reasoning_effort"] = effort

    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.load(response)
    choice = body["choices"][0]
    message = choice.get("message") or {}
    return ((message.get("content") or "").strip(),
            (message.get("reasoning_content") or message.get("reasoning") or "").strip(),
            choice.get("finish_reason") or "")


class LLMError(RuntimeError):
    pass


class Client:
    def __init__(self, project, model=DEFAULT_MODEL, url=DEFAULT_URL,
                 prompt_version="1", temperature=0.0, timeout=300, quiet=False,
                 max_tokens=1200):
        self.project = project
        self.model = model
        self.url = url
        self.prompt_version = prompt_version
        self.temperature = temperature
        self.timeout = timeout
        self.quiet = quiet
        self.max_tokens = max_tokens
        self.reasoning_effort = os.environ.get("DOSSIER_REASONING_EFFORT")
        self.cache_dir = os.path.join(project, CACHE_DIR)
        self.log_path = os.path.join(project, LOG_FILE)
        os.makedirs(self.cache_dir, exist_ok=True)
        self.stats = {"calls": 0, "cached": 0, "retries": 0, "failed": 0}

    # -- cache -------------------------------------------------------------
    def _key(self, system, user):
        blob = "\x00".join([self.model, self.prompt_version,
                            str(self.temperature), system, user])
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cached(self, key):
        path = os.path.join(self.cache_dir, key + ".json")
        if os.path.exists(path):
            try:
                return json.load(open(path))
            except json.JSONDecodeError:
                os.unlink(path)
        return None

    def _store(self, key, value):
        with open(os.path.join(self.cache_dir, key + ".json"), "w") as handle:
            json.dump(value, handle)

    # -- transport ---------------------------------------------------------
    def _post(self, system, user):
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            # Send these explicitly rather than inheriting whatever the server
            # was started with. A repetition penalty is poison for structured
            # output: every element of a JSON array repeats the same keys, so
            # penalising seen tokens pushes the model off "name" and "quote"
            # exactly when it is emitting the second object.
            #
            # This is not hypothetical. GX10 serves its primary model with
            # --presence-penalty 1.5, and an inventory run over 624 sections
            # failed 590 of them on 'each capability needs a name' — while the
            # same endpoint handled flat arrays of strings perfectly, because
            # those repeat no keys.
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
        }
        # Reasoning models default to spending tokens on analysis these tools do
        # not read. Extraction against a schema is not a task that benefits, and
        # on gpt-oss-120b "low" cut completion tokens from 63 to 22 for the same
        # answer. Sent only when set, since servers differ on unknown fields.
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.load(response)
        message = body["choices"][0]["message"]
        content = message.get("content")
        if content:
            return content
        # Reasoning models can spend the whole token budget thinking and never
        # emit a final channel, leaving content null. Falling through to
        # json.loads(None) raised TypeError, which is not in the caught set, so
        # one such reply killed every worker in the pool and lost a 40-minute
        # run. Prefer the reasoning channel if it carries the answer; otherwise
        # fail as a normal retryable error.
        for alternative in ("reasoning_content", "reasoning"):
            text = message.get(alternative)
            if isinstance(text, str) and text.strip():
                return text
        raise KeyError(
            "empty content (finish_reason="
            f"{body['choices'][0].get('finish_reason')!r}) — the model "
            "produced no final answer, usually reasoning that ran past "
            "max_tokens")

    def _log(self, record):
        with open(self.log_path, "a") as handle:
            handle.write(json.dumps(record) + "\n")

    # -- public ------------------------------------------------------------
    def ask(self, system, user, validate=None, label=""):
        """One question, one JSON answer. `validate(obj)` returns an error string
        or None; a failure buys exactly one corrective retry."""
        key = self._key(system, user)
        # DOSSIER_NO_CACHE re-asks questions already answered. The cache key is
        # a hash of model, prompt and temperature, so re-running a finished job
        # returns the same answers by construction — a rerun that reports "100%
        # identical" has measured its own disk, not the model. Set this when the
        # question is how much a verdict moves between runs; leave it unset for
        # every other purpose, because the cache is what makes an interrupted
        # run cheap to resume.
        hit = None if os.environ.get("DOSSIER_NO_CACHE") else self._cached(key)
        if hit is not None:
            self.stats["cached"] += 1
            return hit

        attempt, correction = 0, ""
        while attempt < 2:
            attempt += 1
            started = time.time()
            try:
                raw = self._post(system, user + correction)
            except (urllib.error.URLError, OSError, KeyError) as exc:
                # Count it. Incrementing only on success made a run that could
                # not reach the endpoint at all report "0 calls, 305 cached" in
                # three seconds — which reads as a complete run served from
                # cache, not as 319 refused connections. The stats line is the
                # only thing most runs are judged by, so a failure it cannot
                # express is a failure that gets missed.
                self.stats["calls"] += 1
                self.stats["transport"] = self.stats.get("transport", 0) + 1
                self._log({"label": label, "model": self.model,
                           "prompt_version": self.prompt_version,
                           "prompt_sha256": key[:16], "attempt": attempt,
                           "seconds": round(time.time() - started, 2),
                           "error": f"transport: {exc}", "raw": ""})
                # Every tool turns an LLMError into a row — "unverifiable",
                # "unclear", an empty inventory entry — which is right for a
                # model that answered badly and wrong for an endpoint that is
                # not there. Left alone, a stack that refuses every connection
                # produces a complete, plausible, empty result: 74 sections
                # extracted to nothing, written to disk, and read downstream as
                # a document containing nothing. Once it is proven that nothing
                # has ever got through, stop here rather than in each of the
                # seven tools that would otherwise each need their own guard.
                if self.stats["transport"] >= DEAD_AFTER and self.dead():
                    raise SystemExit(
                        f"\naborting: {self.stats['transport']} calls failed to "
                        f"reach {self.url} and none has succeeded.\n"
                        f"model={self.model!r} — check the endpoint is up and "
                        f"serving that model. Nothing was written.")
                raise LLMError(f"{label}: endpoint call failed: {exc}") from exc
            elapsed = time.time() - started
            self.stats["calls"] += 1

            error = None
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                obj, error = None, f"not valid JSON ({exc})"
            if obj is not None and validate:
                error = validate(obj)

            self._log({"label": label, "model": self.model,
                       "prompt_version": self.prompt_version,
                       "prompt_sha256": key[:16], "attempt": attempt,
                       "seconds": round(elapsed, 2), "error": error,
                       "raw": raw[:4000]})

            if error is None:
                self._store(key, obj)
                return obj

            self.stats["retries"] += 1
            if not self.quiet:
                print(f"    retry {label}: {error}")
            correction = ("\n\nYour previous reply was rejected: " + error +
                          "\nReply with valid JSON matching the schema exactly, "
                          "and nothing else.")

        self.stats["failed"] += 1
        raise LLMError(f"{label}: no schema-valid reply after 2 attempts")

    def summary(self):
        # transport was counted from the start and never printed, so a run in
        # which every single call was refused reported "0 failed" and wrote a
        # full output file. A count that exists only in memory is not a report.
        line = (f"{self.stats['calls']} calls, {self.stats['cached']} cached, "
                f"{self.stats['retries']} retries, {self.stats['failed']} failed")
        transport = self.stats.get("transport", 0)
        if transport:
            line += f", {transport} TRANSPORT FAILURES"
        return line

    def dead(self):
        """Every attempt so far has failed at the transport layer.

        Distinguishes "the endpoint is not there" from "the model answered
        badly". The first is a setup error the caller should stop on; the second
        is a result. Nothing downstream can tell them apart from the verdicts,
        because both arrive as unverifiable rows.
        """
        # calls counts ATTEMPTS — a transport failure increments both calls and
        # transport (lines 165-166), so equality means not one attempt ever
        # reached the server. Checking calls == 0 instead, as a first version
        # did, is always false and the guard never fires.
        transport = self.stats.get("transport", 0)
        return transport > 0 and self.stats["calls"] == transport \
            and not self.stats["cached"]



DEFAULT_EMBED_URL = _setting("DOSSIER_EMBED_URL",
                             "http://localhost:11434/v1/embeddings")
DEFAULT_EMBED_MODEL = _setting("DOSSIER_EMBED_MODEL", "bge-m3")


class Embedder:
    """Batched, disk-cached embeddings from a local endpoint.

    Term-overlap retrieval fails in exactly the case that matters most: when the
    deliverable answers an obligation in different words. That is not a corner
    case — a deliverable that never uses the requirement's vocabulary is itself a
    finding, and the retriever must still surface the passage so a human can
    judge it. Hence embeddings.
    """

    def __init__(self, project, model=DEFAULT_EMBED_MODEL, url=DEFAULT_EMBED_URL,
                 batch=16, timeout=180):
        self.model, self.url, self.batch, self.timeout = model, url, batch, timeout
        self.cache_dir = os.path.join(project, CACHE_DIR, "embed")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.hits = self.misses = 0

    def _path(self, text):
        digest = hashlib.sha256((self.model + "\x00" + text).encode()).hexdigest()
        return os.path.join(self.cache_dir, digest + ".json")

    def preflight(self):
        """Fail with the model name, not a 500 traceback.

        The default embedding model is currently served by no endpoint — absent
        on the Mac, configured on k11 but failing to load — and the symptom is
        an HTTP 500 raised from deep inside a batch, after however long the run
        has already taken. Three synthesis runs died that way, and each looked
        like a transport blip rather than a missing model.

        Not solved by switching the default: bge-m3 is servable everywhere and
        measurably worse, taking the fixture from 6/6 to 5/6 and adding D8
        noise. The right answer is to serve the right model; the right
        behaviour meanwhile is to say so in one line, before the work.
        """
        try:
            self.embed(["preflight"])
        except Exception as exc:                            # noqa: BLE001
            raise LLMError(
                f"embedding model {self.model!r} is not usable at {self.url} "
                f"({exc}).\n"
                f"    Either point --embed-url at a host serving it, or pass "
                f"--embed-model bge-m3\n"
                f"    — bge-m3 works everywhere but scores lower; see DESIGN "
                f"on the embedding model.") from exc

    def available(self):
        try:
            self.embed(["ping"])
            return True
        except Exception:                                   # noqa: BLE001
            return False

    def _post(self, texts):
        payload = {"model": self.model, "input": texts}
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        # Retry with backoff. Unlike a chat call, an embedding batch has no
        # corrective retry above it, so one transient 500 propagates and kills
        # the process — a synthesis run died that way immediately after a
        # 2h47m inventory, on an endpoint that answered the identical request
        # correctly a minute later. These servers swap models under load; a
        # blip is expected and is not a reason to lose the job.
        delay = 2
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request,
                                            timeout=self.timeout) as response:
                    body = json.load(response)
                return [row["embedding"] for row in body["data"]]
            except (urllib.error.URLError, OSError, KeyError) as exc:
                if attempt == 3:
                    raise LLMError(
                        f"embeddings failed after 4 attempts: {exc}") from exc
                time.sleep(delay)
                delay *= 2

    def embed(self, texts):
        out, pending, positions = [None] * len(texts), [], []
        for index, text in enumerate(texts):
            path = self._path(text)
            cached = None
            if os.path.exists(path):
                try:
                    cached = json.load(open(path))
                except (json.JSONDecodeError, OSError):
                    # A half-written or double-written entry. Treat it as a
                    # miss and overwrite rather than crashing a run that is
                    # forty minutes in: the cache is derived data and the only
                    # cost of a bad entry is recomputing it.
                    cached = None
            if cached is not None:
                out[index] = cached
                self.hits += 1
            else:
                pending.append(text)
                positions.append(index)
        for start in range(0, len(pending), self.batch):
            group = pending[start:start + self.batch]
            vectors = self._post(group)
            for text, vector, index in zip(group, vectors,
                                           positions[start:start + self.batch]):
                # Write-then-rename, because two runs sharing a project share
                # this cache and the same text embeds to the same path. Writing
                # in place let two processes interleave into one file, and the
                # result was a JSON document with a second document appended —
                # which then crashed every later run that read it. os.replace is
                # atomic on the same filesystem.
                final = self._path(text)
                temporary = f"{final}.{os.getpid()}.tmp"
                with open(temporary, "w") as handle:
                    json.dump(vector, handle)
                os.replace(temporary, final)
                out[index] = vector
                self.misses += 1
        return out


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# extract.py writes "########## <path> ##########" between concatenated inputs.
# It carries an ABSOLUTE path, so it differs whenever the source file moves —
# and a diff of two revisions then reports one change that is not in either
# document. On a revision that changed four lines, that inflated the count by
# 25%. sweep.py already excludes these lines from heading detection; nothing was
# excluding them from comparison.
BANNER = "##########"


def content_lines(lines):
    """The document's own lines, without the extractor's provenance banners."""
    return [l for l in lines if not l.lstrip().startswith(BANNER)]


def load_doc(project, slug, verify=True):
    """Read a frozen document. Refuses unfrozen corpora — a locator into text
    that can still move is not a locator.

    AND VERIFIES THE TEXT AGAINST ITS PIN, which it did not for a long time. The
    manifest recorded text_sha256 and exactly one place ever read it: freeze.py
    --check. Every other tool — every adjudicator, every renderer, every
    repairer — loaded the file and trusted it. A hand-edited frozen document, a
    truncated write, or a re-freeze that shifted every line all loaded silently
    and produced citations that looked identical to sound ones.

    The pin exists to make "this is the text the model was shown" checkable. A
    pin nobody checks is a comment."""
    manifest_path = os.path.join(project, "parsed", "MANIFEST.json")
    if not os.path.exists(manifest_path):
        raise LLMError("corpus not frozen — run freeze.py first")
    manifest = json.load(open(manifest_path))
    for doc in manifest["documents"]:
        if doc["slug"] == slug:
            path = os.path.join(project, doc["parsed"])
            raw = open(path, "rb").read()
            pinned = doc.get("text_sha256")
            if verify and pinned:
                actual = hashlib.sha256(raw).hexdigest()
                if actual != pinned:
                    raise LLMError(
                        f"{doc['parsed']} does not match its pin.\n"
                        f"  manifest {pinned[:16]}\n"
                        f"  on disk  {actual[:16]}\n\n"
                        f"Every locator into this document is now suspect: the text a "
                        f"citation was checked against is not the text on disk. Either "
                        f"restore the file, or re-freeze and RE-RUN anything that "
                        f"produced citations against it — freeze.py --refreeze does not "
                        f"move locators, it invalidates them.")
            return doc, raw.decode("utf-8", errors="replace").splitlines()
    raise LLMError(f"no document {slug!r}; have: "
                   + ", ".join(d["slug"] for d in manifest["documents"]))
