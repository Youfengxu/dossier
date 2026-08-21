#!/usr/bin/env python3
"""Check that a record reader obeys the contract, rather than being said to.

    ./conformance.py                    # exits non-zero on any violation

`ADAPTERS.md` states six invariants. Until this existed they were prose, and the
repository has already demonstrated twice what prose is worth: two extractor bugs
survived a mutation-checked test suite because no gate ever ran the extractor, and
four tools "answered --help" for months by ignoring it and exiting 0.

WHAT THIS CAN AND CANNOT CHECK TODAY. The `adapters/` package described in the
contract does not exist yet; `matrix.read_sheet` is the de facto adapter, with two
format paths behind one entry point. So the record-model, nasty-input,
cross-format and determinism checks are real and run against real code. The
address-law and round-trip checks belong to the unit/locate half of the contract,
which has no implementation to point at — they are listed by name and reported as
NOT IMPLEMENTED rather than quietly omitted, because a harness that silently
skips half its own specification is the failure mode it was built to prevent.

CROSS-FORMAT AGREEMENT is the one the contract singles out: the same table
expressed two ways must produce identical records. It is checked against a
committed .xlsx twin rather than one generated here, because a generated twin can
only ever agree with its generator.
"""

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import matrix                                                   # noqa: E402

ROOT = os.path.dirname(os.path.realpath(__file__))
FIXTURES = os.path.join(ROOT, "fixtures", "floodtwin")


class Violation(Exception):
    pass


def check(name, fn, results):
    try:
        detail = fn()
        results.append(("pass", name, detail or ""))
    except Violation as exc:
        results.append(("FAIL", name, str(exc)))
    except Exception as exc:                    # a crash IS a conformance failure
        results.append(("FAIL", name, f"{type(exc).__name__}: {exc}"))


def want(condition, message):
    if not condition:
        raise Violation(message)


def temp(text, suffix=".csv", binary=False):
    path = os.path.join(tempfile.mkdtemp(), "t" + suffix)
    mode, encoding = ("wb", None) if binary else ("w", "utf-8")
    with open(path, mode, newline="" if not binary else None,
              encoding=encoding) as handle:
        handle.write(text)
    return path


# ---------------------------------------------------------------- the checks

def cross_format_agreement():
    """The same fourteen comments as .csv and as .xlsx, read by the same call."""
    csv_rows = matrix.read_sheet(os.path.join(FIXTURES, "comments.csv"), "Comments")
    xlsx_rows = matrix.read_sheet(os.path.join(FIXTURES, "comments.xlsx"), "Comments")
    want(len(csv_rows) == len(xlsx_rows),
         f"row counts differ: csv {len(csv_rows)}, xlsx {len(xlsx_rows)}")
    for a, b in zip(csv_rows, xlsx_rows):
        want(a == b, f"row {a.get(matrix.ROW_KEY)} differs:\n"
                     f"      csv  {a}\n      xlsx {b}")
    return f"{len(csv_rows)} rows identical across both formats"


def record_model():
    """Values are str, never None; the header is a record; __row__ is correct."""
    for name in ("comments.csv", "comments.xlsx"):
        rows = matrix.read_sheet(os.path.join(FIXTURES, name), "Comments")
        want(rows, f"{name}: no records")
        want(rows[0]["A"] == "id", f"{name}: header not returned as the first record")
        for row in rows:
            want(matrix.ROW_KEY in row, f"{name}: a record without {matrix.ROW_KEY}")
            want(isinstance(row[matrix.ROW_KEY], int),
                 f"{name}: {matrix.ROW_KEY} is not an int")
            for letter, value in row.items():
                if letter == matrix.ROW_KEY:
                    continue
                want(isinstance(value, str),
                     f"{name}: {letter}{row[matrix.ROW_KEY]} is {type(value).__name__}, "
                     f"not str")
        want(matrix.ROW_KEY not in matrix.columns(rows),
             f"{name}: the bookkeeping key leaks out of columns()")
    return "str values, header consumed, row key present and typed"


def row_key_survives_a_gap():
    """The defect the key exists for: list position and sheet row diverge."""
    rows = matrix.read_sheet(temp("id,ref\nC-1,6.3\n,\nC-2,7.1\n"), "x")
    want([r["A"] for r in rows] == ["id", "C-1", "C-2"],
         f"blank row not dropped: {[r['A'] for r in rows]}")
    want(rows[2][matrix.ROW_KEY] == 4,
         f"C-2 reports row {rows[2][matrix.ROW_KEY]}, sheet row is 4")
    return "blank row dropped, physical row preserved"


def nasty_inputs():
    """Every one of these is a real export someone has handed a review team."""
    cases = []

    rows = matrix.read_sheet(temp("﻿id,ref\nC-1,6.3\n"), "x")
    want(rows[0]["A"] == "id", f"byte-order mark not stripped: {rows[0]['A']!r}")
    cases.append("BOM")

    rows = matrix.read_sheet(temp("id,comment,comment\nC-1,first,second\n"), "x")
    want((rows[1]["B"], rows[1]["C"]) == ("first", "second"),
         "duplicate header names collided")
    cases.append("duplicate headers")

    rows = matrix.read_sheet(temp("id,,ref\nC-1,mid,6.3\n"), "x")
    want(rows[1]["B"] == "mid", "a column with a blank header became unaddressable")
    cases.append("blank header")

    rows = matrix.read_sheet(temp("id,ref,comment\nC-1,6.3\nC-2\n"), "x")
    want(rows[1].get("C", "") == "", "a short row invented a value")
    want(rows[2]["A"] == "C-2", "a short row was dropped")
    cases.append("ragged rows")

    rows = matrix.read_sheet(temp('id,comment\nC-1,"two\nlines"\nC-2,plain\n'), "x")
    want(rows[2][matrix.ROW_KEY] == 3,
         f"embedded newline shifted the row key to {rows[2][matrix.ROW_KEY]}")
    cases.append("embedded newline")

    want(matrix.read_sheet(temp(""), "x") == [], "an empty file produced records")
    cases.append("empty file")

    rows = matrix.read_sheet(temp("id,ref\n"), "x")
    want(len(rows) == 1, f"header-only file produced {len(rows)} records")
    cases.append("header-only")

    rows = matrix.read_sheet(temp("id;ref\nC-1;6.3\n"), "x")
    want(rows[1]["B"] == "6.3", "semicolon export read as one column")
    cases.append("semicolon")

    return ", ".join(cases)


def determinism():
    """Parse in two processes under different ambient state; require byte equality.

    Invariant 1 names the class — no absolute paths, no locale, no timezone, no
    unordered iteration, no environment. The absolute-path instance was found by
    someone going looking. This is the thing that would have failed instead.
    """
    script = ("import json,sys; sys.path.insert(0, %r); import matrix; "
              "print(json.dumps(matrix.read_sheet(sys.argv[1], 'Comments'), "
              "sort_keys=True))" % ROOT)
    environments = [
        {"PYTHONHASHSEED": "0", "LC_ALL": "C", "TZ": "UTC"},
        {"PYTHONHASHSEED": "random", "LC_ALL": "de_DE.UTF-8", "TZ": "Pacific/Kiritimati"},
    ]
    working = [ROOT, tempfile.mkdtemp()]
    for name in ("comments.csv", "comments.xlsx"):
        target = os.path.join(FIXTURES, name)
        seen = []
        for env, cwd in zip(environments, working):
            done = subprocess.run([sys.executable, "-c", script, target],
                                  capture_output=True, text=True, cwd=cwd,
                                  env=dict(os.environ, **env))
            want(done.returncode == 0, f"{name}: reader failed under {env}:\n"
                                       f"      {done.stderr.strip()[:200]}")
            seen.append(done.stdout)
        want(seen[0] == seen[1],
             f"{name}: two environments produced different records")

    # The extractor writes text that gets hashed, so it carries the same duty.
    docx = os.path.join(FIXTURES, "extractor-smoke.docx")
    outputs = []
    for env, cwd in zip(environments, working):
        done = subprocess.run([sys.executable, os.path.join(ROOT, "extract.py"), docx],
                              capture_output=True, cwd=cwd,
                              env=dict(os.environ, **env))
        want(done.returncode == 0, "extractor failed")
        outputs.append(done.stdout)
    want(outputs[0] == outputs[1],
         "the extractor produced different bytes in two environments — the "
         "frozen text, and therefore text_sha256, depends on ambient state")
    return "readers and extractor byte-identical across hashseed, locale, tz, cwd"


def twin_has_not_drifted():
    """The .xlsx twin is a committed file, so it can go stale against its source.

    Compared by bytes rather than by `git status`, which calls a staged file
    changed and would make the result depend on where in a workflow this ran."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "twin", os.path.join(FIXTURES, "make-xlsx-twin.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected, records, _strings = module.build()
    on_disk = open(os.path.join(FIXTURES, "comments.xlsx"), "rb").read()
    want(on_disk == expected,
         "comments.xlsx no longer matches what comments.csv generates — "
         "regenerate it with fixtures/floodtwin/make-xlsx-twin.py")
    return f"committed twin matches its source ({records} rows)"


def ooxml_writes_are_reproducible():
    """No zip entry may carry a wall-clock stamp.

    Found by this harness rather than by reading: the committed .xlsx twin failed
    the drift check while its contents were byte-identical, because zipfile stamps
    each entry with the current time unless told otherwise. Two builds a second
    apart differed; two in the same second did not, which is why a naive
    "generate twice and compare" had already passed.

    Checked by inspecting the stamps rather than by building twice and sleeping —
    a test that needs a delay to fail is a test that will be deleted."""
    import zipfile
    from writeback import ZIP_EPOCH

    twin = os.path.join(FIXTURES, "comments.xlsx")
    for info in zipfile.ZipFile(twin).infolist():
        want(info.date_time == ZIP_EPOCH,
             f"{os.path.basename(twin)}:{info.filename} is stamped "
             f"{info.date_time}, not the fixed epoch — the file cannot be "
             f"regenerated byte-for-byte")

    # An annotated copy must inherit the source's stamps, not take new ones.
    import writeback
    out = os.path.join(tempfile.mkdtemp(), "annotated.xlsx")
    writeback.annotate(twin, out, "Comments", {"1": "verdict"}, "F", True)
    for info in zipfile.ZipFile(out).infolist():
        want(info.date_time == ZIP_EPOCH,
             f"annotate() re-stamped {info.filename} as {info.date_time}")
    return "fixture and annotated copy both carry the fixed epoch"


# ---------------------------------------------------- proving the gate bites
#
# `run-tests.py --mutate` exists because a test that passes against broken code
# turns an absence of checking into a claim of checking. A conformance harness has
# the same exposure and had no equivalent: nothing established that any of the
# checks above could fail. These break the record adapter in ways a contributed
# one would plausibly be broken, and require the named check to notice.
#
# Anchors are asserted unique before use. The mutation entry in run-tests.py was
# silently disarmed twice tonight — once by a rename, once by a second copy of the
# anchored line appearing elsewhere in the file — and both times the harness
# reported "nothing pins this" when it meant "I cannot find the thing that does".
ADAPTER_MUTATIONS = [
    {"what": "blank rows are no longer dropped",
     "old": "            if any(v.strip() for v in cells.values()):\n"
            "                cells[ROW_KEY] = record",
     "new": "            if True:\n"
            "                cells[ROW_KEY] = record",
     "expect": "row key across a gap"},
    {"what": "the row key is list position, not the physical record",
     "old": "                cells[ROW_KEY] = record",
     "new": "                cells[ROW_KEY] = len(rows) + 1",
     "expect": "row key across a gap"},
    {"what": "the byte-order mark is not stripped",
     "old": 'with open(path, newline="", encoding="utf-8-sig") as handle:',
     "new": 'with open(path, newline="", encoding="utf-8") as handle:',
     "expect": "nasty inputs"},
    {"what": "the delimiter is assumed rather than sniffed",
     "old": '            dialect = csv.Sniffer().sniff(sample, delimiters=",;\\t|")',
     "new": "            dialect = csv.excel",
     "expect": "nasty inputs"},
    {"what": "the bookkeeping key leaks out of columns()",
     "old": "    seen = {k for row in rows for k in row if k != ROW_KEY}",
     "new": "    seen = {k for row in rows for k in row}",
     "expect": "record model"},
]

CHECKS = None            # filled in by main(), so prove() can re-run them


def prove():
    """Break the adapter five ways; require the named check to catch each."""
    path = os.path.join(ROOT, "matrix.py")
    original = open(path, encoding="utf-8").read()
    restore = compile(original, path, "exec")
    caught, missed = 0, []

    print("proving the checks bite — each entry breaks the record adapter")
    print("=" * 74)
    for mutation in ADAPTER_MUTATIONS:
        occurrences = original.count(mutation["old"])
        if occurrences != 1:
            print(f"  BROKEN ANCHOR  {mutation['what']}")
            print(f"                 patches {occurrences} places in matrix.py, "
                  f"must be exactly 1")
            missed.append(mutation["what"])
            continue
        try:
            exec(compile(original.replace(mutation["old"], mutation["new"]),
                         path, "exec"), matrix.__dict__)
            results = []
            for name, fn in CHECKS:
                check(name, fn, results)
        finally:
            exec(restore, matrix.__dict__)

        failed = {name for status, name, _ in results if status == "FAIL"}
        if mutation["expect"] in failed:
            print(f"  caught  {mutation['what']}")
            caught += 1
        elif failed:
            print(f"  caught  {mutation['what']}")
            print(f"          (by {', '.join(sorted(failed))}, not the expected "
                  f"{mutation['expect']!r} — still caught, but the mapping is stale)")
            caught += 1
        else:
            print(f"  MISSED  {mutation['what']}")
            print(f"          every check still passed against a broken adapter")
            missed.append(mutation["what"])

    print("=" * 74)
    print(f"  {caught}/{len(ADAPTER_MUTATIONS)} adapter mutations caught")
    if missed:
        print("\n  A conformance check that cannot fail is not a check. Fix the "
              "check,\n  not this list.")
    return 1 if missed else 0


NOT_IMPLEMENTED = [
    ("address law", "start <= locate(address(start, end)).start <= end — needs the "
                    "Unit/locate half of the contract, which has no implementation"),
    ("unit round trip", "parse(p).units == resplit(join(parse(p).units)) — same"),
]


def main():
    # A real --help. The gate that checks every tool answers it was itself passing
    # on four tools that ignored it, so adding a tool without one would quietly
    # widen that hole rather than being caught by it.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        return 0
    global CHECKS
    CHECKS = (("cross-format agreement", cross_format_agreement),
              ("record model", record_model),
              ("row key across a gap", row_key_survives_a_gap),
              ("nasty inputs", nasty_inputs),
              ("determinism", determinism),
              ("committed twin is current", twin_has_not_drifted),
              ("ooxml writes reproducible", ooxml_writes_are_reproducible))

    if any(a == "--prove" for a in sys.argv[1:]):
        return prove()

    print(__doc__.strip().split("\n")[0])
    print("=" * 74)
    results = []
    for name, fn in CHECKS:
        check(name, fn, results)

    for status, name, detail in results:
        print(f"  {status:>4}  {name}")
        if detail:
            for line in str(detail).splitlines():
                print(f"        {line}")
    for name, why in NOT_IMPLEMENTED:
        print(f"  ----  {name}")
        print(f"        NOT IMPLEMENTED — {why}")

    failed = [name for status, name, _ in results if status == "FAIL"]
    print("=" * 74)
    print(f"  {len(results) - len(failed)}/{len(results)} checks pass, "
          f"{len(NOT_IMPLEMENTED)} not implemented")
    if failed:
        print("\n  An adapter that violates the contract is not registered. Fix the "
              "adapter,\n  or amend the contract deliberately — but do not leave "
              "them disagreeing.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
