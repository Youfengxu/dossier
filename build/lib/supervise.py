#!/usr/bin/env python3
"""Run a command until it succeeds, diagnosing the endpoint between attempts.

    ./supervise.py --url http://localhost:8085/v1/models \
        --restart "launchctl kickstart -k gui/501/local.llama-swap" \
        --attempts 6 -- python3 panel.py --project . ...

This exists because the thing it supervises is resumable. `panel.py` writes every
answer as it arrives and skips what is already on disk, so a retry costs one
re-prefill rather than the run. That property is what makes a dumb retry loop the
right supervisor: there is no state to reconcile, no partial write to repair, and
nothing to clean up before trying again.

So the loop is deliberately small. It distinguishes only the cases with different
remedies:

  endpoint unreachable   restart the server, wait for /v1/models, retry
  model not served       nothing to restart into -- stop and say so
  endpoint healthy but
  the command failed     retry with backoff; a transient 5xx or an eviction

Anything it cannot classify it retries anyway, up to the cap, and then stops with
the last output rather than looping forever. A supervisor that never gives up
hides the failure it was supposed to surface.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request


def probe(url, timeout=10):
    """(reachable, [model ids])"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as f:
            return True, [m.get("id", "") for m in json.load(f).get("data", [])]
    except Exception:
        return False, []


def wait_ready(url, model, deadline):
    """Poll until the server answers and serves `model`. Loading a 60 GB
    checkpoint is minutes, not seconds, so this waits rather than guesses."""
    end = time.time() + deadline
    while time.time() < end:
        ok, models = probe(url)
        if ok and (not model or any(m == model or m.endswith(model) for m in models)):
            return True
        time.sleep(15)
    return False


def log(message):
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True, help="/v1/models to probe")
    p.add_argument("--model", default="", help="model that must be served")
    p.add_argument("--restart", default="", help="shell command to restart it")
    p.add_argument("--attempts", type=int, default=6)
    p.add_argument("--ready-wait", type=int, default=900)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    args = p.parse_args()

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        sys.exit("nothing to run; put the command after --")

    for attempt in range(1, args.attempts + 1):
        log(f"ATTEMPT {attempt}/{args.attempts}: {' '.join(cmd[:6])}...")
        result = subprocess.run(cmd)
        if result.returncode == 0:
            log("SUCCESS")
            return 0

        log(f"FAILED exit={result.returncode}")
        reachable, models = probe(args.url)
        if not reachable:
            log("DIAGNOSIS: endpoint unreachable")
            if args.restart:
                log(f"REMEDY: {args.restart}")
                subprocess.run(args.restart, shell=True)
                if wait_ready(args.url, args.model, args.ready_wait):
                    log("endpoint back")
                else:
                    log("ERROR: endpoint did not come back in time")
        elif args.model and not any(m == args.model or m.endswith(args.model)
                                    for m in models):
            # Restarting cannot conjure a model the server does not have. This is
            # a configuration problem and looping on it just wastes the window.
            log(f"DIAGNOSIS: {args.model!r} is not served here. "
                f"Available: {', '.join(models[:8])}")
            log("STOPPING: no remedy for a missing model")
            return 5
        else:
            log("DIAGNOSIS: endpoint healthy; treating as transient")

        if attempt < args.attempts:
            backoff = min(30 * attempt, 180)
            log(f"retrying in {backoff}s")
            time.sleep(backoff)

    log(f"GIVING UP after {args.attempts} attempts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
