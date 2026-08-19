#!/usr/bin/env python3
"""Freeze a document corpus into stable, hash-pinned plain text.

Findings cite locators. A locator is only worth anything if the text it points
into never moves, so the corpus is extracted ONCE, hashed, and thereafter
treated as read-only. Re-freezing is an explicit act that invalidates every
existing locator and must be followed by a cross-reference re-check.

Reads corpus.yaml from the project directory:

    extractor: tools/extract.py        # relative to project dir
    documents:
      - path: source/Foo v5.docx       # relative to project dir
        slug: arch-v5
        role: anchor                   # anchor | requirements | draft | reference
        sha256: 1f4cb95c...            # optional; if set, MUST match or we abort

Writes parsed/<slug>.txt and parsed/MANIFEST.json.

    ./freeze.py --project ~/reviews/some-engagement
    ./freeze.py --project . --check     # verify nothing drifted, write nothing

`--check` is the one to put in a pre-commit hook or a nightly job: it re-hashes
every source and every frozen output and exits non-zero on any drift.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

MANIFEST = "MANIFEST.json"

# `role` is a closed vocabulary because tooling branches on it: a coverage
# matrix has to know which document states the obligations, and a citation
# check has to know which documents are drafts and therefore not citable. A
# typo in a free-text field would not fail here — it would fail silently and
# much later, somewhere harder to trace.
#
#   anchor        the document findings are written against; exactly one
#   requirements  the contract, spec or standard the anchor is judged by
#   draft         a revision available for diffing but never for citation
#   reference     background; readable, not authoritative
#
# Plain-English description belongs in `note`, which is carried into the
# manifest untouched.
ROLES = ("anchor", "requirements", "draft", "reference")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_corpus(project):
    """Minimal YAML subset reader — avoids a PyYAML dependency on review machines."""
    path = os.path.join(project, "corpus.yaml")
    if not os.path.exists(path):
        sys.exit(f"no corpus.yaml in {project}")
    extractor, documents, current = "tools/extract.py", [], None
    for raw in open(path):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            current = {}
            documents.append(current)
            stripped = stripped[2:].strip()
            if not stripped:
                continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key == "documents":
            continue
        if key == "extractor" and current is None:
            extractor = value
        elif current is not None and value:
            current[key] = value
    return extractor, [d for d in documents if d.get("path")]


PLAIN_SUFFIXES = (".md", ".txt", ".csv")


def extract_pdf(source):
    """PDF text, preferring pypdf/PyPDF2 when present.

    Tender PDFs are usually born-digital with a clean text layer; a
    layout-reconstructing extractor tends to emit one text run per line and
    shred hyphenated words ("Human\n-\nin\n-\nthe\n-\nloop"), which then
    hands a model fragments instead of sentences. Falls back to the project
    extractor if no library is installed.
    """
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
    except ImportError:
        return None
    reader = PdfReader(source)
    pages = [(page.extract_text() or "") for page in reader.pages]
    return ("\n".join(pages)).encode()


def extract(project, extractor, relpath):
    source = os.path.join(project, relpath)
    if relpath.lower().endswith(".pdf"):
        text = extract_pdf(source)
        if text is not None:
            return text
    # Plain text is already its own extraction, and passing it through an OOXML
    # extractor would only add a banner line and shift every locator by one.
    if relpath.lower().endswith(PLAIN_SUFFIXES):
        return open(source, "rb").read()

    # Look for the extractor in the project first — a project may pin its own,
    # and re-extracting with a different one would move every locator — then
    # fall back to the copy shipped alongside this script, so a new project
    # needs no extractor of its own.
    script = os.path.join(project, extractor)
    if not os.path.exists(script):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              os.path.basename(extractor))
    if not os.path.exists(script):
        sys.exit(f"no extractor found: tried {os.path.join(project, extractor)} "
                 f"and {script}")

    result = subprocess.run([sys.executable, script, source], capture_output=True)
    if result.returncode != 0:
        sys.exit(f"extractor failed on {relpath}:\n{result.stderr.decode()[:500]}")
    return result.stdout


DOC_SUFFIXES = (".docx", ".pptx", ".xlsx", ".pdf", ".md", ".txt", ".csv")

TEMPLATE_HEADER = """# Corpus for this review. Written by `freeze.py --init`, then edited by hand.
#
# One entry per document:
#
#   path    where the file is, relative to this folder            (required)
#   slug    the short name you pass to --doc                      (required)
#   role    anchor | requirements | draft | reference             (default: reference)
#   sha256  pin the file's identity; prefix is fine               (optional)
#   note    free text, carried into parsed/MANIFEST.json          (optional)
#
# The four roles:
#
#   anchor        the document findings are written against. Exactly one.
#   requirements  the contract, spec or standard it is judged against.
#   draft         a revision you want to diff but must never cite.
#   reference     background: quotable, but not under review.
#
# --init sets every role to `reference` because it cannot know which document
# you are reviewing. Set exactly one `anchor` before freezing.

"""


def slugify(filename):
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return re.sub(r"-{2,}", "-", stem)[:40] or "doc"


def init_corpus(project, scan):
    target = os.path.join(project, "corpus.yaml")
    if os.path.exists(target):
        sys.exit(f"corpus.yaml already exists at {target} — refusing to overwrite")

    root = os.path.join(project, scan) if scan else project
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "parsed", "exports")]
        for name in sorted(filenames):
            if name.startswith(".") or not name.lower().endswith(DOC_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            found.append((os.path.relpath(full, project), sha256(full)[:12]))

    if not found:
        sys.exit(f"no documents found under {root}")

    seen, lines = {}, [TEMPLATE_HEADER, "documents:\n"]
    for relpath, digest in sorted(found):
        slug = slugify(relpath)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        lines.append(f"  - path: {relpath}\n")
        lines.append(f"    slug: {slug}\n")
        lines.append(f"    role: reference\n")
        lines.append(f"    sha256: {digest}\n\n")

    with open(target, "w") as handle:
        handle.write("".join(lines))

    print(f"wrote {os.path.relpath(target, project)} with {len(found)} document(s)")
    for relpath, digest in sorted(found):
        print(f"  {digest}  {relpath}")
    print("\nNow edit it: set exactly one `role: anchor`, mark the requirements")
    print("source, and delete anything you do not want in the corpus.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--check", action="store_true",
                        help="verify sources and frozen text still match the manifest")
    parser.add_argument("--refreeze", action="store_true",
                        help="re-extract even if already frozen (INVALIDATES LOCATORS)")
    parser.add_argument("--init", action="store_true",
                        help="scan the project for documents and write a starter "
                             "corpus.yaml, hashes filled in")
    parser.add_argument("--scan", default=None,
                        help="--init: subdirectory to scan (default: whole project)")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    if args.init:
        return init_corpus(project, args.scan)
    extractor, documents = load_corpus(project)
    parsed_dir = os.path.join(project, "parsed")
    manifest_path = os.path.join(parsed_dir, MANIFEST)
    previous = {}
    if os.path.exists(manifest_path):
        previous = {d["slug"]: d for d in json.load(open(manifest_path))["documents"]}

    os.makedirs(parsed_dir, exist_ok=True)
    entries, drift, wrote = [], [], []

    for doc in documents:
        relpath, slug = doc["path"], doc["slug"]
        role = doc.get("role", "reference")
        if role not in ROLES:
            sys.exit(f"{slug}: role {role!r} not one of {ROLES}\n"
                     f"  role drives tool behaviour and is a fixed set; put a "
                     f"plain-English description in `note:` instead.")
        source = os.path.join(project, relpath)
        if not os.path.exists(source):
            sys.exit(f"{slug}: missing source {relpath}")

        source_hash = sha256(source)
        if doc.get("sha256") and not source_hash.startswith(doc["sha256"]):
            sys.exit(f"{slug}: source hash mismatch\n  pinned {doc['sha256']}\n"
                     f"  actual {source_hash[:len(doc['sha256'])]}\n"
                     f"  {relpath}\nRefusing to freeze the wrong file.")

        out_path = os.path.join(parsed_dir, slug + ".txt")
        prior = previous.get(slug)
        frozen = os.path.exists(out_path)

        if args.check or (frozen and not args.refreeze):
            if not frozen:
                drift.append(f"{slug}: never frozen")
                continue
            text = open(out_path, "rb").read()
            text_hash = sha256_bytes(text)
            if prior:
                if prior["source_sha256"] != source_hash:
                    drift.append(f"{slug}: SOURCE changed since freeze")
                if prior["text_sha256"] != text_hash:
                    drift.append(f"{slug}: FROZEN TEXT edited since freeze")
        else:
            text = extract(project, extractor, relpath)
            with open(out_path, "wb") as handle:
                handle.write(text)
            text_hash = sha256_bytes(text)
            wrote.append(slug)

        entry = {
            "slug": slug, "role": role, "path": relpath,
            "source_sha256": source_hash,
            "source_bytes": os.path.getsize(source),
            "text_sha256": text_hash,
            # splitlines(), not count("\n")+1. A file ending in a newline has
            # no empty final line, and the +1 counted one anyway: every sweep
            # header reported 5961 lines for a 5960-line document, one line
            # further than any locator it printed could reach.
            "text_lines": len(text.decode("utf-8", "replace").splitlines()),
            "parsed": os.path.relpath(out_path, project),
        }
        if doc.get("note"):
            entry["note"] = doc["note"]
        entries.append(entry)

    if args.check:
        for entry in entries:
            print(f"  {entry['role']:12} {entry['slug']:14} "
                  f"{entry['text_lines']:6} lines  {entry['source_sha256'][:12]}")
        if drift:
            print("\nDRIFT:", file=sys.stderr)
            for item in drift:
                print("  " + item, file=sys.stderr)
            print("\nLocators may no longer resolve. Re-run cross-reference checks.",
                  file=sys.stderr)
            return 1
        print("\nno drift — every source and frozen text matches the manifest")
        return 0

    with open(manifest_path, "w") as handle:
        json.dump({"extractor": extractor, "documents": entries}, handle, indent=2)
        handle.write("\n")

    anchors = [e["slug"] for e in entries if e["role"] == "anchor"]
    for entry in entries:
        mark = "*" if entry["slug"] in wrote else " "
        print(f" {mark} {entry['role']:12} {entry['slug']:14} "
              f"{entry['text_lines']:6} lines  {entry['source_sha256'][:12]}")
    print(f"\nfroze {len(wrote)} document(s); manifest at "
          f"{os.path.relpath(manifest_path, project)}")
    if len(anchors) != 1:
        print(f"warning: {len(anchors)} documents marked 'anchor' (expected exactly 1)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
