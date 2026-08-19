#!/usr/bin/env python3
"""Fetch the pinned Kubernetes Enhancement Proposal corpus.

The documents are NOT vendored into this repository. They are fetched on demand
from kubernetes/enhancements at pinned commit SHAs, so the corpus is
reproducible without carrying several thousand lines of someone else's Apache-2.0
text in our git history.

    ./fetch.py            # fetch anything missing, verify every hash
    ./fetch.py --force    # re-fetch everything
    ./fetch.py --update   # re-fetch and PRINT new hashes (after changing a pin)

Every entry is pinned twice over: by commit SHA in the URL, and by SHA-256 of the
content. A mismatch is a hard error — the same discipline freeze.py applies to a
client corpus, for the same reason. Upstream rewriting history or a proxy
injecting content should stop the run, not silently change what is under review.

Licence: kubernetes/enhancements is Apache-2.0. Fetching leaves the licence
question upstream where it belongs.
"""

import argparse
import hashlib
import os
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/kubernetes/enhancements"
HERE = os.path.dirname(os.path.abspath(__file__))

# slug, commit sha, path in repo, expected sha256 of the content ("" until pinned)
SOURCES = [
    ("prr-template", "6ab9bf717d1228928740bdbfe761b6e62b870902",
     "keps/NNNN-kep-template/README.md", "38ed9f6e5c99bf72"),
    ("kep-2400", "d03d5a6e577af7010170a6eeabca2691a38f1b52",
     "keps/sig-node/2400-node-swap/README.md", "77ff857a211e65df"),
    ("kep-2400-2024", "dfb0ff13627f9f4df41ca2504d82c6d253744623",
     "keps/sig-node/2400-node-swap/README.md", "fb8fc454a2222a3e"),
    ("kep-1287", "d47a8df46c8c26d6300fd30047520e397127805c",
     "keps/sig-node/1287-in-place-update-pod-resources/README.md", "97879308d54aef0c"),
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--update", action="store_true",
                        help="re-fetch and print hashes to paste back into SOURCES")
    args = parser.parse_args()

    out_dir = os.path.join(HERE, "source")
    os.makedirs(out_dir, exist_ok=True)
    failures, printed = [], []

    for slug, commit, path, expected in SOURCES:
        target = os.path.join(out_dir, slug + ".md")
        if os.path.exists(target) and not (args.force or args.update):
            data = open(target, "rb").read()
        else:
            url = f"{RAW}/{commit}/{path}"
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    data = response.read()
            except Exception as exc:                        # noqa: BLE001
                failures.append(f"{slug}: {exc}")
                print(f"  !! {slug:16} fetch failed: {exc}")
                continue
            with open(target, "wb") as handle:
                handle.write(data)

        digest = sha256(data)
        if expected and digest != expected and not args.update:
            failures.append(f"{slug}: content hash mismatch\n"
                            f"     pinned {expected}\n     actual {digest}")
            print(f"  !! {slug:16} HASH MISMATCH")
            continue
        lines = data.count(b"\n") + 1
        state = "pinned" if expected else "UNPINNED"
        print(f"  ok {slug:16} {digest[:16]}  {lines:5} lines  {state}")
        printed.append((slug, digest))

    if args.update:
        print("\nPaste these into SOURCES and into corpus.yaml:")
        for slug, digest in printed:
            print(f'  {slug:16} {digest[:16]}')

    if failures:
        print("\nFAILED:", file=sys.stderr)
        for item in failures:
            print("  " + item, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
