#!/usr/bin/env python3
"""Say what answers and what does not, before a fifteen-minute run finds out.

    ./doctor.py                 # probe the configured endpoints
    ./doctor.py --project .     # also check the project's corpus

Every failure this reports used to surface as something else. A chat endpoint on
the wrong port surfaced after five hours, because the client timeout is 300
seconds and a coverage run makes sixty calls. An embedding model that no endpoint
serves surfaced as an HTTP 500 from deep inside a batch. A dead stack surfaced as
a complete CSV full of "unverifiable" rows and a summary reading "0 failed".

The point is not that these were unfixable. It is that a tool which cannot say
"the thing you configured is not there" makes every one of its other messages
less trustworthy.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm                                                   # noqa: E402

OK, BAD, WARN = "  ok  ", " FAIL ", " warn "


def probe(url, payload, timeout=25):
    started = time.time()
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response), time.time() - started, None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        return None, time.time() - started, f"HTTP {exc.code}: {body}"
    except Exception as exc:                                  # noqa: BLE001
        return None, time.time() - started, str(exc)


def models_at(url):
    """What the endpoint says it serves. Naming the alternatives turns 'model
    not found' into an answer rather than a puzzle."""
    base = url.split("/chat/completions")[0].split("/embeddings")[0].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/models", timeout=8) as response:
            return [m.get("id") for m in (json.load(response).get("data") or [])]
    except Exception:                                         # noqa: BLE001
        return []


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", help="also verify this project's corpus")
    args = parser.parse_args()

    print("configuration")
    print(f"    chat   {llm.DEFAULT_MODEL}  at  {llm.DEFAULT_URL}")
    print(f"    embed  {llm.DEFAULT_EMBED_MODEL}  at  {llm.DEFAULT_EMBED_URL}")
    source = "environment" if os.environ.get("DOSSIER_CHAT_URL") else "default"
    config = os.path.join(os.path.expanduser("~"), ".config", "dossier", "config")
    if os.path.exists(config):
        source = f"{source}, plus {config}"
    print(f"    from   {source}\n")

    failures = 0

    print("chat endpoint")
    available = models_at(llm.DEFAULT_URL)
    body, seconds, error = probe(llm.DEFAULT_URL, {
        "model": llm.DEFAULT_MODEL, "temperature": 0, "max_tokens": 400,
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}]})
    if error:
        failures += 1
        print(f"  {BAD} {error}")
        if available:
            print(f"         endpoint serves: {', '.join(available[:8])}")
            if llm.DEFAULT_MODEL not in available:
                print(f"         {llm.DEFAULT_MODEL!r} is NOT among them — set "
                      f"DOSSIER_MODEL to one of the above")
        elif error.startswith("HTTP"):
            # An HTTP status means something answered. Saying "nothing
            # reachable" here sends the reader to check the wrong thing — the
            # server is up and the model is missing, which is a one-line fix.
            print(f"         the endpoint is up but has no such model. "
                  f"Pull it, or set DOSSIER_MODEL.")
        else:
            print(f"         nothing is listening at {llm.DEFAULT_URL}")
    else:
        message = body["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        print(f"  {OK} answered in {seconds:.1f}s")
        if not content and reasoning:
            failures += 1
            print(f"  {WARN} empty answer, {len(reasoning)} chars of reasoning — "
                  f"this model spends its budget thinking.")
            print(f"         set DOSSIER_REASONING_EFFORT=low, or raise "
                  f"--max-tokens on the tool you are running.")

    print("\nembedding endpoint")
    body, seconds, error = probe(llm.DEFAULT_EMBED_URL, {
        "model": llm.DEFAULT_EMBED_MODEL, "input": ["probe"]})
    if error:
        failures += 1
        print(f"  {BAD} {error}")
        available = models_at(llm.DEFAULT_EMBED_URL)
        if available:
            print(f"         endpoint serves: {', '.join(available[:8])}")
        print(f"         coverage and synthesis need this; the deterministic "
              f"sweeps do not.")
    else:
        dims = len(body["data"][0]["embedding"])
        print(f"  {OK} answered in {seconds:.1f}s, {dims} dimensions")

    if args.project:
        print("\ncorpus")
        project = os.path.abspath(os.path.expanduser(args.project))
        manifest = os.path.join(project, "parsed", "MANIFEST.json")
        if not os.path.exists(manifest):
            print(f"  {WARN} not frozen — run: dossier freeze {args.project}")
        else:
            docs = json.load(open(manifest))["documents"]
            print(f"  {OK} {len(docs)} document(s) frozen")
            for doc in docs:
                print(f"         {doc['role']:12} {doc['slug']:14} "
                      f"{doc.get('text_lines', '?'):>6} lines")

    print()
    if failures:
        print(f"{failures} problem(s). The deterministic half of the toolkit "
              f"— freeze, sweep,\nclosure, review — needs none of the above and "
              f"will run regardless.")
        return 1
    print("ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
