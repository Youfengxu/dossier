#!/usr/bin/env python3
"""Run the test suite for the deterministic core. Stdlib only, no network.

    ./run-tests.py                 # everything
    ./run-tests.py closure matrix  # only these modules
    ./run-tests.py -v              # one line per test
    ./run-tests.py --mutate        # check that the tests can still fail

WHY THIS EXISTS. 1,831 lines across closure.py, freeze.py, sweep.py, matrix.py,
extract.py and writeback.py decided what a finding is, with no test and no
assert. In one day that code produced a heading boundary that sliced nested
sections to zero length, a row-alignment invariant contradicted one line below
its own comment, a cache that corrupted entries under concurrency, a hash
compared against a truncated pin, and two scorers that measured nothing. Every
one is what a unit test catches. Every one was found by accident.

The CI already had three gates and none of them checked an answer: they check
that nothing names the client, that no citation dangles, and that every tool
loads. A tool that loads and returns the wrong verdict passes all three.

WHY --mutate. DESIGN §3.11 — a failed check is not a passed check — applies to
the tests as much as to anything they cover. A test that passes against broken
code is worse than no test, because it converts an absence of checking into a
claim of checking. --mutate breaks one function at a time, in memory, and
requires the named tests to fail. If a mutation is not caught, the run fails:
the suite is reporting confidence it has not earned.

Nothing here reaches a model or an endpoint. That is deliberate — DESIGN §3.17,
where a deterministic check exists it wins — and it is why this can run on every
push next to the other three gates.
"""

import argparse
import io
import os
import re
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.realpath(__file__))

# Each entry breaks ONE decision in ONE function and names the tests that must
# notice. The fragment must still be present in the source; if it is not, the
# mutation is stale and the run says so rather than quietly checking nothing —
# a mutation that no longer applies is the same failure as a test that cannot
# fail.
MUTATIONS = [
    {
        "what": "closure.classify — absence and broken-query verdicts swapped",
        "why": "a mistyped search term then reports 'still missing' with the "
               "same confidence as a real finding",
        "module": "closure",
        "old": '        return ("STILL ABSENT", None) if is_absence else ("ABSENT", None)',
        "new": '        return ("ABSENT", None) if is_absence else ("STILL ABSENT", None)',
        "tests": [
            "tests.test_closure.Classify."
            "test_nothing_anywhere_is_absent_when_the_finding_is_not_an_absence",
            "tests.test_closure.Classify."
            "test_nothing_anywhere_is_still_absent_when_the_register_says_so",
        ],
    },
    {
        "what": "synthesize.reached — anchor coverage threshold dropped to zero",
        "why": "this is the previous scorer, which a bag of the document's own "
               "nouns scored 6/6 against",
        "module": "synthesize",
        "old": "            if match.size >= ANCHOR_COVERAGE * len(anchor):",
        "new": "            if match.size >= 0 * len(anchor):",
        "tests": [
            "tests.test_synthesize.ReachedRejectsAWordBag."
            "test_rejects_the_anchors_own_words_in_scrambled_order",
            "tests.test_synthesize.ReachedRejectsAWordBag."
            "test_rejects_a_bag_of_the_documents_own_words",
            "tests.test_synthesize.ReachedGates."
            "test_coverage_threshold_is_a_fraction_of_the_anchor",
        ],
    },
    {
        "what": "undefined.same — the containment length floor removed",
        "why": "bare 'order' then satisfies 'frozen execution order', and the "
               "scorer starts rewarding vague output",
        "module": "undefined",
        "old": "                return len(short) >= 0.6 * len(long)",
        "new": "                return True",
        "tests": [
            "tests.test_undefined.Same.test_a_bare_word_inside_a_phrase_does_not_match",
            "tests.test_undefined.Same.test_the_contained_side_must_be_most_of_the_container",
        ],
    },
    {
        "what": "matrix.read_sheet — blank rows no longer dropped",
        "why": "the behaviour the row-alignment defect rests on; if changing it "
               "breaks nothing, nothing was pinning it. NOTE: this entry named a "
               "test that was later renamed out from under it, and the mutation "
               "then read as UNCAUGHT rather than as a broken name — so a rename "
               "can quietly disarm a mutation. If you rename a test, grep here.",
        "module": "matrix",
        # Anchored on the line above it as well. The bare `if any(...)` line
        # appeared verbatim in read_csv once delimited input landed, and a
        # substring patch then rewrote BOTH -- breaking the indentation of the
        # other and turning a mutation into an import error, which reads as
        # UNCAUGHT rather than as the harness misfiring.
        "old": ('            cells[re.sub(r"\\d", "", cell.get("r"))] = value(cell)\n'
                '        if any(v.strip() for v in cells.values()):'),
        "new": ('            cells[re.sub(r"\\d", "", cell.get("r"))] = value(cell)\n'
                '        if True:'),
        "tests": [
            "tests.test_matrix.BlankRows.test_a_row_of_empty_cells_is_dropped",
            "tests.test_matrix.BlankRows.test_list_position_still_diverges_but_the_row_key_does_not",
        ],
    },
    {
        "what": "extract.docx — runs joined with a space",
        "why": "Word splits words across runs, so this inserts a space into the "
               "middle of every term an anchor quotes",
        "module": "extract",
        "old": "            txt = ''.join(t.text or '' for t in el.iter('{%s}t' % NS['w']))",
        "new": "            txt = ' '.join(t.text or '' for t in el.iter('{%s}t' % NS['w']))",
        "tests": [
            "tests.test_extract.Docx.test_runs_inside_a_paragraph_are_joined_with_no_separator",
        ],
    },
    {
        "what": "sweep.headings_of — the table-of-contents filter removed",
        "why": "the heading set then doubles and a real deletion is buried in "
               "the churn",
        "module": "sweep",
        "old": '                    if re.search(r"[^\\s\\d]\\d{1,4}$", stripped):',
        "new": "                    if False:",
        "tests": [
            "tests.test_chunking.SweepHeadings.test_a_table_of_contents_line_is_excluded",
            "tests.test_chunking.SweepHeadings."
            "test_bug_a_heading_ending_in_an_identifier_is_discarded",
        ],
    },
    {
        "what": "inventory.split_sections — the minimum-lines floor removed",
        "why": "changes which heading owns which passage, and every locator "
               "undefined.py prints is a section start",
        "module": "inventory",
        "old": "def split_sections(lines, min_lines=4, max_lines=90, max_chars=1200):",
        "new": "def split_sections(lines, min_lines=0, max_lines=90, max_chars=1200):",
        "tests": [
            "tests.test_chunking.InventorySections."
            "test_bug_a_heading_arriving_too_soon_is_swallowed_as_body_text",
            "tests.test_chunking.InventorySections."
            "test_bug_the_fixtures_own_subsections_are_swallowed",
        ],
    },
    {
        "what": "freeze.slugify — the empty-slug fallback removed",
        "why": "a document then freezes to parsed/.txt, which no --doc argument "
               "can name",
        "module": "freeze",
        "old": '    return re.sub(r"-{2,}", "-", stem)[:40] or "doc"',
        "new": '    return re.sub(r"-{2,}", "-", stem)[:40]',
        "tests": [
            "tests.test_freeze.Slugify.test_a_filename_with_nothing_usable_still_gets_a_slug",
        ],
    },
    {
        "what": "trace.chunk — the character cap disabled",
        "why": "one passage then holds most of an unwrapped contract, useless "
               "to retrieve and too large to send",
        "module": "trace",
        "old": "                    sum(len(x) for x in current) >= max_chars:",
        "new": "                    False:",
        "tests": [
            "tests.test_chunking.TraceChunk.test_the_character_cap_splits_unwrapped_prose",
        ],
    },
]


def load(names):
    loader = unittest.TestLoader()
    if not names:
        return loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    suite = unittest.TestSuite()
    for name in names:
        if not name.startswith("tests."):
            name = f"tests.test_{name}"
        suite.addTests(loader.loadTestsFromName(name))
    return suite


def cause(trace_text):
    """The assertion, and the line that made it — not the whole traceback.

    unittest ends a failure with a multi-line diff, so the LAST line is
    routinely "?    ^^ ^" and says nothing. The useful pair is the exception
    line and the frame inside tests/ that raised it.
    """
    lines = trace_text.strip().splitlines()
    message = next((l for l in lines if re.match(r"^\w*(Error|Exception):", l)),
                   lines[-1])
    where = ""
    for line in lines:
        found = re.match(r'\s*File "(.+/tests/[^"]+)", line (\d+)', line)
        if found:
            where = f"{os.path.basename(found.group(1))}:{found.group(2)}"
    return where, message.strip()


def report(result, seconds, stream=sys.stdout):
    """One summary line, then the failures with their assertion, and nothing
    else. A runner that prints more than the failures makes the failures harder
    to find, which is the only job it has on the day it matters."""
    broken = result.failures + result.errors
    print(f"\n{'-' * 74}", file=stream)
    if broken:
        for case, trace_text in broken:
            where, message = cause(trace_text)
            print(f"  FAIL  {case.id()}", file=stream)
            print(f"        {where}  {message[:88]}", file=stream)
        print(file=stream)
    print(f"  {result.testsRun} tests, {len(result.failures)} failed, "
          f"{len(result.errors)} errored, {seconds:.2f}s", file=stream)
    return not broken


def run(names, verbosity):
    # At verbosity 0 the per-test stream is discarded and only report() speaks.
    # That is the mode --mutate uses for its precondition run, where unittest's
    # own "OK" between the summary and the mutation results reads as a result
    # for the mutation check rather than for the suite.
    started = time.time()
    stream = sys.stderr if verbosity else io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=verbosity).run(
        load(names))
    return report(result, time.time() - started)


def mutate():
    """Break one function at a time and require the named tests to notice.

    The mutation is applied by re-executing the module's source into the module
    object that is already imported, so every reference to it — including the
    nested functions lifted out of main() — sees the change. Nothing on disk is
    touched: a mutation run that crashed halfway through and left a file
    modified would be a far worse failure than the one it was checking for.
    """
    print("mutation check — each entry breaks one function and requires the "
          "tests to fail\n" + "=" * 74)
    caught, missed = 0, []
    for mutation in MUTATIONS:
        path = os.path.join(ROOT, mutation["module"] + ".py")
        original = open(path, encoding="utf-8").read()
        if original.count(mutation["old"]) != 1:
            print(f"\n  STALE  {mutation['what']}")
            print(f"         the line it patches is no longer in "
                  f"{mutation['module']}.py — update or drop this mutation")
            missed.append(("STALE ANCHOR", mutation["what"]))
            continue

        module = __import__(mutation["module"])
        # Compile the ORIGINAL before touching anything. Several sessions edit
        # this repository at once, and reading a file mid-write would otherwise
        # mutate the module and then fail to put it back.
        restore = compile(original, path, "exec")
        broken_source = original.replace(mutation["old"], mutation["new"])
        try:
            exec(compile(broken_source, path, "exec"), module.__dict__)
            result = unittest.TextTestRunner(
                stream=io.StringIO(), verbosity=0).run(
                    load(mutation["tests"]))
        finally:
            exec(restore, module.__dict__)

        # A named test that does not exist is turned by unittest's loader into
        # a _FailedTest that ERRORS, so it counted 1/1 "failed" and scored as
        # caught. Renaming a test therefore kept the mutation green — the exact
        # thing this harness exists to prevent, one level up. An empty test list
        # scored 0 == 0 and passed too. So: the named tests must exist, there
        # must be some, and the failures have to be assertion failures rather
        # than the loader complaining it cannot find them.
        missing = [str(e[0]) for e in result.errors
                   if "_FailedTest" in str(e[0]) or "ModuleNotFound" in str(e[1])]
        noticed = len(result.failures) + len(result.errors) - len(missing)
        if missing:
            status = "BROKEN TEST NAME"
        elif result.testsRun == 0:
            status = "NO TESTS NAMED"
        elif noticed == result.testsRun:
            status = "caught"
        else:
            status = "NOT CAUGHT"
        print(f"\n  {status:>10}  {mutation['what']}")
        print(f"              {mutation['why']}")
        print(f"              {noticed}/{result.testsRun} of the named tests "
              f"failed")
        if status == "caught":
            caught += 1
        else:
            missed.append((status, mutation["what"]))

    print("\n" + "=" * 74)
    print(f"  {caught}/{len(MUTATIONS)} mutations caught")
    # The summary used to print every miss as UNCAUGHT, including the ones the
    # per-mutation line had correctly called STALE ANCHOR or BROKEN TEST NAME.
    # Those are opposite diagnoses — "nothing pins this behaviour" versus "the
    # harness cannot find the thing that does" — and they call for opposite
    # actions. Reading only the summary sent this session down the wrong path
    # twice in one night, which is a good argument for a summary that does not
    # discard the distinction it was handed.
    for status, what in missed:
        print(f"  {status:>16}  {what}")
    if any(s == "NOT CAUGHT" for s, _ in missed):
        print("\n  A test that passes against broken code is worse than no "
              "test: it turns\n  an absence of checking into a claim of "
              "checking. Fix the test, not this file.")
    if any(s != "NOT CAUGHT" for s, _ in missed):
        print("\n  A mutation the harness could not apply or could not find "
              "tests for is\n  not a passing mutation. Repair the entry — the "
              "behaviour it names is\n  currently unchecked either way.")
    return not missed


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", metavar="MODULE",
                        help="module short names (closure, matrix, ...) or "
                             "full dotted test names; default is everything")
    parser.add_argument("-v", "--verbose", action="count", default=1,
                        help="one line per test")
    parser.add_argument("--mutate", action="store_true",
                        help="break each covered function in memory and "
                             "require the tests to fail")
    args = parser.parse_args()

    sys.path.insert(0, ROOT)
    if args.mutate:
        # The suite has to be green before its failures mean anything.
        if not run(args.names, 0):
            print("\n  suite is already failing — fix that before asking "
                  "whether it can fail")
            return 1
        return 0 if mutate() else 1
    return 0 if run(args.names, args.verbose) else 1


if __name__ == "__main__":
    sys.exit(main())
