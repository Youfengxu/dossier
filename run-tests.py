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

WHY --mutate. DESIGN §0.1 — a failed check is not a passed check — applies to
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
#
# NAME ONLY IN-PROCESS TESTS HERE. mutate() applies a mutation by re-executing
# source into the imported module object and never writes to disk (see its
# docstring for why). A test that shells out therefore runs a fresh interpreter
# reading the UNMUTATED file, cannot fail, and makes the entry read NOT CAUGHT
# however good the test is. That is indistinguishable from a weak test, and it
# is what happened to the locate.quoted entry until bundle.markdown was lifted
# out of main() so the check could run in-process. If an entry is stubbornly
# NOT CAUGHT, check that its tests are not subprocess tests before rewriting
# them.
MUTATIONS = [
    {
        "what": "bundle — body cells fall back to the unstyled default",
        "why": "the default format does not wrap, so a multi-line evidence "
               "quote is stored intact and shown clipped at the column edge — "
               "a reviewer sees fifty characters of a five-hundred-character "
               "citation with nothing to say more exists",
        "module": "bundle",
        "old": 'BODY_STYLE, HEADER_STYLE = 1, 2',
        "new": 'BODY_STYLE, HEADER_STYLE = 0, 0',
        "tests": [
            "tests.test_xlsx_styles.StylesPart."
            "test_body_cells_reference_a_style_that_wraps",
            "tests.test_xlsx_styles.StylesPart."
            "test_header_cells_reference_a_style_that_wraps_and_is_bold",
        ],
    },
    {
        "what": "locate.quoted — a table quote reflowed like prose",
        "why": "every character reaches the report and none of it can be "
               "checked: a table's meaning is the alignment of cell to column, "
               "and the collapse discards exactly that. Evidence that is "
               "decorative, and harder to spot than evidence that is missing",
        "module": "locate",
        "old": '    rows = [ln for ln in lines if ln.count("|") >= 2]',
        "new": '    rows = []',
        "tests": [
            "tests.test_quoting.Quoted.test_a_table_keeps_one_row_per_line",
            "tests.test_quoting.RenderersAreWired."
            "test_markdown_emits_a_table_quote_as_a_table",
        ],
    },
    {
        "what": "matrix.Record — letters resolved before header names",
        "why": "a matrix headed with criteria labels \"A\"/\"B\"/\"C\" is ordinary, "
               "and there row[\"C\"] would read column C while resolve_column "
               "picked the column HEADED C — one string, two columns",
        "module": "matrix",
        "old": '        found = self._names.get(normalise(key))',
        "new": '        if dict.__contains__(self, key):\n            return key\n        found = self._names.get(normalise(key))',
        "tests": [
            "tests.test_matrix.NameAddressing."
            "test_a_name_beats_a_letter_and_matches_resolve_column",
        ],
    },
    {
        "what": "matrix.resolve_column — returns a bare str, losing the Letter tag",
        "why": "the letter it returns goes straight back into row[col] at "
               "nineteen sites; untagged it is re-resolved as a header NAME and "
               "reads a different column than the one just selected",
        "module": "matrix",
        "old": '        return Letter(hits[0])',
        "new": '        return hits[0]',
        "tests": [
            "tests.test_matrix.NameAddressing."
            "test_the_resolve_column_round_trip_survives_a_letter_shaped_header",
        ],
    },
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
        "what": "matrix.numbered — the sheet row taken from list position",
        "why": "the reported bug, closed once as already fixed because the WRITER "
               "had been corrected and the four callers building its row-keyed "
               "dict had not; one deleted row in Excel files every verdict below "
               "it against the wrong comment, in a workbook that opens cleanly",
        "module": "matrix",
        "old": "        yield row.get(ROW_KEY, position), row",
        "new": "        yield position, row",
        "tests": [
            "tests.test_matrix.NumberedRows.test_numbers_come_from_the_sheet_not_the_list",
            "tests.test_matrix.NumberedRows.test_a_verdict_lands_against_its_own_comment_across_a_gap",
        ],
    },
    {
        "what": "vocabulary.flagged — the unknown value dropped",
        "why": "eight tools ask what a reviewer must look at; dropping the "
               "unknown value silently stops flagging 'we could not verify this', "
               "which is a request for evidence and the thing most worth reading",
        "module": "vocabulary",
        "old": "        return tuple(self.scale[:-1]) + (self.unknown,)",
        "new": "        return tuple(self.scale[:-1])",
        "tests": [
            "tests.test_vocabulary.DerivedViews.test_the_literals_eight_files_used_are_reproduced_exactly",
            "tests.test_vocabulary.DerivedViews.test_flagged_and_shortfall_are_not_the_same_question",
        ],
    },
    {
        "what": "extract.docx — runs joined with a space",
        "why": "Word splits words across runs, so this inserts a space into the "
               "middle of every term an anchor quotes",
        "module": "extract",
        "old": "            txt = ''.join(t.text or '' for t in el.iter()",
        "new": "            txt = ' '.join(t.text or '' for t in el.iter()",
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
        "what": "fixtures/sec — the reissue pattern loses its misspelling",
        "why": "\"We resissue prior comment 30 in full\" is then read as the "
               "staff accepting the response: the wrong label, in the direction "
               "that flatters whatever is scored against it",
        "module": "fixtures/sec/fetch",
        "old": 'REISSUE = re.compile(r"\\bre-?s?issu\\w*|\\breiterat\\w*", re.I)',
        "new": 'REISSUE = re.compile(r"\\bre-?issu\\w*|\\breiterat\\w*", re.I)',
        "tests": [
            "tests.test_sec_fixture.TheWordsForReissuing."
            "test_the_misspelling_one_letter_uses",
        ],
    },
    {
        "what": "fixtures/sec — a reissue reaches only the sentence it is in",
        "why": "\"We note your response to prior comment 24. We reissue the "
               "first bullet of the prior comment in part.\" is then addressed, "
               "because the number and the reissue are a full stop apart",
        "module": "fixtures/sec/fetch",
        "old": "        for number in here or sorted(at):",
        "new": "        for number in here:",
        "tests": [
            "tests.test_sec_fixture.ReissueOutsideTheCitingSentence."
            "test_a_reissue_in_the_next_sentence_reaches_the_comment_cited_before_it",
            "tests.test_sec_fixture.ReissueOutsideTheCitingSentence."
            "test_a_reissue_further_on_is_quoted_with_the_gap_marked",
            "tests.test_sec_fixture.ReissueOutsideTheCitingSentence."
            "test_one_that_names_none_reaches_every_comment_cited_around_it",
        ],
    },
    {
        "what": "fixtures/sec — any use of the word reissues the comments around it",
        "why": "a question about a reissued audit report then reissues every "
               "prior comment its numbered comment cites; reading across "
               "sentences is only safe while this guard holds",
        "module": "fixtures/sec/fetch",
        "old": '            ours = OURS.search(parts[i]) or re.search(r"\\bcomments?\\b", parts[i], re.I)',
        "new": '            ours = True',
        "tests": [
            "tests.test_sec_fixture.ReissueOutsideTheCitingSentence."
            "test_the_word_used_of_something_else_reissues_nothing",
        ],
    },
    {
        "what": "fixtures/sec — a comment of an older letter counted as this one's",
        "why": "\"prior comment 15 of our letter dated March 14, 2023\" becomes a "
               "row about comment 15 of a letter that never said it",
        "module": "fixtures/sec/fetch",
        "old": "        if other and iso(other) != first:",
        "new": "        if False:",
        "tests": [
            "tests.test_sec_fixture.ACommentOfAnotherLetter."
            "test_a_comment_of_an_older_letter_is_not_a_comment_of_this_one",
            "tests.test_sec_fixture.ACommentOfAnotherLetter."
            "test_reissuing_another_letters_comment_reissues_nothing_here",
            "tests.test_sec_fixture.TheWholeRun."
            "test_what_is_labelled_and_what_is_set_aside",
        ],
    },
    {
        "what": "fixtures/sec — only a comment called \"prior\" is cited",
        "why": "\"We note your response to comment 4 and reissue it in part\" "
               "is then a reissue of nothing: no row, and one negative fewer",
        "module": "fixtures/sec/fetch",
        "old": '    r"\\b(?:prior comments?|responses? to comments?)\\s+(?:(?:nos?\\.?|numbers?)\\s+)?"',
        "new": '    r"\\b(?:prior comments?)\\s+(?:(?:nos?\\.?|numbers?)\\s+)?"',
        "tests": [
            "tests.test_sec_fixture.WhichNumbersAreCited."
            "test_a_comment_cited_without_the_word_prior",
        ],
    },
    {
        "what": "fixtures/sec — a comment number spelled out is not a number",
        "why": "\"prior comment five and reissue it in part\" cites nothing, so "
               "a letter that spells its numbers keeps its follow-ups and loses "
               "its reissues",
        "module": "fixtures/sec/fetch",
        "old": 'NUM = r"(?:\\d{1,3}(?![\\d%%]|,\\d{3}|\\.\\d)|(?:%s)\\b)" % "|".join(WORDS)',
        "new": 'NUM = r"(?:\\d{1,3}(?![\\d%%]|,\\d{3}|\\.\\d)|(?:%s)\\b)" % "zero"',
        "tests": [
            "tests.test_sec_fixture.WhichNumbersAreCited."
            "test_a_number_spelled_out",
        ],
    },
    {
        "what": "fixtures/sec — running text that starts with a number opens a comment",
        "why": "a line wrapped before \"18. Please revise\" takes comment 18's "
               "place and the real one is folded into it; one letter of the "
               "default slice's eight does this",
        "module": "fixtures/sec/fetch",
        "old": 'NUMBER = re.compile(r"^\\s{0,12}(\\d{1,3})\\.(?:\\s{2,}|\\s*$)")',
        "new": 'NUMBER = re.compile(r"^\\s{0,12}(\\d{1,3})\\.(?!\\d)")',
        "tests": [
            "tests.test_sec_fixture.WhereACommentStarts."
            "test_a_wrapped_line_that_starts_with_the_next_number_opens_nothing",
            "tests.test_sec_fixture.WhereACommentStarts."
            "test_a_numbered_heading_from_the_filing_opens_nothing",
        ],
    },
    {
        "what": "fixtures/sec — a letter whose numbering breaks is labelled anyway",
        "why": "every comment after the lost number is read as one, and a "
               "reissue in any of them lands on prior comments it never named",
        "module": "fixtures/sec/fetch",
        "old": "    if not starts or any(found and int(found.group(1)) == want + 1",
        "new": "    if not starts or any(False and int(found.group(1)) == want + 1",
        "tests": [
            "tests.test_sec_fixture.WhereACommentStarts."
            "test_a_letter_whose_numbering_breaks_is_set_aside_not_labelled",
            "tests.test_sec_fixture.TheWholeRun."
            "test_what_is_labelled_and_what_is_set_aside",
        ],
    },
    {
        "what": "fixtures/sec — the first-round letter taken without reading its date",
        "why": "the staff letter nearest the date named is then the first-round "
               "letter whatever is printed on it, which is how a closing notice "
               "came to stand for a comment letter",
        "module": "fixtures/sec/fetch",
        "old": "        if dated(text) != first or not comments:",
        "new": "        if False:",
        "tests": [
            "tests.test_sec_fixture.WhichLetterIsBeingAnswered."
            "test_a_letter_filed_the_day_after_the_date_printed_on_it",
            "tests.test_sec_fixture.WhichLetterIsBeingAnswered."
            "test_a_notice_dated_the_same_day_is_not_the_comment_letter",
        ],
    },
    {
        "what": "fixtures/sec — a page header left inside a sentence",
        "why": "its \"Corp.\" ends the sentence, \"which we reissue\" and \"in "
               "part\" land in different ones, and a partial reissue is read "
               "as a full one",
        "module": "fixtures/sec/fetch",
        "old": "    return \"\\n\".join(line for i, line in enumerate(lines) if i not in drop",
        "new": "    return \"\\n\".join(line for i, line in enumerate(lines) if True",
        "tests": [
            "tests.test_sec_fixture.PageHeaders."
            "test_a_header_inside_the_reissuing_sentence_does_not_end_it",
            "tests.test_sec_fixture.PageHeaders."
            "test_nor_does_the_plain_header_of_a_later_page",
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
    # check-aggregation.py. The module name has a hyphen, which `import` cannot
    # spell and __import__ can; tests/test_aggregation.py loads it the same way,
    # so the tests and these mutations hold one module object between them.
    {
        "what": "check-aggregation — brackets inside a string literal counted again",
        "why": "the gate as it shipped: one regex literal with an unmatched "
               "\"(\" holds its statement open to the end of the file. It read "
               "50 of llm.py's 595 lines and printed clean over the rest, and "
               "24 of the 52 top-level files ended the same way",
        "module": "check-aggregation",
        "old": '            if token.type == tokenize.NEWLINE:',
        "new": ('            if token.type == tokenize.NEWLINE and '
                'sum(t.string.count(c) for t in pending for c in "([{") <= '
                'sum(t.string.count(c) for t in pending for c in ")]}"):'),
        "tests": [
            "tests.test_aggregation.TheReportedFailure."
            "test_a_site_below_a_regex_literal_is_reported",
            "tests.test_aggregation.TheReportedFailure."
            "test_and_nothing_else_is_said_about_that_file",
            "tests.test_aggregation.WhereAStatementEnds."
            "test_a_bracket_inside_a_string_does_not_hold_a_statement_open",
            "tests.test_aggregation.TheTreeItself."
            "test_every_file_is_read_to_its_end",
            "tests.test_aggregation.TheTreeItself."
            "test_the_statements_are_the_ones_the_parser_sees",
        ],
    },
    {
        "what": "check-aggregation — a line break inside a statement ends it",
        "why": "the first version of the gate, which read line by line: a "
               "comprehension whose filter sits on its second line is two "
               "halves, and neither half is a filtered tally",
        "module": "check-aggregation",
        "old": '            if token.type == tokenize.NEWLINE:',
        "new": '            if token.type in (tokenize.NEWLINE, tokenize.NL):',
        "tests": [
            "tests.test_aggregation.WhereAStatementEnds."
            "test_a_filter_on_the_second_line_belongs_to_its_comprehension",
            "tests.test_aggregation.WhereAStatementEnds."
            "test_a_closing_bracket_inside_a_string_does_not_end_one_early",
            "tests.test_aggregation.TheTreeItself."
            "test_the_statements_are_the_ones_the_parser_sees",
        ],
    },
    {
        "what": "check-aggregation — the statement still open at the end of a "
                "file is dropped",
        "why": "the other half of the same defect. The loop kept a statement "
               "when it closed, and one that never closed was thrown away "
               "with everything it had swallowed, unscanned and unmentioned",
        "module": "check-aggregation",
        "old": '    if pending:\n        # Still open when the tokens ran out.',
        "new": '    if False:\n        # Still open when the tokens ran out.',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_statement_still_open_at_the_end_is_scanned",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_and_the_file_is_reported_from_the_line_that_opened_it",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_an_open_statement_fails_the_gate_with_no_site_in_it",
        ],
    },
    {
        "what": "check-aggregation — a scan the tokenizer abandons says nothing",
        "why": "every statement above the break is closed, so nothing is left "
               "pending to give it away, and the parser does not always "
               "object either: under 3.12 and 3.14 the tokenizer gives up on "
               "one VALID f-string. The gate reads to that line, stops, and "
               "reports on what it read as though that were the file",
        "module": "check-aggregation",
        "old": '        stopped = (line or reached, f"not read past this line: {reason}")',
        "new": '        stopped = None',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_scan_that_stops_between_statements_says_where",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_whatever_the_tokenizer_raises_the_file_is_reported",
        ],
    },
    {
        "what": "check-aggregation — a file that cannot be read is skipped",
        "why": "this was `continue`. A folder holding one undecodable file "
               "beside a readable one was reported clean on the strength of "
               "the readable one",
        "module": "check-aggregation",
        "old": '                unread.append((path, 0, f"could not be read ({said(error)})"))',
        "new": '                pass',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_file_that_cannot_be_decoded_is_reported_not_skipped",
        ],
    },
    {
        "what": "check-aggregation — a file not read to its end still passes",
        "why": "CI consumes the exit status and nothing else. A report that "
               "names the unread file and exits 0 is a paragraph nobody is "
               "shown",
        "module": "check-aggregation",
        "old": '    if not hits and not unread:',
        "new": '    if not hits:',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_an_open_statement_fails_the_gate_with_no_site_in_it",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_scan_that_stops_between_statements_says_where",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_file_that_cannot_be_decoded_is_reported_not_skipped",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_path_that_does_not_exist_is_not_clean",
        ],
    },
    {
        "what": "check-aggregation — string literals blanked out of the statement",
        "why": "the tidy way to stop a docstring matching, and it disarms the "
               "gate: two of the filter pattern's four endings name a literal, "
               "so with the strings gone neither can match anything",
        "module": "check-aggregation",
        "old": '            for n, piece in enumerate(token.string.split("\\n")):',
        "new": ('            for n, piece in enumerate((\'""\' if literal(token) '
                'else token.string).split("\\n")):'),
        "tests": [
            "tests.test_aggregation.StringsAreNotCode."
            "test_a_literal_the_pattern_names_still_counts",
        ],
    },
    {
        "what": "check-aggregation — an acknowledgement reaches the statement "
                "above it",
        "why": "the window as it was: one line past the statement's first. An "
               "`# aggregation-ok` written for one site then silences the "
               "site on the line above it, which nobody looked at",
        "module": "check-aggregation",
        "old": '    rows = range(statement.line, statement.end + 1)',
        "new": '    rows = range(statement.line, statement.end + 2)',
        "tests": [
            "tests.test_aggregation.TheAcknowledgement."
            "test_one_written_for_the_next_statement_does_not_reach_back",
        ],
    },
    {
        "what": "check-aggregation — a file this Python cannot parse is trusted",
        "why": "a tokenizer older than the file does not refuse it. It cuts an "
               "f-string that reuses its own quote into two plain strings, "
               "and the site between the braces is read as text: clean under "
               "3.9 to 3.11, where the bracket counter had flagged it",
        "module": "check-aggregation",
        "old": '    return hits, len(found), stopped or unparsed(source)',
        "new": '    return hits, len(found), stopped',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_file_this_python_cannot_parse_is_not_called_clean",
        ],
    },
    {
        "what": "check-aggregation — a line break inside a call is a space",
        "why": "the lines of a statement were joined by a space wherever the "
               "break fell, and the patterns allow none in `.most_common(` or "
               "`.get(\"verdict\")`: a site wrapped after its dot, or inside "
               "its call, read as clean",
        "module": "check-aggregation",
        "old": '    a = before.string if before.type == tokenize.OP else ""',
        "new": '    return False',
        "tests": [
            "tests.test_aggregation.WhereAStatementEnds."
            "test_a_line_break_inside_a_call_does_not_hide_it",
            "tests.test_aggregation.WhereAStatementEnds."
            "test_nor_does_one_inside_the_literal_a_pattern_names",
        ],
    },
    {
        "what": "check-aggregation — an argument with nothing to read passes",
        "why": "a folder with no Python under it, named beside one that has "
               "some, is covered by its neighbour: the mistyped argument, "
               "reported as checked",
        "module": "check-aggregation",
        "old": '            unread.append((argument, 0, "no Python source here"))',
        "new": '            pass',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_folder_holding_no_python_is_not_clean",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_nor_is_one_given_beside_a_path_that_does_hold_some",
        ],
    },
    {
        "what": "check-aggregation — fixtures/ goes unread again",
        "why": "sixteen scripts that tally verdicts and print the baselines "
               "the documents quote were skipped from the gate's first "
               "commit, for no recorded reason, and one of them held the "
               "first shape the gate names",
        "module": "check-aggregation",
        "old": 'SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}',
        "new": ('SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", '
                '"fixtures"}'),
        "tests": [
            "tests.test_aggregation.TheTreeItself."
            "test_the_default_walk_reaches_everything_it_is_meant_to",
        ],
    },
    {
        "what": "fixtures/ntsb — a tied majority named by whichever came first",
        "why": "the site the aggregation gate found on the day it first read "
               "fixtures/. The baseline is the same count either way; the "
               "class printed beside it as the answer to give was decided by "
               "the order of labels.json",
        "module": "fixtures/ntsb/score-ntsb",
        "old": "    return sorted(k for k, n in counts.items() if n == most), most",
        "new": "    return [max(counts, key=counts.get)], most",
        "tests": [
            "tests.test_ntsb_fixture.TheMajorityClass."
            "test_a_tie_names_every_class_in_it",
            "tests.test_ntsb_fixture.TheMajorityClass."
            "test_and_does_not_depend_on_which_was_counted_first",
            "tests.test_ntsb_fixture.TheLineItPrints."
            "test_a_tie_is_said_to_be_one",
        ],
    },
    # What a tool may say about a document it did not read all of. synthesize.py
    # asserts absences over an inventory, and three places upstream of it
    # reported a failure as a result.
    {
        "what": "synthesize.read_inventory — the sections that failed are forgotten",
        "why": "the defect as it shipped: `[s for s in data[\"sections\"] if s "
               "and \"error\" not in s]`. On a sound three-section document "
               "one failed section produced a false D3, D6 or D8, an exit "
               "status of 0, and a section count one lower as the only trace",
        "module": "synthesize",
        "old": '    return sections, unread, partial, short',
        "new": '    return sections, [], partial, short',
        "tests": [
            "tests.test_unread_sections.WhatTheInventoryLineSays."
            "test_a_section_that_failed_is_counted_and_named",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_a_pointer_to_an_unread_section_is_not_called_a_missing_section",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_ownership_gap_the_unread_section_may_close",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_orphan_the_unread_section_may_consume",
        ],
    },
    {
        "what": "synthesize.read_inventory — a section read by the fallback "
                "alone counts as read",
        "why": "the fallback schema never asks what a section consumes. Taken "
               "as a full reading, the consumer's silence makes an orphan of "
               "whatever it consumes",
        "module": "synthesize",
        "old": '        if section.get("degraded") and not fully_read(section):',
        "new": '        if False:',
        "tests": [
            "tests.test_unread_sections.WhatTheInventoryLineSays."
            "test_a_section_read_by_the_fallback_alone_is_named_as_read_in_part",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_orphan_when_the_consumer_was_read_without_its_consumes",
        ],
    },
    {
        "what": "synthesize.fully_read — an old inventory's fallback entries "
                "are taken as read",
        "why": "an entry written before `full_passes` existed does not say "
               "whether a full pass followed the fallback. With five empty "
               "fields and no sign of one, assuming it did turns \"nothing "
               "asked\" back into \"consumes nothing\"",
        "module": "synthesize",
        "old": '    return any(entry.get(key) for key in UNASKED)',
        "new": '    return True',
        "tests": [
            "tests.test_unread_sections.WhatTheInventoryLineSays."
            "test_an_old_inventory_is_read_in_part_only_where_it_shows_no_full_pass",
        ],
    },
    {
        "what": "synthesize — a pointer to an unread section is a missing "
                "section again",
        "why": "\"Section 3 — no such section in the document\", printed about "
               "a section that is in the document and failed at extraction: "
               "the tool's own failure, reported as the author's defect",
        "module": "synthesize",
        "old": '        if not hits and unread:',
        "new": '        if False:',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_a_pointer_to_an_unread_section_is_not_called_a_missing_section",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_nor_when_the_inventory_cannot_say_which_section_went_unread",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_no_pointer_is_cleared_by_what_an_unread_heading_says",
        ],
    },
    {
        "what": "synthesize.open_question — no absence is ever in doubt",
        "why": "nothing owns it, nothing consumes it: each is a claim about "
               "every section of the document, made over the ones that "
               "happened to be read",
        "module": "synthesize",
        "old": '    if not sections:\n        return ""',
        "new": '    if True:\n        return ""',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_ownership_gap_the_unread_section_may_close",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_orphan_the_unread_section_may_consume",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_orphan_when_the_consumer_was_read_without_its_consumes",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_the_summary_counts_them_apart",
        ],
    },
    {
        "what": "synthesize.open_question — an absence is in doubt with "
                "every section read",
        "why": "marking that cries wolf is marking nobody reads. When nothing "
               "went unread the absence is the inventory's to assert, and "
               "the finding has to stand as one",
        "module": "synthesize",
        "old": '    if not sections:\n        return ""',
        "new": '    if False:\n        return ""',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_and_so_is_a_claim_the_section_it_names_does_not_hold",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_the_fallback_does_not_put_an_ownership_finding_in_doubt",
        ],
    },
    {
        "what": "synthesize — the dataflow flag forgets the sections read "
                "in part",
        "why": "--authority-as-dataflow takes ownership from produces and "
               "consumes. The fallback never asks for either, so under the "
               "flag a section read in part can be hiding the owner, and "
               "the ownership gap was asserted over it",
        "module": "synthesize",
        "old": '        owners_unseen = unread + (partial if args.authority_as_dataflow else [])',
        "new": '        owners_unseen = unread',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_under_the_dataflow_flag_a_section_read_in_part_may_be_the_owner",
        ],
    },
    {
        "what": "synthesize — the candidate file calls a finding in doubt unmet",
        "why": "the CSV is what score-register.py reads and what a reviewer is "
               "handed. `unverifiable` is the coverage scale's own word for "
               "evidence that could not be reached; `unmet` there would be a "
               "verdict nobody reached",
        "module": "synthesize",
        "old": '                    label, UNVERIFIABLE if doubt else UNMET, quote, where,',
        "new": '                    label, UNMET, quote, where,',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_the_candidate_file_carries_the_verdict",
        ],
    },
    {
        "what": "synthesize.adjudicate_pairs — a call that failed says no conflict",
        "why": "DESIGN §0.1's first example, a second time: the lens that "
               "errored defaulted to not refuted. Here the pair came back as "
               "None, which is what a pair judged clear comes back as, and "
               "was counted in \"adjudicated N pairs\"",
        "module": "synthesize",
        "old": '            return UNJUDGED',
        "new": '            return None',
        "tests": [
            "tests.test_unread_sections.APairThatCouldNotBeJudged."
            "test_a_failed_call_is_counted_as_failed",
            "tests.test_unread_sections.APairThatCouldNotBeJudged."
            "test_and_a_run_where_every_call_failed_has_judged_nothing",
            "tests.test_unread_sections.TheAdjudicationLine."
            "test_it_says_how_many_pairs_were_judged_not_how_many_were_tried",
        ],
    },
    {
        "what": "synthesize — a score that could not be computed exits clean",
        "why": "--ground-truth without PyYAML returned 0 and printed no score. "
               "Asked for a measurement, it reported nothing and called that "
               "success",
        "module": "synthesize",
        "old": '                  file=sys.stderr)\n            return 1',
        "new": '                  file=sys.stderr)\n            return 0',
        "tests": [
            "tests.test_unread_sections.WithoutPyYAML."
            "test_a_score_that_cannot_be_computed_is_not_a_clean_exit",
        ],
    },
    {
        "what": "inventory.catalogue — a section that failed loses its name",
        "why": "{\"error\": ...} and nothing else is what was stored. The "
               "inventory could say that a section had gone unread and not "
               "which, and a pointer to it could not be told from a pointer "
               "to a section the document does not have",
        "module": "inventory",
        "old": ('            return {"error": str(exc), "heading": section["heading"],\n'
                '                    "locator": locator}'),
        "new": '            return {"error": str(exc)}',
        "tests": [
            "tests.test_inventory.ASectionThatFailed.test_it_is_still_named",
            "tests.test_inventory.ASectionThatFailed."
            "test_and_holds_nothing_that_reads_as_content",
        ],
    },
    {
        "what": "inventory.catalogue — a pass that failed counts as one that "
                "answered",
        "why": "--runs defaults to 3 because one pass misses things: at one "
               "pass the fixture's deferral cycle is invisible. A failed pass "
               "was swallowed, so a section read once looked the same as one "
               "read three times",
        "module": "inventory",
        "old": '            continue                # not merged, and not counted: see `passes`',
        "new": '            more = {}',
        "tests": [
            "tests.test_inventory.ThePassesBehindAnEntry."
            "test_a_pass_that_failed_is_not",
        ],
    },
    {
        "what": "inventory.catalogue — the fallback schema recorded as a full pass",
        "why": "the fallback asks for three fields and stores the other five "
               "as empty lists. Recorded as a full pass, `consumes: []` reads "
               "as \"consumes nothing\" when nothing asked",
        "module": "inventory",
        "old": '        degraded, passes, full = True, 1, 0',
        "new": '        degraded, passes, full = True, 1, 1',
        "tests": [
            "tests.test_inventory.ThePassesBehindAnEntry."
            "test_the_fallback_is_one_pass_and_says_which_schema_it_used",
        ],
    },
    {
        "what": "inventory.limited — --limit leaves no record of what it "
                "left out",
        "why": "the inventory held the first N sections under a line that "
               "read \"reading 100% of the document\", and synthesize.py "
               "called a pointer to section N+1 a pointer to a section that "
               "does not exist",
        "module": "inventory",
        "old": '    return sections[:limit], beyond',
        "new": '    return sections[:limit], []',
        "tests": [
            "tests.test_inventory.ARunToldToStopEarly."
            "test_the_sections_it_stopped_before_are_recorded_as_not_read",
        ],
    },
    # Lines of the document that are in no section. The splitter left two kinds
    # out under a run that printed "reading 100% of the document". Each entry
    # below is one decision of 2026-10-04: the two kinds are kept, the run
    # checks what its splitter left out and records it, and synthesize.py
    # asserts no absence over lines no call was shown.
    {
        "what": "inventory.split_sections — a heading straight after a heading "
                "replaces it again",
        "why": "the replaced line is then in no section. A numbered list is a "
               "run of heading-like lines, so each step replaced the one "
               "before: lines 284 and 285 of the fixture, the second a "
               "corroborating anchor of the planted defect GT-D5-001",
        "module": "inventory",
        "old": '        elif is_heading and not current and not opened:',
        "new": '        elif is_heading and not current:',
        "tests": [
            "tests.test_unread_lines.AHeadingStraightAfterAHeading."
            "test_each_step_of_a_numbered_list_is_shown_to_a_call",
            "tests.test_unread_lines.AHeadingStraightAfterAHeading."
            "test_the_first_of_the_run_heads_the_section_and_the_rest_are_text",
            "tests.test_unread_lines.AHeadingStraightAfterAHeading."
            "test_a_heading_followed_at_once_by_its_sub_heading_keeps_both",
            "tests.test_unread_lines.AHeadingStraightAfterAHeading."
            "test_the_two_steps_the_fixture_lost_are_read",
            "tests.test_unread_lines.AHeadingStraightAfterAHeading."
            "test_no_line_of_the_fixture_that_holds_text_is_in_no_section",
            "tests.test_unread_lines.WhatALocatorSays."
            "test_the_committed_inventory_differs_from_a_fresh_cut_in_one_section",
            "tests.test_unread_lines.OnDocumentsNobodyChose."
            "test_every_line_with_text_is_in_one_section_and_no_locator_lies",
        ],
    },
    {
        "what": "inventory.split_sections — the sixty-character floor put back",
        "why": "a stub, a short front matter and a short last section were "
               "dropped heading and all, to save a call that \"cannot produce "
               "anything\". Forty-four characters is \"Component maturity is "
               "assessed in Section 9.\", which is what D3 checks",
        "module": "inventory",
        "old": '        if opened or any(x.strip() for x in current):',
        "new": '        if current and sum(len(x.strip()) for x in current) > 60:',
        "tests": [
            "tests.test_unread_lines.ASectionOfAFewWords."
            "test_a_stub_is_a_section_of_its_own",
            "tests.test_unread_lines.ASectionOfAFewWords."
            "test_a_short_front_matter_is_kept",
            "tests.test_unread_lines.ASectionOfAFewWords."
            "test_a_short_last_section_is_kept",
            "tests.test_unread_lines.ASectionOfAFewWords."
            "test_a_heading_with_nothing_under_it_is_kept",
            "tests.test_chunking.InventorySections."
            "test_a_section_with_almost_no_text_is_still_a_section",
            # The run's own check, on a document with a stub in it: with the
            # floor back it no longer says it reads the whole document.
            "tests.test_unread_lines.ARun."
            "test_it_reads_the_whole_document_and_the_inventory_says_so",
            "tests.test_unread_lines.OnDocumentsNobodyChose."
            "test_every_line_with_text_is_in_one_section_and_no_locator_lies",
        ],
    },
    {
        "what": "inventory.split_sections — a heading with nothing under it "
                "is dropped",
        "why": "a heading is text, and a section that holds nothing is not "
               "the same as no section: a pointer to it would be called a "
               "pointer to nowhere",
        "module": "inventory",
        "old": '        if opened or any(x.strip() for x in current):',
        "new": '        if any(x.strip() for x in current):',
        "tests": [
            "tests.test_unread_lines.ASectionOfAFewWords."
            "test_a_heading_with_nothing_under_it_is_kept",
            "tests.test_unread_lines.OnDocumentsNobodyChose."
            "test_every_line_with_text_is_in_one_section_and_no_locator_lies",
        ],
    },
    {
        "what": "inventory.split_sections — the piece after a size cut starts "
                "one line early",
        "why": "its locator then names a line its text does not hold, and "
               "that line is in two sections. Everything downstream takes a "
               "locator for what a call was shown: unread_lines() is the one "
               "place that checks, and the documents in the fixture are never "
               "cut for size, so only generated ones reach this",
        "module": "inventory",
        "old": '                current, start, opened = [], number + 1, False',
        "new": '                current, start, opened = [], number, False',
        "tests": [
            "tests.test_unread_lines.OnDocumentsNobodyChose."
            "test_every_line_with_text_is_in_one_section_and_no_locator_lies",
        ],
    },
    {
        "what": "inventory.unread_lines — a section is taken at its locator's "
                "word",
        "why": "the claim is that a call was shown the line, and a locator is "
               "the splitter's own account of that. A check that reads only "
               "the line numbers confirms what the splitter believes",
        "module": "inventory",
        "old": '        if whole or headed:',
        "new": '        if True:',
        "tests": [
            "tests.test_unread_lines.WhatTheRunChecks."
            "test_a_section_whose_text_is_not_its_lines_shows_none_of_them",
            "tests.test_unread_lines.WhatTheRunChecks."
            "test_nor_does_one_whose_heading_is_not_the_line_it_starts_on",
        ],
    },
    {
        "what": "inventory.unread_lines — it never finds a line",
        "why": "a check that cannot fail: the run says 100% whatever the "
               "splitter did, which is where this started",
        "module": "inventory",
        "old": '            if line.strip() and number not in shown]',
        "new": '            if False]',
        "tests": [
            "tests.test_unread_lines.WhatTheRunChecks."
            "test_a_line_in_no_section_is_reported",
            "tests.test_unread_lines.WhatTheRunChecks."
            "test_it_finds_the_two_lines_the_committed_inventory_left_out",
            "tests.test_unread_lines.ARun."
            "test_a_splitter_that_leaves_lines_out_is_not_taken_at_its_word",
            "tests.test_unread_lines.ARun."
            "test_and_the_inventory_records_which_lines",
        ],
    },
    {
        "what": "inventory.unread_lines — a blank line in no section counts "
                "as text not read",
        "why": "blank lines under no heading are what the splitter still "
               "leaves out, and nothing is lost with them. A run that says "
               "NOT the whole document over them says it of most documents, "
               "and stops being read",
        "module": "inventory",
        "old": '            if line.strip() and number not in shown]',
        "new": '            if number not in shown]',
        "tests": [
            "tests.test_unread_lines.WhatTheRunChecks."
            "test_a_blank_line_in_no_section_is_not",
        ],
    },
    {
        "what": "inventory.reading — 100% is said over lines in no section",
        "why": "the line was a constant. It is the sentence synthesize.py's "
               "licence to assert an absence rests on",
        "module": "inventory",
        "old": '    if not beyond and not left_out:',
        "new": '    if not beyond:',
        "tests": [
            "tests.test_unread_lines.WhatTheRunSays.test_and_not_when_one_is",
            "tests.test_unread_lines.ARun."
            "test_a_splitter_that_leaves_lines_out_is_not_taken_at_its_word",
        ],
    },
    {
        "what": "inventory.py — the run does not check what its splitter "
                "left out",
        "why": "the splitter is tested on the documents the tests hold. The "
               "run checks it on the document in hand, and that is the one "
               "the inventory is about",
        "module": "inventory",
        "old": '    left_out = unread_lines(lines, everything)',
        "new": '    left_out = []',
        "tests": [
            "tests.test_unread_lines.ARun."
            "test_a_splitter_that_leaves_lines_out_is_not_taken_at_its_word",
            "tests.test_unread_lines.ARun."
            "test_and_the_inventory_records_which_lines",
            "tests.test_unread_lines.ARun."
            "test_and_the_last_line_of_the_run_counts_them",
        ],
    },
    {
        "what": "inventory.py — the inventory does not say which lines it "
                "left out",
        "why": "the announcement scrolls away and the inventory is what "
               "synthesize.py reads. One that does not say cannot be told "
               "from one that left out none",
        "module": "inventory",
        "old": '            "runs": runs, "unread_lines": left_out, "sections": results}',
        "new": '            "runs": runs, "sections": results}',
        "tests": [
            "tests.test_unread_lines.ARun."
            "test_it_reads_the_whole_document_and_the_inventory_says_so",
            "tests.test_unread_lines.ARun."
            "test_and_the_inventory_records_which_lines",
            "tests.test_inventory.WhatIsWritten."
            "test_the_inventory_says_which_text_its_line_numbers_are_in",
            "tests.test_inventory.WhatIsWritten."
            "test_and_which_of_its_lines_are_in_no_section",
        ],
    },
    {
        "what": "synthesize.moved — a heading with nothing in it is found "
                "on a blank line",
        "why": "a bare \"#\" is a heading whose text is empty, and so is a "
               "blank line once its hashes are taken off. Since a short "
               "chunk is kept, such a line heads one more often, and a line "
               "added above it moved every later line without being noticed. "
               "The generated documents of tests/test_pointers_generated.py "
               "found it the first time they were cut the new way",
        "module": "synthesize",
        "old": '            if first.lstrip("#").strip() == heading and (heading or first):',
        "new": '            if first.lstrip("#").strip() == heading:',
        "tests": [
            "tests.test_pointers_generated.TheTextTheInventoryWasSplitFrom."
            "test_and_a_line_added_above_a_chunk_that_starts_on_a_heading_is_noticed",
            "tests.test_pointers.WhichDocument."
            "test_a_heading_with_nothing_in_it_is_not_found_on_a_blank_line",
        ],
    },
    {
        "what": "synthesize.lines_left_out — the inventory's own list is "
                "ignored",
        "why": "the gaps between sections cannot tell a blank line from one "
               "that holds text. An inventory that checked and found none "
               "would be doubted over its blank lines, and one that found "
               "some would be reported as a guess",
        "module": "synthesize",
        "old": '    if line_numbers(record):',
        "new": '    if False:',
        "tests": [
            "tests.test_unread_lines.TheInventoryLine."
            "test_an_inventory_that_left_no_line_out_says_so",
            "tests.test_unread_lines.TheInventoryLine."
            "test_lines_the_inventory_left_out_are_counted_and_named",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_with_no_line_left_out_the_gap_and_the_orphan_are_findings",
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_the_inventorys_own_list_is_taken_first",
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_and_an_empty_list_is_an_answer",
        ],
    },
    {
        "what": "synthesize.lines_left_out — an older inventory's gaps are "
                "not looked for",
        "why": "inv-ablation.json, which DESIGN §3.24-§3.30 quote from, was "
               "written before the lines left out were recorded. Its two "
               "show only as a gap between its sections' line numbers",
        "module": "synthesize",
        "old": '        if first > reached + 1:',
        "new": '        if False:',
        "tests": [
            "tests.test_unread_lines.TheInventoryLine."
            "test_an_older_inventory_shows_them_as_a_gap_between_its_sections",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_an_older_inventorys_gap_does_the_same",
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_without_one_the_gaps_between_the_sections_are_all_there_is",
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_lines_before_the_first_section_are_a_gap",
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_the_committed_inventory_has_two",
        ],
    },
    # What an independent review of the entries above found, each reproduced
    # before it was fixed. Four mutations it tried were caught by no test.
    {
        "what": "synthesize.line_numbers — a list of anything is the "
                "inventory's own account",
        "why": "entries that were not line numbers were dropped one by one "
               "and what was left was the record: [21.0, 22.0] read as \"no "
               "line left out\", and the absences were asserted over lines "
               "21 and 22",
        "module": "synthesize",
        "old": '    return all(type(n) is int and n > 0 for n in value)',
        "new": '    return True',
        "tests": [
            "tests.test_unread_lines.TheInventoryLine."
            "test_a_list_that_is_not_line_numbers_is_not_taken_as_the_record",
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_a_list_holding_numbers_that_are_not_line_numbers_is_no_record",
        ],
    },
    {
        "what": "synthesize.lines_left_out — a locator that runs backwards "
                "holds lines",
        "why": "\"d:40-23\" was read as reaching line 23, and the gap after "
               "it was then counted twice: \"36 line(s) ... lines 21-39, "
               "24-40\", of twenty",
        "module": "synthesize",
        "old": '        if found and int(found.group(1)) <= int(found.group(2)):',
        "new": '        if found:',
        "tests": [
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_a_locator_that_runs_backwards_names_no_lines",
        ],
    },
    {
        "what": "synthesize.lines_left_out — a section inside another ends "
                "the outer one",
        "why": "the lines between the end of the inner section and the end "
               "of the outer are then a gap, reported as lines no call was "
               "shown, and every absence is marked over them",
        "module": "synthesize",
        "old": '        reached = max(reached, last)',
        "new": '        reached = last',
        "tests": [
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_nor_is_a_section_inside_another",
        ],
    },
    {
        "what": "synthesize.lines_left_out — no locator to read is no gap "
                "found",
        "why": "\"going by its sections' line numbers there is none\", "
               "printed over an inventory none of whose sections gave a line "
               "number. Nothing was looked at, and it read as a look that "
               "found nothing",
        "module": "synthesize",
        "old": '    return gaps, BY_LOCATOR if spans else NOT_KNOWN',
        "new": '    return gaps, BY_LOCATOR',
        "tests": [
            "tests.test_unread_lines.WhatAnInventorySaysOfItsLines."
            "test_with_no_locator_that_can_be_read_nothing_is_known",
            "tests.test_unread_lines.TheInventoryLine."
            "test_an_inventory_whose_sections_name_no_lines_says_so",
        ],
    },
    {
        "what": "synthesize — a cycle of deferrals is put in doubt by lines "
                "no call was shown",
        "why": "a cycle is three deferrals found, and what was not read "
               "cannot unfind them. Marked with the rest, the one D6 the "
               "inventory establishes would read as a question",
        "module": "synthesize",
        "old": '                             labelled, ""))',
        "new": ('                             labelled, '
                'line_question(no_section, "could own it")))'),
        "tests": [
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_so_is_a_cycle_of_deferrals_and_the_header_does_not_say_otherwise",
        ],
    },
    {
        "what": "inventory.unread_lines — a section that starts before the "
                "first line is taken at its word",
        "why": "a start of 0 is a slice from the end of the list. It read as "
               "the last line, the text matched, and every line of the "
               "range counted as shown: the check, fooled by the thing it "
               "checks",
        "module": "inventory",
        "old": '        if not 1 <= start <= end <= len(lines):',
        "new": '        if False:',
        "tests": [
            "tests.test_unread_lines.WhatTheRunChecks."
            "test_nor_one_whose_lines_the_text_does_not_have",
        ],
    },
    {
        "what": "inventory.unread_lines — a section's first line is taken "
                "for its heading",
        "why": "a section whose text is every line after its first shows "
               "that first line only if it IS the heading. The test that "
               "was meant to hold this passed with the comparison deleted",
        "module": "inventory",
        "old": ('        headed = "\\n".join(span[1:]) == section["text"] and \\\n'
                '            span[0].strip().lstrip("#").strip() == section["heading"]'),
        "new": '        headed = "\\n".join(span[1:]) == section["text"]',
        "tests": [
            "tests.test_unread_lines.WhatTheRunChecks."
            "test_nor_does_one_whose_heading_is_not_the_line_it_starts_on",
        ],
    },
    {
        "what": "inventory.py — the lines of a section --limit stopped "
                "before are counted as lines in no section",
        "why": "they are in a section, and the section is recorded as not "
               "read. Counted again as lines the splitter lost, a run told "
               "to stop early blames the splitter for it. No test ran "
               "main() with --limit",
        "module": "inventory",
        "old": ('    left_out = unread_lines(lines, everything)\n'
                '    sections, beyond = limited(everything, args.doc, args.limit)'),
        "new": ('    sections, beyond = limited(everything, args.doc, args.limit)\n'
                '    left_out = unread_lines(lines, sections)'),
        "tests": [
            "tests.test_unread_lines.ARun."
            "test_a_section_limit_stopped_before_is_not_lines_in_no_section",
        ],
    },
    {
        "what": "inventory.py — a run with nothing to read divides by the "
                "time it took",
        "why": "older than the rest of this block: an empty document wrote "
               "its inventory and then died of ZeroDivisionError in the "
               "line that reports the rate, in more than half of 200 runs",
        "module": "inventory",
        "old": '    rate = len(sections) / elapsed if elapsed else 0.0',
        "new": '    rate = len(sections) / elapsed',
        "tests": [
            "tests.test_unread_lines.ARun."
            "test_a_document_with_nothing_in_it_is_read_as_that",
        ],
    },
    {
        "what": "inventory.reading — a document with no text is read 100%",
        "why": "nothing was read. \"100% of the document\" over no sections "
               "is true the way an empty tally is unanimous",
        "module": "inventory",
        "old": '    if not everything and not left_out:',
        "new": '    if False:',
        "tests": [
            "tests.test_unread_lines.WhatTheRunSays."
            "test_a_document_with_no_text_is_not_read_one_hundred_percent",
            "tests.test_unread_lines.ARun."
            "test_a_document_with_nothing_in_it_is_read_as_that",
        ],
    },
    {
        "what": "synthesize — every inventory is taken to have left no line "
                "out",
        "why": "\"74 of 74 sections read\" says nothing about a line that is "
               "in none of the 74, and said nothing for as long as there "
               "were two",
        "module": "synthesize",
        "old": '    no_section, line_record = lines_left_out(data)',
        "new": '    no_section, line_record = [], RECORDED',
        "tests": [
            "tests.test_unread_lines.TheInventoryLine."
            "test_lines_the_inventory_left_out_are_counted_and_named",
            "tests.test_unread_lines.TheInventoryLine."
            "test_an_older_inventory_with_no_gap_is_not_said_to_be_whole",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_an_orphan_a_line_in_no_section_could_consume",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_an_ownership_gap_a_line_in_no_section_could_close",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_a_score_says_what_the_inventory_did_not_read",
        ],
    },
    {
        "what": "synthesize — the lines left out are counted by their runs",
        "why": "\"1 line(s)\" over lines 284-285: a count of something other "
               "than what it names",
        "module": "synthesize",
        "old": '    return sum(last - first + 1 for first, last in left_out)',
        "new": '    return len(left_out)',
        "tests": [
            "tests.test_unread_lines.TheInventoryLine."
            "test_lines_the_inventory_left_out_are_counted_and_named",
            "tests.test_unread_lines.TheInventoryLine."
            "test_an_older_inventory_shows_them_as_a_gap_between_its_sections",
            "tests.test_unread_lines.TheInventoryLine."
            "test_a_long_list_is_cut_and_its_count_is_not",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_a_score_says_what_the_inventory_did_not_read",
        ],
    },
    {
        "what": "synthesize — a score counts the pairs it adjudicated as "
                "lines in no section",
        "why": "main() is one long namespace and already keeps a `count`: "
               "the pairs adjudicate_pairs tried. The first version of the "
               "score line kept its own under that name, and with "
               "--adjudicate printed \"has 1 line(s) in no section\" under a "
               "header that said 3",
        "module": "synthesize",
        "old": '                  f"{how_many(no_section)} line(s) in no section: a miss may "',
        "new": '                  f"{count} line(s) in no section: a miss may "',
        "tests": [
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_and_says_the_same_after_pairs_were_adjudicated",
        ],
    },
    {
        "what": "synthesize — an orphan is asserted over lines no call was "
                "shown",
        "why": "\"never consumed\" needs every line's consumes. One of the "
               "two lines the committed inventory left out is \"SF ingests "
               "telemetry and applies quality flags.\"",
        "module": "synthesize",
        "old": 'line_question(no_section, "could consume it")',
        "new": '""',
        "tests": [
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_an_orphan_a_line_in_no_section_could_consume",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_an_older_inventorys_gap_does_the_same",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_a_section_not_read_and_a_line_in_none_are_both_named",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_the_summary_counts_them",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_the_candidate_file_carries_the_verdict_and_the_lines",
        ],
    },
    {
        "what": "synthesize — an ownership gap is asserted over lines no "
                "call was shown",
        "why": "\"owned nowhere\" is a statement about every line of the "
               "document, and the owner can be named on one that is in no "
               "section",
        "module": "synthesize",
        "old": 'line_question(no_section, "could own it")',
        "new": '""',
        "tests": [
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_an_ownership_gap_a_line_in_no_section_could_close",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_an_older_inventorys_gap_does_the_same",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_the_summary_counts_them",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_the_candidate_file_carries_the_verdict_and_the_lines",
        ],
    },
    {
        "what": "synthesize.line_question — an absence is in doubt with no "
                "line left out",
        "why": "the same as for sections: marking that cries wolf is marking "
               "nobody reads. With every line in a section the absence is "
               "the inventory's to assert",
        "module": "synthesize",
        "old": '    if not left_out:\n        return ""',
        "new": '    if False:\n        return ""',
        "tests": [
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_with_no_line_left_out_the_gap_and_the_orphan_are_findings",
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_an_older_inventory_with_no_gap_keeps_its_findings",
        ],
    },
    {
        "what": "synthesize — a score says nothing of the lines the "
                "inventory left out",
        "why": "the score is of the inventory, not of the document. \"miss "
               "GT-D5-001\" was printed over an inventory that had never "
               "been shown one of that defect's corroborating anchors, with "
               "nothing beside it to say so",
        "module": "synthesize",
        "old": '        if unread or partial or short or no_section or aside:',
        "new": '        if unread or partial or short or aside:',
        "tests": [
            "tests.test_unread_lines.AnAbsenceALineInNoSectionCouldAnswer."
            "test_a_score_says_what_the_inventory_did_not_read",
        ],
    },
    {
        "what": "claim.judged — a pair nobody judged is dropped",
        "why": "\"judged 200/200, 0 contradictions\" is what a run printed "
               "when every call had been refused. The pair was skipped by "
               "`if result:` and the progress line went on counting it",
        "module": "claim",
        "old": '                unjudged.append(candidates[index - 1])',
        "new": '                pass',
        "tests": [
            "tests.test_claim.APairNobodyJudged."
            "test_it_is_kept_and_counted_apart",
            "tests.test_claim.APairNobodyJudged."
            "test_the_progress_line_counts_what_was_judged_not_what_was_tried",
            "tests.test_claim.APairNobodyJudged."
            "test_a_run_whose_calls_all_failed_judged_nothing",
        ],
    },
    {
        "what": "check-aggregation — the gate forgets `\"error\" not in`",
        "why": "it knew `!= \"error\"` and not this spelling, so "
               "synthesize.py's first line dropped every failed section in "
               "plain view of a gate that read it on every push",
        "module": "check-aggregation",
        "old": ('           |["\']error["\']\\s+not\\s+in\\b          '
                '# synthesize.py dropped every'),
        "new": ('                                                '
                '# synthesize.py dropped every'),
        "tests": [
            "tests.test_aggregation.AFilterThatDropsTheFailures."
            "test_keeping_only_the_entries_that_carry_no_error",
        ],
    },
    # Where a pointer points. D3 asked the inventory which sections exist, and
    # the inventory keeps the first heading of each chunk: a sub-section folded
    # into its parent's chunk, a stub too short to be kept, a heading spelled
    # "Section 3: ..." were each "no such section in the document".
    {
        "what": "synthesize — D3 asks the inventory which sections exist",
        "why": "the defect as it shipped. \"Section 3.1 — no such section in "
               "the document\", about a sub-section whose heading came two "
               "lines after its parent's and was folded into that chunk. "
               "Which sections a document has is the document's to say",
        "module": "synthesize",
        "old": '    document, unconsulted = consult(project, data)',
        "new": '    document, unconsulted = None, "not asked"',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_a_sub_section_folded_into_its_parents_chunk_is_found_there",
            "tests.test_pointers.ASectionThatExists."
            "test_a_section_is_all_of_its_lines_not_its_first_chunk",
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_a_section_an_older_inventory_has_no_chunk_for",
            "tests.test_pointers.ASectionThatDoesNot."
            "test_a_section_nobody_wrote_is_called_missing",
        ],
    },
    # What D3 stands behind. Two reviews each broke a version of this a dozen
    # ways, nearly all through one step: deciding which lines of a text are
    # headings when the text does not say.
    {
        "what": "synthesize.self_claims — a finding is asserted in text that "
                "marks no headings",
        "why": "in text from a .docx a cell of a table reads \"10 km\", a "
               "step reads \"1. Stop the pump\", and a tab lost on extraction "
               "leaves \"3.1Drift limits\". Every rule that told those from a "
               "heading asserted something false about some document, so "
               "nothing found by such a rule is asserted",
        "module": "synthesize",
        "old": '                if not document["marked"]:\n'
               '                    doubts.append(UNMARKED)',
        "new": '                if False:\n'
               '                    doubts.append(UNMARKED)',
        "tests": [
            "tests.test_pointers.TextFromADocx."
            "test_a_claim_a_section_does_not_hold_is_listed_and_not_asserted",
            "tests.test_pointers_generated.OnDocumentsWhoseAnswersAreKnown."
            "test_in_text_that_marks_no_headings_it_asserts_nothing",
        ],
    },
    {
        "what": "synthesize.unsettled — in text that marks no headings, a "
                "section no line opens with is called missing",
        "why": "Word keeps the numbers it gives headings itself out of the "
               "paragraph's text. With a table whose rows read \"1 - Low\", "
               "\"2 - Medium\", \"3 - High\" for evidence of numbering, "
               "Section 5 of such a document was \"no such section\"",
        "module": "synthesize",
        "old": '    if not document["marked"]:\n        return UNMARKED',
        "new": '    if False:\n        return UNMARKED',
        "tests": [
            "tests.test_pointers.TextFromADocx."
            "test_a_section_no_line_opens_with_is_listed_and_not_called_missing",
            "tests.test_pointers.TextFromADocx."
            "test_markdown_frozen_from_a_docx_is_not_a_markdown_source",
        ],
    },
    {
        "what": "synthesize.consult — the text says whether the document "
                "marks its headings",
        "why": "two lines of shell in a .docx, \"# 1. Stop the service\" and "
               "\"# 2. Apply the migration\", were taken for marks: they "
               "became sections 1 and 2, no real heading was a heading any "
               "more, and two true claims were reported. What the source was "
               "is the manifest's to say",
        "module": "synthesize",
        "old": '    marked = str(doc.get("path") or "").lower().endswith(MARKDOWN) and \\',
        "new": '    marked = True and \\',
        "tests": [
            "tests.test_pointers.TextFromADocx."
            "test_two_lines_of_shell_do_not_make_it_a_document_that_marks",
            "tests.test_pointers.TextFromADocx."
            "test_markdown_frozen_from_a_docx_is_not_a_markdown_source",
        ],
    },
    {
        "what": "synthesize.self_claims — a finding is asserted without the "
                "document",
        "why": "with no frozen text beside the inventory only the chunks "
               "headed by the section's number are looked in, and a section "
               "runs on under whatever headings the splitter chose. What is "
               "not found there is a question, not a finding",
        "module": "synthesize",
        "old": '            doubts.append(UNCONSULTED)',
        "new": '            pass',
        "tests": [
            "tests.test_pointers.WithoutTheDocument."
            "test_and_what_is_not_found_there_is_listed_and_not_asserted",
        ],
    },
    {
        "what": "synthesize.self_claims — a section is the chunk that starts "
                "on its heading",
        "why": "a section runs from its heading to the next one that is not "
               "inside it, over however many chunks the splitter made of "
               "those lines. Looking only in the chunk it heads loses a "
               "sub-section inside its parent's chunk, and the part of a "
               "section the splitter filed under a numbered step",
        "module": "synthesize",
        "old": '                if held and any(first <= held[1] and held[0] <= last',
        "new": '                if held and any(first == held[0]',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_a_sub_section_folded_into_its_parents_chunk_is_found_there",
            "tests.test_pointers.ASectionThatExists."
            "test_a_section_is_all_of_its_lines_not_its_first_chunk",
            "tests.test_pointers.ASectionThatExists."
            "test_and_in_the_last_of_them",
            "tests.test_pointers.TextFromADocx."
            "test_a_section_whose_heading_heads_no_chunk_is_found",
        ],
    },
    {
        "what": "synthesize.located — a place is its own heading and nothing "
                "under it",
        "why": "a document can number 5.1 and 5.2 and never write a heading "
               "for 5. The old lookup reached the sub-sections by the prefix "
               "of their headings, and a first rewrite did not",
        "module": "synthesize",
        "old": '    return [head for head in heads if head["key"] and under(key, head["key"])]',
        "new": '    return [head for head in heads if head["key"] == key]',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_a_section_with_no_line_of_its_own_is_its_sub_sections",
        ],
    },
    {
        "what": "synthesize.self_claims — a section found by its sub-sections "
                "alone is taken for the whole of it",
        "why": "a heading set in bold above sub-sections that are marked. The "
               "text between it and 3.1 belongs to Section 3, only 3.1 was "
               "looked in, and a true claim about Section 3 was reported "
               "with no doubt",
        "module": "synthesize",
        "old": '                if not any(same(key, head["key"]) for head in heads):',
        "new": '                if False:',
        "tests": [
            "tests.test_pointers.APlaceInDoubtBesideOneThatWasFound."
            "test_the_section_is_found_by_its_sub_sections_and_has_a_line_of_its_own",
        ],
    },
    {
        "what": "synthesize.places — a table's number is a section's",
        "why": "every number in a pointer was taken for a section number, so "
               "\"Table 4\" was looked up as section 4 and reported as holding "
               "no such thing, or as a missing section when there was no "
               "section 4",
        "module": "synthesize",
        "old": '        if owed == 1 and kind == "number":',
        "new": '        if False:',
        "tests": [
            "tests.test_pointers.APlaceThatIsNotASection."
            "test_a_page_number_is_not_a_section_number",
            "tests.test_pointers.WhatAPointerNames."
            "test_a_section_with_a_table_in_it_is_still_the_section",
            "tests.test_pointers.WhatAPointerNames."
            "test_a_number_is_given_up_only_to_the_word_that_owns_it",
        ],
    },
    {
        "what": "synthesize.places — a word for a table or a step takes every "
                "number after it",
        "why": "\"Section 1 (Line protection) and 3\": \"line\" is a word "
               "that counts something, it owns no number here, and a rewrite "
               "gave it the 3. A true claim about Section 3 was reported. A "
               "number is given up only to the word it stands straight after",
        "module": "synthesize",
        "old": '        owed = 0\n        if kind in',
        "new": '        if kind in',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_a_section_named_after_a_title_with_a_counting_word_in_it",
            "tests.test_pointers.WhatAPointerNames."
            "test_a_number_is_given_up_only_to_the_word_that_owns_it",
        ],
    },
    {
        "what": "synthesize.places — a pointer to several sections names no "
                "place",
        "why": "the test for a place was the whole word `section`, which "
               "\"Sections 6.4, 7.4\" does not contain. Five of the fixture's "
               "pointers were skipped as pointing outside the document",
        "module": "synthesize",
        "old": 'PART = (r"(?<![\\w-])(?i:(?:sub-?)?sections?\\b|sect?\\b\\.?|clauses?\\b"',
        "new": 'PART = (r"(?<![\\w-])(?i:section\\b|clauses?\\b"',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_several_sections_named_at_once",
            "tests.test_pointers.WhatAPointerNames."
            "test_a_list_of_sections",
            "tests.test_pointers.WhatAPointerNames."
            "test_the_other_ways_of_writing_section",
        ],
    },
    {
        "what": "synthesize.places — a list of sections is read short of its "
                "last member",
        "why": "a first rewrite took \", and\" for the end of the list, so "
               "\"Sections 5, 6, and 7\" was 5 and 6 and a claim Section 7 "
               "holds was reported. Reading too few places is the worse "
               "mistake: every number after the word is a section's",
        "module": "synthesize",
        "old": '                last = start = None         # a word: no range runs across it',
        "new": '                carrier = last = start = None',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_the_last_of_a_list_written_with_a_comma_before_and",
            "tests.test_pointers.WhatAPointerNames."
            "test_every_member_of_a_list_however_it_is_joined",
        ],
    },
    {
        "what": "synthesize.places — \"Section 2 onwards\" is Section 2",
        "why": "a pointer that names more places than it lists. What it does "
               "not hold cannot be told from the one section it spells out, "
               "and a claim that 3.1 holds was reported against 2",
        "module": "synthesize",
        "old": '            more, last, start = True, None, None',
        "new": '            more, last, start = False, None, None',
        "tests": [
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_a_pointer_that_names_more_places_than_it_lists",
            "tests.test_pointers.WhatAPointerNames."
            "test_a_pointer_that_names_more_than_it_lists",
        ],
    },
    {
        "what": "synthesize.self_claims — the passage a claim is in is not "
                "one of the places it names",
        "why": "\"this section and Appendix A\": the claim is held where it "
               "was made, Appendix A does not hold it, and it was reported "
               "as held nowhere",
        "module": "synthesize",
        "old": '        if home and OWN.search(target):',
        "new": '        if False:',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_the_passage_a_claim_is_in_is_one_of_the_places_it_names",
        ],
    },
    {
        "what": "synthesize.leading — the number a line opens with is cut short",
        "why": "\"3.1<TAB>Drift limits\" comes out of the extractor as "
               "\"3.1Drift limits\". A reading that wants a space or a full "
               "stop after the number backs off to 3, and 3.1 becomes a "
               "section no line of the document opens with",
        "module": "synthesize",
        "old": 'NUMBERED = re.compile(rf"(?:(?i:section|clause|chapter)\\s+)?({NUMBER})")',
        "new": 'NUMBERED = re.compile(rf"(?:(?i:section|clause|chapter)\\s+)?({NUMBER})(?=[\\s.:)]|$)")',
        "tests": [
            "tests.test_pointers.WhatALineOpensWith."
            "test_the_number_is_taken_whole",
            "tests.test_pointers.TextFromADocx."
            "test_a_sub_section_whose_tab_the_extractor_dropped_is_found",
        ],
    },
    {
        "what": "synthesize.leading — a heading that says Section has no "
                "number",
        "why": "a heading had to START with its number, so \"Section 3: "
               "Calibration Register\" was not section 3 and a pointer to "
               "Section 3 was a pointer to nothing",
        "module": "synthesize",
        "old": 'NUMBERED = re.compile(rf"(?:(?i:section|clause|chapter)\\s+)?({NUMBER})")',
        "new": 'NUMBERED = re.compile(rf"({NUMBER})")',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_a_heading_that_spells_out_the_word_section",
            "tests.test_pointers.WithoutTheDocument."
            "test_and_so_is_one_whose_heading_spells_out_the_word_section",
        ],
    },
    {
        "what": "synthesize.heading_key — a marked heading's number may run "
                "into its title",
        "why": "\"# 3D flood twin design\" at the head of a file was section "
               "3, one level up from every section, so Section 3 was the "
               "whole document and nothing was ever missing from it",
        "module": "synthesize",
        "old": '    if not found or (strict and found[1] and found[1][0] not in',
        "new": '    if not found or (False and found[1] and found[1][0] not in',
        "tests": [
            "tests.test_pointers.ASectionThatDoesNot."
            "test_a_title_that_opens_with_a_number_is_not_that_section",
            "tests.test_pointers.WhatALineOpensWith."
            "test_a_marked_heading_has_to_end_its_number_where_a_number_ends",
        ],
    },
    {
        "what": "synthesize.heading_key — what stands in front of a number "
                "is part of the line",
        "why": "\"- 4.2 The twin shall ...\", \"| 4.2 | ... |\", \"**4.2**\", "
               "'<a name=\"x\"></a>4.2': the number is there, behind a list "
               "mark, a table bar, asterisks or an anchor, and \"clause 4.2 — "
               "no such section\" was printed about it",
        "module": "synthesize",
        "old": '    found = leading(FRONT.sub("", str(text or "")))',
        "new": '    found = leading(str(text or "").strip())',
        "tests": [
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_nor_one_that_opens_with_it_behind_a_list_or_a_table_mark",
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_a_line_that_opens_with_the_number_and_is_not_marked_a_heading",
            "tests.test_pointers.ASectionThatExists."
            "test_a_heading_behind_an_anchor",
        ],
    },
    {
        "what": "synthesize.prose — a heading the author commented out is a "
                "heading",
        "why": "\"## 5. Deployment (old)\" inside a comment ended Section 4 "
               "there, and was a Section 5 the document does not have",
        "module": "synthesize",
        "old": '        elif line.lstrip().startswith("<!--") and "-->" not in line:',
        "new": '        elif False:',
        "tests": [
            "tests.test_pointers.ASectionThatDoesNot."
            "test_a_heading_the_author_commented_out_is_not_one",
        ],
    },
    {
        "what": "synthesize.prose — a comment between <pre> and its close is "
                "a heading",
        "why": "raw HTML that Markdown does not read into. \"# 2. start the "
               "service\" in a listing set that way was a section 2, which "
               "ended Section 1 above the lines that held the claim",
        "module": "synthesize",
        "old": '        elif opened and f"</{opened.group(1).lower()}>" not in line.lower():\n'
               '            raw = opened.group(1).lower()',
        "new": '        elif False:\n'
               '            raw = opened.group(1).lower()',
        "tests": [
            "tests.test_pointers.WhatTheDocumentIsDividedInto."
            "test_nor_between_pre_and_its_close",
        ],
    },
    {
        "what": "synthesize.prose — a fence is closed by a shorter one",
        "why": "a template fenced in four backticks with a listing in three "
               "inside it. The inner fence closed the outer, and \"# 2. the "
               "command that was run\" was a heading",
        "module": "synthesize",
        "old": '                    and len(found.group(2)) >= len(fence) \\',
        "new": '                    and True \\',
        "tests": [
            "tests.test_pointers.WhatTheDocumentIsDividedInto."
            "test_a_fence_is_closed_only_by_one_as_long",
        ],
    },
    {
        "what": "synthesize.inside — a heading with no number ends the place "
                "before it",
        "why": "\"## A.2 Register of gauges\" after \"## Appendix A\", and "
               "\"## Detailed design\" after \"## 3. Design\". Neither says "
               "that it is another place; each ended the place there, and a "
               "claim held under it was reported as one the place does not "
               "hold",
        "module": "synthesize",
        "old": '    if not later["key"] or later["level"] > head["level"]:',
        "new": '    if later["level"] > head["level"]:',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_a_part_of_an_appendix_marked_at_the_appendixs_own_depth",
            "tests.test_pointers.ASectionThatExists."
            "test_a_heading_with_no_number_at_the_sections_own_depth",
        ],
    },
    {
        "what": "synthesize.inside — numbering that starts again ends the "
                "appendix",
        "why": "\"## 2. Register of gauges\" after \"## Appendix A\" was "
               "taken for the document's Section 2 over again. The appendix "
               "ended there, and a claim held in its second part was reported "
               "against it",
        "module": "synthesize",
        "old": '    return later["level"] == head["level"] and \\\n'
               '        head["key"][0] != "section" and later["key"][0] == "section"',
        "new": '    return False',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_numbering_that_starts_again_under_an_appendix",
        ],
    },
    {
        "what": "synthesize.apart — a step numbered under a heading is one of "
                "the document's sections",
        "why": "a document numbered when it is rendered has no numbers on its "
               "headings. \"### 2. Apply the migration\" under \"## "
               "Operations\" was taken for the Section 2 its prose points to, "
               "and a true claim about the Hydrology Model was reported with "
               "no doubt",
        "module": "synthesize",
        "old": '        and not (other["key"] and under(other["key"], head["key"]))',
        "new": '        and False',
        "tests": [
            "tests.test_pointers.NumbersThatAreNotTheDocumentsSections."
            "test_a_step_numbered_under_a_heading_is_not_the_documents_section",
            "tests.test_pointers.NumbersThatAreNotTheDocumentsSections."
            "test_sub_sections_under_a_heading_that_has_no_number",
        ],
    },
    {
        "what": "synthesize.own — numbers under a heading show how the "
                "document numbers its sections",
        "why": "three steps numbered 1 to 3 under one heading made \"Section "
               "5 — no such section in the document\" of a document whose "
               "sections carry no numbers in the source",
        "module": "synthesize",
        "old": '            if head["key"] and head["key"][0] == kind and not head["over"]]',
        "new": '            if head["key"] and head["key"][0] == kind]',
        "tests": [
            "tests.test_pointers.NumbersThatAreNotTheDocumentsSections."
            "test_nor_do_the_steps_show_how_the_document_numbers_its_sections",
            "tests.test_pointers.NumbersThatAreNotTheDocumentsSections."
            "test_nor_do_steps_under_a_heading_that_opens_with_a_number",
        ],
    },
    {
        "what": "synthesize.self_claims — a numbered line that heads text of "
                "its own is not looked at",
        "why": "sections written as plain or bold numbered lines, in a "
               "document that marks two numbered steps with \"#\". \"Section "
               "2\" was looked for under \"### 2. Apply the migration\", and "
               "a true claim about the Hydrology Model was reported with no "
               "doubt",
        "module": "synthesize",
        "old": '                elif key in document["loose"]:',
        "new": '                elif False:',
        "tests": [
            "tests.test_pointers.NumbersThatAreNotTheDocumentsSections."
            "test_a_step_marked_as_a_heading_beside_a_section_that_is_not",
        ],
    },
    {
        "what": "synthesize.unlisted — an item of a list is a heading in all "
                "but the mark",
        "why": "the other side of that rule. With every numbered line taken "
               "for a heading nobody marked, the three steps of a list put "
               "Sections 1 to 3 in doubt, and D3 stands behind nothing that a "
               "document with a list in it says about them",
        "module": "synthesize",
        "old": '        if key and key[0] == "section" and not any(\n'
               '                (keys.get(other) or ("",))[0] == "section" for other in beside):',
        "new": '        if key and key[0] == "section":',
        "tests": [
            "tests.test_pointers.ASectionThatDoesNot."
            "test_a_numbered_step_in_a_list_is_not_a_section",
            "tests.test_pointers.WhichLinesHeadTextWithoutAMark."
            "test_the_steps_of_the_document_are_a_list",
        ],
    },
    {
        "what": "synthesize.self_claims — \"this section\" said in two "
                "sections is one claim",
        "why": "true where Section 1 says it and untrue where Section 2 "
               "does. The second was counted as a repeat of the first and "
               "never looked at",
        "module": "synthesize",
        "old": '                       OWN.search(target) and str(claim.get("_locator")))',
        "new": '                       None)',
        "tests": [
            "tests.test_pointers.HowManyClaimsWereChecked."
            "test_this_section_said_in_two_sections_is_two_claims",
        ],
    },
    {
        "what": "synthesize.places — a range that cannot be filled in is its "
                "two ends and no more",
        "why": "\"Sections 4a to 7\", \"Sections 7 to 3\": what lies between "
               "the ends is not known, the pointer was read as the two of "
               "them, and a claim held between them was reported with no "
               "doubt",
        "module": "synthesize",
        "old": '            more = more or not run',
        "new": '            more = more',
        "tests": [
            "tests.test_pointers.WhatAPointerNames."
            "test_and_names_more_than_them",
        ],
    },
    {
        "what": "synthesize.self_claims — \"this section\" is the passage "
                "and none of the section around it",
        "why": "said in the first lines of Section 3 and held in 3.1, which "
               "has a chunk of its own, or in the second piece of a section "
               "cut for size. Only the chunk the claim came from was looked "
               "in, and a true claim was reported",
        "module": "synthesize",
        "old": '                    if head["key"] and head["line"] <= at[0] <= head["end"]][-1:]',
        "new": '                    if head["key"] and head["line"] <= at[0] <= head["end"]][:0]',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_this_section_is_the_whole_of_the_section_the_claim_is_made_in",
        ],
    },
    {
        "what": "synthesize.prose — a closing fence set in four spaces further "
                "still closes",
        "why": "four spaces in from a fence at the margin is a line of the "
               "listing. Taken for the end of it, the comment on the next "
               "line was a heading and the real end opened a fence that hid "
               "the headings after it",
        "module": "synthesize",
        "old": '                    and len(found.group(1)) < depth + 4:',
        "new": '                    and True:',
        "tests": [
            "tests.test_pointers.WhatTheDocumentIsDividedInto."
            "test_a_fence_is_closed_by_one_set_in_no_further_than_it",
        ],
    },
    {
        "what": "synthesize.consult — a name with .md anywhere in it is a "
                "Markdown source",
        "why": "D3 asserts only in a Markdown source, and which source that "
               "is was tested for one name. \"design.md.docx\" is text out "
               "of an extractor, which marks nothing",
        "module": "synthesize",
        "old": '    marked = str(doc.get("path") or "").lower().endswith(MARKDOWN) and \\',
        "new": '    marked = MARKDOWN in str(doc.get("path") or "").lower() and \\',
        "tests": [
            "tests.test_pointers.WhichDocument."
            "test_which_sources_are_markdown",
        ],
    },
    {
        "what": "synthesize.consult — a text that is its own source is held to "
                "its line numbers all the same",
        "why": "freeze.py stores a .md as it is, so the source's hash, which "
               "is all an old inventory has, is the text's. An unchanged "
               "Markdown text whose chunks start on no heading was refused as "
               "\"not the text this inventory was built from\"",
        "module": "synthesize",
        "old": '        astray = data["source_sha256"] != doc.get("text_sha256") and \\',
        "new": '        astray = True and \\',
        "tests": [
            "tests.test_pointers.WhichDocument."
            "test_an_old_inventory_of_a_text_that_is_its_own_source",
        ],
    },
    {
        "what": "synthesize.prose — a comment in the front matter of a file "
                "is a heading",
        "why": "\"# template version 3\" between the two rules a file opens "
               "with was the first heading of the document. The title was "
               "then the second, and every section under it was taken for "
               "one numbered apart from the document's own: nothing asserted",
        "module": "synthesize",
        "old": '        if number <= front:\n            continue',
        "new": '        if False:\n            continue',
        "tests": [
            "tests.test_pointers.WhatTheDocumentIsDividedInto."
            "test_a_comment_in_the_front_matter_is_not_a_heading",
        ],
    },
    {
        "what": "synthesize.self_claims — a sub-section with no heading of its "
                "own is not looked for in its section",
        "why": "deep headings are often set in bold. \"Section 3.2\", under "
               "\"**3.2 Drift history**\", was a question for a reviewer "
               "whether or not the claim was held there, when Section 3 "
               "could have been looked in",
        "module": "synthesize",
        "old": '                while why and key[0] == "section" and "." in stem and not heads:',
        "new": '                while False:',
        "tests": [
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_and_it_is_looked_for_in_the_section_it_is_part_of",
        ],
    },
    {
        "what": "synthesize — a hit that rests on a question is not said to",
        "why": "--ground-truth counts a finding marked unverifiable as a hit, "
               "and said so only when a section had gone unread. In text from "
               "a .docx every D3 finding is a question, and the score printed "
               "three planted defects FOUND with nothing beside it",
        "module": "synthesize",
        "old": '        elif resting:',
        "new": '        elif False:',
        "tests": [
            "tests.test_pointers.WhatTheScoreRestsOn."
            "test_a_hit_that_rests_on_a_question_is_said_to",
        ],
    },
    {
        "what": "synthesize.unsettled — a heading that has the number in it "
                "is not looked at",
        "why": "\"## Part 4: Algorithms\", and \"### C. Register of "
               "gauges\" under \"## Appendices\". Neither opens with the "
               "place as D3 reads it, each is the place, and each was "
               "\"no such section in the document\"",
        "module": "synthesize",
        "old": '    return renamed(key, document["heads"])',
        "new": '    return ""',
        "tests": [
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_a_heading_that_has_the_number_in_it_and_does_not_open_with_it",
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_nor_an_appendix_headed_by_its_letter_alone",
        ],
    },
    {
        "what": "synthesize.self_claims — a pointer with more in it than its "
                "places is asserted",
        "why": "whose a number is, in a pointer with other words in it, is a "
               "guess. \"Tables 4 and 5 of the calibration section\" was "
               "read as Section 5, \"the calibration section, 12 gauges\" as "
               "Section 12 and \"Section 9 of the interface control "
               "document\" as this document's: each \"no such section in the "
               "document\". \"Sections 2 up to and including 4\" was read as "
               "2 and 4, and reported a claim that Section 3 holds",
        "module": "synthesize",
        "old": '        elif not bare(target):',
        "new": '        elif False:',
        "tests": [
            "tests.test_pointers.APointerThatSaysMoreThanItsPlaces."
            "test_a_section_of_another_document",
            "tests.test_pointers.APointerThatSaysMoreThanItsPlaces."
            "test_tables_named_before_the_section_they_are_in",
            "tests.test_pointers.APointerThatSaysMoreThanItsPlaces."
            "test_a_number_that_counts_something",
            "tests.test_pointers.APointerThatSaysMoreThanItsPlaces."
            "test_a_range_written_in_words_this_does_not_know",
        ],
    },
    {
        "what": "synthesize.bare — \"and\" is a word like any other",
        "why": "the other side of that rule. With every word taken for "
               "something more than a place, \"Section 14 and Appendix F\" "
               "is in doubt, and D3 stands behind nothing that names two "
               "places",
        "module": "synthesize",
        "old": '                    and found.group().lower() not in ("and", "or")):',
        "new": '                    and True):',
        "tests": [
            "tests.test_pointers.ASectionThatDoesNot."
            "test_one_place_that_is_missing_beside_one_that_holds_nothing",
            "tests.test_pointers.WhatAPointerIsMadeOf."
            "test_places_and_what_joins_them",
        ],
    },
    {
        "what": "synthesize.places — a range written with two marks starts "
                "at the second",
        "why": "\"Sections 2--4\": the second hyphen forgot where the range "
               "began, the pointer was read as 2 and 4, and a claim that "
               "Section 3 holds was reported",
        "module": "synthesize",
        "old": '            start, last, between = last or start, None, False',
        "new": '            start, last, between = last, None, False',
        "tests": [
            "tests.test_pointers.WhatAPointerNames."
            "test_a_range_written_with_two_marks_or_with_dots",
        ],
    },
    {
        "what": "synthesize.leading — a capital after the number is not part "
                "of it",
        "why": "\"## 4A. Overfitting audit\" was read as a title, the way "
               "\"3D flood twin design\" is, and \"Section 4a — no such "
               "section in the document\" was printed about it",
        "module": "synthesize",
        "old": '    if re.match(r"[a-z](?=[\\s.:)]|$)|[A-Z](?=[.:)]|$)", rest):',
        "new": '    if re.match(r"[a-z](?=[\\s.:)]|$)", rest):',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_a_section_numbered_with_a_capital_after_the_number",
            "tests.test_pointers.WhatALineOpensWith."
            "test_and_so_is_a_capital_with_a_stop_after_it",
        ],
    },
    {
        "what": "synthesize.unsettled — a kind of place the document has none "
                "of is called missing",
        "why": "a document with no annex among its headings says nothing "
               "about what an annex would be called in it, or whether the "
               "annexes are separate files. \"Annex B — no such section\" "
               "would be the tool's ignorance reported as the author's defect",
        "module": "synthesize",
        "old": "    if not names:\n        return f\"none of the document's headings",
        "new": "    if False:\n        return f\"none of the document's headings",
        "tests": [
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_a_kind_of_place_the_document_has_none_of",
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_nor_one_place_of_two_when_the_other_cannot_be_looked_up",
        ],
    },
    {
        "what": "synthesize.numbered — one heading that opens with a number "
                "shows a document that numbers its sections",
        "why": "\"## 3 options considered\" among headings that carry no "
               "numbers. With that for evidence, Section 9 was \"no such "
               "section\" in a document that numbers nothing",
        "module": "synthesize",
        "old": '    if any(n + 1 in tops for n in tops):',
        "new": '    if tops:',
        "tests": [
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_a_document_that_numbers_none_of_its_sections",
        ],
    },
    {
        "what": "synthesize.unsettled — a line that opens with the number "
                "does not count",
        "why": "deep headings are often set in bold and not marked. A section "
               "is called missing only when no line of the document opens "
               "with its number; the outline alone would call \"**3.2 Drift "
               "history**\" a section that is not there",
        "module": "synthesize",
        "old": '        if under(key, other):\n            return (f"line {line}',
        "new": '        if False:\n            return (f"line {line}',
        "tests": [
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_a_line_that_opens_with_the_number_and_is_not_marked_a_heading",
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_nor_one_inside_a_fenced_block",
        ],
    },
    {
        "what": "synthesize.self_claims — a section the inventory never saw "
                "is called missing",
        "why": "until 2026-10-04 the splitter dropped a chunk of sixty "
               "characters or fewer, heading and all, so in an inventory cut "
               "before then a stub section is in the document and in no "
               "chunk. It was \"no such section in the document\"",
        "module": "synthesize",
        "old": '        if not hits and spans:',
        "new": '        if False:',
        "tests": [
            "tests.test_pointers.APlaceThatCannotBeLookedUp."
            "test_a_section_an_older_inventory_has_no_chunk_for",
        ],
    },
    {
        "what": "synthesize.self_claims — a doubt about the place is dropped "
                "once one chunk of it is found",
        "why": "\"Sections 2 and 3.2\", where 3.2 is a line in bold: Section "
               "2 was found, did not hold the claim, and the finding was "
               "asserted with the reason for doubting it already in hand. "
               "A claim is called untrue only when every place it names was "
               "found or shown to be missing",
        "module": "synthesize",
        "old": '                "read") or doubt))',
        "new": '                "read")))',
        "tests": [
            "tests.test_pointers.APlaceInDoubtBesideOneThatWasFound."
            "test_the_other_is_a_line_the_document_does_not_mark_as_a_heading",
            "tests.test_pointers.APlaceInDoubtBesideOneThatWasFound."
            "test_the_other_is_of_a_kind_the_document_has_none_of",
            "tests.test_pointers.APlaceInDoubtBesideOneThatWasFound."
            "test_part_of_the_place_is_in_no_chunk",
        ],
    },
    {
        "what": "synthesize.self_claims — lines of the place that no chunk "
                "covers are not a doubt",
        "why": "an inventory cut before 2026-10-04 has lines in no chunk: "
               "a stub the splitter dropped, a numbered step replaced by the "
               "next. What the rest of the section does not hold, those "
               "lines may, and nothing read them",
        "module": "synthesize",
        "old": '            if hits and unseen:',
        "new": '            if False:',
        "tests": [
            "tests.test_pointers.APlaceInDoubtBesideOneThatWasFound."
            "test_part_of_the_place_is_in_no_chunk",
        ],
    },
    {
        "what": "synthesize.consult — an old inventory is not held to its "
                "line numbers",
        "why": "freeze.py --refreeze leaves the source's hash as it was and "
               "moves every line. An inventory that records only that hash "
               "would be read against headings one line off, and D3 would "
               "look for each section in the wrong chunk without a word",
        "module": "synthesize",
        "old": '            moved(data.get("sections") or [], lines)',
        "new": '            ""',
        "tests": [
            "tests.test_pointers.WhichDocument."
            "test_a_re_freeze_that_moved_the_lines_is_noticed",
            "tests.test_pointers.WhichDocument."
            "test_and_so_is_one_that_cut_the_text_short",
        ],
    },
    {
        "what": "synthesize.moved — a chunk cut for size is taken for a text "
                "that moved",
        "why": "a section longer than the size cap is cut and the pieces "
               "share its heading, so the second does not start on a heading "
               "line. Taken as a mismatch, every long document would go "
               "unconsulted",
        "module": "synthesize",
        "old": '        if heading not in ("(front matter)", before):',
        "new": '        if heading not in ("(front matter)",):',
        "tests": [
            "tests.test_pointers.ASectionThatExists."
            "test_a_section_cut_into_chunks_is_looked_for_in_the_first_of_them",
        ],
    },
    {
        "what": "synthesize.moved — the chunk after a failed section is held "
                "to a heading line",
        "why": "a failed section was once stored as {\"error\": ...} with no "
               "heading. The piece after it, of a section cut for size, "
               "carries a heading that is on no line of its own, and the "
               "unchanged text was refused as one that had moved",
        "module": "synthesize",
        "old": '            elif not blind:',
        "new": '            else:',
        "tests": [
            "tests.test_pointers.WhichDocument."
            "test_a_failed_section_with_no_heading_does_not_refuse_the_text",
        ],
    },
    {
        "what": "synthesize.moved — an inventory nothing ties to the text is "
                "taken on trust",
        "why": "where the splitter took no line for a heading, every chunk is "
               "front matter cut for size and no chunk can be looked for at "
               "its line. A text six lines longer at the top was consulted, "
               "and two true claims were reported from the wrong chunks",
        "module": "synthesize",
        "old": '    if not tied:',
        "new": '    if False:',
        "tests": [
            "tests.test_pointers.WhichDocument."
            "test_an_inventory_no_chunk_of_which_starts_on_a_heading",
        ],
    },
    {
        "what": "synthesize.consult — the hash of the text is not compared",
        "why": "the inventory's locators are line numbers in one text. Read "
               "against another, a heading found at line 300 says nothing "
               "about the chunk the inventory has at 300",
        "module": "synthesize",
        "old": '        if data["text_sha256"] != doc.get("text_sha256"):',
        "new": '        if False:',
        "tests": [
            "tests.test_pointers.WhichDocument."
            "test_and_a_text_with_another_hash_is_not_consulted",
        ],
    },
    {
        "what": "synthesize.consult — the hash of the source is not compared",
        "why": "the same, for an inventory written before the text's own hash "
               "was kept",
        "module": "synthesize",
        "old": '        if data["source_sha256"] != doc.get("source_sha256"):',
        "new": '        if False:',
        "tests": [
            "tests.test_pointers.WhichDocument."
            "test_a_document_whose_source_changed_is_not_consulted",
        ],
    },
    {
        "what": "synthesize — the claims D3 did not check are not counted",
        "why": "D3 checked 9 of the fixture's 122 evidence claims and said "
               "nothing about the other 113. A reader took its silence about "
               "a pointer for a pointer that had been checked",
        "module": "synthesize",
        "old": '    if left_out:\n        print(',
        "new": '    if False:\n        print(',
        "tests": [
            "tests.test_pointers.HowManyClaimsWereChecked."
            "test_the_line_counts_what_was_checked_and_why_the_rest_was_not",
            "tests.test_pointers.APlaceThatIsNotASection."
            "test_a_table_is_not_section_four",
        ],
    },
    {
        "what": "synthesize.self_claims — a claim with no place to check it "
                "against counts as checked",
        "why": "\"D3 checked 3 of 3 evidence claims against the place each "
               "points to\", with the document absent and two of the three "
               "listed as not found among any headings. Nothing was compared "
               "with anything to reach those two",
        "module": "synthesize",
        "old": '        looked += bool(hits)',
        "new": '        looked += 1',
        "tests": [
            "tests.test_pointers.HowManyClaimsWereChecked."
            "test_a_claim_with_no_place_to_check_it_against_is_not_one_checked",
            "tests.test_pointers.WithoutTheDocument."
            "test_a_pointer_no_chunk_heading_matches_is_in_doubt_not_missing",
        ],
    },
    {
        "what": "synthesize.BOILERPLATE — \"through\" and \"across\" are what "
                "a claim says",
        "why": "\"Requirements R-001 through R-042 are addressed across "
               "Sections 2 to 13\" is the traceability sentence about a "
               "range. With those two words counted as content it is checked "
               "against twelve sections' capability names and reported",
        "module": "synthesize",
        "old": '               "through", "across"}',
        "new": '               }',
        "tests": [
            "tests.test_pointers.HowManyClaimsWereChecked."
            "test_a_sentence_about_a_range_of_requirements_is_boilerplate_too",
        ],
    },
    {
        "what": "inventory.record — the hash of the text is not recorded",
        "why": "every locator in the inventory is a line number in the frozen "
               "text, and only the source's hash was kept, which a re-freeze "
               "leaves unchanged. Without the text's, synthesize.py cannot "
               "tell that the document beside the inventory is still the one "
               "those line numbers are in",
        "module": "inventory",
        "old": '            "text_sha256": doc.get("text_sha256"),\n',
        "new": '',
        "tests": [
            "tests.test_inventory.WhatIsWritten."
            "test_the_inventory_says_which_text_its_line_numbers_are_in",
            "tests.test_pointers.WhichDocument."
            "test_an_inventory_that_records_the_hash_of_the_text_is_held_to_it",
        ],
    },
    # Sections that did not answer. claim.py and undefined.py ask a model
    # about a document one section at a time and keep no entry for each, so a
    # call that failed came back as [], which is what a section with nothing
    # in it comes back as, and --limit cut the list without a word. Decided
    # with the author on 2026-10-04: counted and named beside the result,
    # carried in --out, and the exit status stays 0.
    {
        "what": "undefined.py — a section whose call failed has no terms",
        "why": "the defect as it shipped: `except LLMError: return []`. "
               "\"4/4 sections\" was printed with one of the four unanswered, "
               "and a term only that section would have raised was not in "
               "the list, with nothing to say why",
        "module": "undefined",
        "old": ('            return None\n'
                '        return [(item["term"].strip(), item["quote"].strip(), section)'),
        "new": ('            return []\n'
                '        return [(item["term"].strip(), item["quote"].strip(), section)'),
        "tests": [
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_section_whose_call_failed_is_counted_and_named",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_the_file_says_how_many_sections_its_terms_came_from",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_run_no_section_answered_is_not_a_document_with_no_such_term",
        ],
    },
    {
        "what": "undefined.py — --limit cuts the list and says nothing",
        "why": "\"d: 2 sections\", of a document that has four. inventory.py "
               "had the same and records the sections it stopped before; "
               "this tool reads the same sections and did not",
        "module": "undefined",
        "old": '    sections, beyond = inventory.limited(everything, args.doc, args.limit)',
        "new": '    sections, beyond = everything[:args.limit or None], []',
        "tests": [
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_run_told_to_stop_early_says_so_and_names_what_it_left",
        ],
    },
    {
        "what": "undefined.py — nothing is said beside the result",
        "why": "the count belongs with the number it qualifies. \"1 "
               "undefined\" reads as the document's, and the sections the "
               "terms did not come from were in a dict on the last line",
        "module": "undefined",
        "old": ('    for line in inventory.unanswered(read, NOT_READ, NOTHING_READ):\n'
                '        print(f"  {line}")'),
        "new": ('    for line in ():\n'
                '        print(f"  {line}")'),
        "tests": [
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_section_whose_call_failed_is_counted_and_named",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_run_told_to_stop_early_says_so_and_names_what_it_left",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_run_no_section_answered_is_not_a_document_with_no_such_term",
        ],
    },
    {
        "what": "undefined.py — the file does not say which sections its "
                "terms came from",
        "why": "the output scrolls away and the file is what bundle.py puts "
               "in front of a reviewer. A list of terms that does not say "
               "is read as the document's",
        "module": "undefined",
        "old": '            json.dump({"doc": args.doc, "sections": read, "terms": findings},',
        "new": '            json.dump({"doc": args.doc, "terms": findings},',
        "tests": [
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_the_file_says_how_many_sections_its_terms_came_from",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_bundle_made_from_a_partial_run_says_so_on_the_page",
        ],
    },
    {
        "what": "undefined.py — a score says nothing of the sections it is "
                "not a score of",
        "why": "\"miss\" against a register term is a term the tool did not "
               "raise, or one whose only section it never asked about. The "
               "score is of what was asked",
        "module": "undefined",
        "old": ('        if read["not_read"]:\n'
                '            # The score is of what was asked about, not of the document.'),
        "new": ('        if False:\n'
                '            # The score is of what was asked about, not of the document.'),
        "tests": [
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_score_says_how_many_sections_it_is_a_score_of",
        ],
    },
    {
        "what": "claim.extract — a section whose call failed asserts nothing",
        "why": "the defect as it shipped: `except LLMError: return []`. Two "
               "components each own the calibration baseline; with one of "
               "the two sections unanswered there is no pair to judge, and "
               "\"0 finding(s)\" was the whole report",
        "module": "claim",
        "old": '            return None             # not []: see the docstring',
        "new": '            return []',
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_section_whose_call_failed_is_counted_under_the_findings",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_the_index_says_how_many_sections_its_claims_came_from",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_section_one_pass_answered_is_read_and_listed_apart",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_no_section_answered_has_not_found_a_consistent_document",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_extract_returns_how_many_passes_answered_each_section",
        ],
    },
    {
        "what": "claim.py — --limit cuts the list and says nothing",
        "why": "the same line as undefined.py's, in the other tool that "
               "reads inventory.py's sections",
        "module": "claim",
        "old": '        sections, beyond = inventory.limited(everything, args.doc, args.limit)',
        "new": '        sections, beyond = everything[:args.limit or None], []',
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_told_to_stop_early_says_so_and_names_what_it_left",
        ],
    },
    {
        "what": "claim.py — nothing is said under the findings",
        "why": "claim.py already says, under \"N finding(s)\", how many "
               "pairs it could not judge. A section nobody answered for is "
               "further back: its claims were never in the index, so no "
               "pair was made from them and that count is silent too",
        "module": "claim",
        "old": ('        for line in inventory.unanswered(read, NOT_READ, NOTHING_READ):\n'
                '            print(line)'),
        "new": ('        for line in ():\n'
                '            print(line)'),
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_section_whose_call_failed_is_counted_under_the_findings",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_told_to_stop_early_says_so_and_names_what_it_left",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_section_one_pass_answered_is_read_and_listed_apart",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_no_section_answered_has_not_found_a_consistent_document",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_an_index_reused_brings_its_record_with_it",
        ],
    },
    {
        "what": "claim.py — the index does not say which sections its "
                "claims came from",
        "why": "--reuse and score-claims.py start from the index. One that "
               "does not say is scored, and reused, as the whole document",
        "module": "claim",
        "old": '            json.dump({"doc": args.doc, "sections": read, "claims": claims,',
        "new": '            json.dump({"doc": args.doc, "claims": claims,',
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_the_index_says_how_many_sections_its_claims_came_from",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_an_index_reused_brings_its_record_with_it",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_score_of_an_index_says_how_many_sections_the_index_is_of",
        ],
    },
    {
        "what": "claim.py — an index that does not say is taken to be of "
                "every section",
        "why": "every index written before 2026-10-04. Its claims may be all "
               "of the document's or half of them, and nothing in it tells "
               "the two apart: not known is not the same as all",
        "module": "claim",
        "old": ('        read = saved.get("sections") \\\n'
                '            if inventory.is_answers(saved.get("sections")) else None'),
        "new": '        read = inventory.answers([], [], [], args.runs, args.doc)',
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_one_that_does_not_say_is_not_taken_to_be_of_every_section",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_an_index_reused_brings_its_record_with_it",
        ],
    },
    {
        "what": "inventory.answers — a section no pass answered counts as "
                "answered",
        "why": "the count a run prints beside its result. \"4 of 4\" over a "
               "section nobody answered for is the line this replaced",
        "module": "inventory",
        "old": '            "answered": sum(1 for count in passes if count),',
        "new": '            "answered": len(passes),',
        "tests": [
            "tests.test_unanswered_sections.TheRecord."
            "test_a_section_no_pass_answered_is_not_read_and_is_named",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_the_file_says_how_many_sections_its_terms_came_from",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_no_section_answered_has_not_found_a_consistent_document",
        ],
    },
    {
        "what": "inventory.answers — the sections --limit stopped before are "
                "left out of the record",
        "why": "they are sections of the document and nobody asked about "
               "them. Left out, a run told to stop at two reports two "
               "sections answered and none unread",
        "module": "inventory",
        "old": ('    not_read += [{"heading": entry["heading"], "locator": entry["locator"],\n'
                '                  "why": entry["error"]} for entry in beyond]'),
        "new": '    not_read += []',
        "tests": [
            "tests.test_unanswered_sections.TheRecord."
            "test_so_is_one_the_run_was_told_to_stop_before",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_run_told_to_stop_early_says_so_and_names_what_it_left",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_told_to_stop_early_says_so_and_names_what_it_left",
        ],
    },
    {
        "what": "inventory.unanswered — a run no section answered is "
                "reported like one that missed a few",
        "why": "the no-result case. \"4 of 4 sections were NOT read\" above "
               "\"0 finding(s)\" still lets the zero stand as a result; said "
               "apart, it does not",
        "module": "inventory",
        "old": ('    elif not read["answered"]:\n'
                '        lines += [f"NOTHING WAS READ: 0 of'),
        "new": ('    elif False:\n'
                '        lines += [f"NOTHING WAS READ: 0 of'),
        "tests": [
            "tests.test_unanswered_sections.WhatIsSaidBesideTheResult."
            "test_a_run_no_section_answered_is_said_apart",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_run_no_section_answered_is_not_a_document_with_no_such_term",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_no_section_answered_has_not_found_a_consistent_document",
        ],
    },
    {
        "what": "inventory.asking — a run under --limit announces itself as "
                "the whole document",
        "why": "\"d: 2 sections\" was true of the list and read as true of "
               "the document",
        "module": "inventory",
        "old": ('    if beyond:\n'
                '        return (f"{doc}: asking about the first {len(sections)} of "'),
        "new": ('    if False:\n'
                '        return (f"{doc}: asking about the first {len(sections)} of "'),
        "tests": [
            "tests.test_unanswered_sections.WhatIsSaidBesideTheResult."
            "test_before_it_starts_a_run_says_how_many_sections_it_will_ask",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_run_told_to_stop_early_says_so_and_names_what_it_left",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_told_to_stop_early_says_so_and_names_what_it_left",
        ],
    },
    {
        "what": "inventory.is_answers — a record whose counts do not add up "
                "is taken at its word",
        "why": "4 sections, 2 answered, and a list of the unread that "
               "somebody emptied. The readers of these files print what the "
               "record says, so the record is checked before it is believed",
        "module": "inventory",
        "old": '        value["answered"] + len(value["not_read"]) == value["of"]',
        "new": '        True',
        "tests": [
            "tests.test_unanswered_sections.TheRecord.test_nor_is_one_whose_counts_do_not_add_up",
        ],
    },
    {
        "what": "bundle.undefined_terms — an older list of terms is not read",
        "why": "every terms file written before 2026-10-04 is a bare list. "
               "Changing what undefined.py writes must not empty the "
               "bundles made from those",
        "module": "bundle",
        "old": '    if isinstance(data, list):\n        terms, read = data, None',
        "new": '    if isinstance(data, list):\n        terms, read = [], None',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_a_file_from_before_it_was_an_object",
        ],
    },
    {
        "what": "bundle.terms_note — a terms file that does not say is "
                "taken for the whole document",
        "why": "the bundle is the page a reviewer reads. An older list of "
               "terms says nothing of the sections behind it, and silence "
               "on the page would say they were all read",
        "module": "bundle",
        "old": '        return UNSAID',
        "new": '        return ""',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_a_file_from_before_it_was_an_object",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_one_made_from_an_older_list_keeps_its_terms_and_says_so",
        ],
    },
    {
        "what": "bundle.terms_note — a partial run is bundled without a word",
        "why": "the record is in the file so that this page can say it",
        "module": "bundle",
        "old": ('    if read["not_read"]:\n'
                '        return (f"NOT THE WHOLE DOCUMENT: undefined terms were looked for in "'),
        "new": ('    if False:\n'
                '        return (f"NOT THE WHOLE DOCUMENT: undefined terms were looked for in "'),
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_the_note_counts_the_sections_and_is_empty_when_all_answered",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_bundle_made_from_a_partial_run_says_so_on_the_page",
        ],
    },
    {
        "what": "bundle.markdown — the note does not reach the page",
        "why": "collected, printed to the terminal of whoever ran the "
               "bundle, and absent from what the reviewer is sent",
        "module": "bundle",
        "old": '        out.append(f"- {note}")',
        "new": '        pass',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles.test_the_reviewers_page_carries_the_note",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_bundle_made_from_a_partial_run_says_so_on_the_page",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_one_made_from_an_older_list_keeps_its_terms_and_says_so",
        ],
    },
    {
        "what": "score-claims — a score says nothing of the sections the "
                "index is not of",
        "why": "MISSED is printed for a defect whose claim sits in a "
               "section the index was never answered on. The score is of "
               "the index, not of the document",
        "module": "score-claims",
        "old": ('    elif read["not_read"]:\n'
                '        said.append(f"the index was answered on {read[\'answered\']} of "'),
        "new": ('    elif False:\n'
                '        said.append(f"the index was answered on {read[\'answered\']} of "'),
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_score_of_an_index_says_how_many_sections_the_index_is_of",
        ],
    },
    {
        "what": "score-claims — an index that does not say is scored as the "
                "whole document",
        "why": "every index written before 2026-10-04, and the same rule as "
               "for --reuse: not known is not all",
        "module": "score-claims",
        "old": '        said.append(UNSAID)',
        "new": '        pass',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_says_so_of_an_index_that_does_not_record_it",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_of_one_whose_record_does_not_add_up",
        ],
    },
    # What a review of that change found, on the code and not on an account
    # of it (2026-10-04). The readers fell short of the tools: the sheet
    # without the note its page carried, a run no section answered bundled
    # as "no inputs found", a file that does not say labelled as partial,
    # and a score silent on the pairs nobody judged. And sixteen ways to
    # break the new code that no test noticed, each of which has one now.
    {
        "what": "inventory.unanswered — a text with no section in it has "
                "nothing said beside its result",
        "why": "\"nothing to read\" was the run's first line, and \"0 "
               "finding(s)\" stood bare at the end of it. A text cut into "
               "no section is likelier a parse that failed than a document "
               "with nothing to say, and its file read as answered in full",
        "module": "inventory",
        "old": ('    if not read["of"]:\n'
                '        lines += ["NOTHING WAS READ: no line of the document holds text.",'),
        "new": ('    if False:\n'
                '        lines += ["NOTHING WAS READ: no line of the document holds text.",'),
        "tests": [
            "tests.test_unanswered_sections.WhatIsSaidBesideTheResult."
            "test_a_text_with_no_section_in_it_is_said_apart_too",
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_a_text_with_no_section_in_it_is_not_one_with_no_such_term",
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_text_with_no_section_in_it_has_not_been_found_consistent",
        ],
    },
    {
        "what": "inventory.is_answers — a count below nothing is a count",
        "why": "-1 answered and five not read add up to the four sections "
               "there were. The one test of a negative count also broke the "
               "sum, so the sum caught it and this check was never asked",
        "module": "inventory",
        "old": '    counted = all(type(value.get(key)) is int and value[key] >= 0\n',
        "new": '    counted = all(type(value.get(key)) is int\n',
        "tests": [
            "tests.test_unanswered_sections.TheRecord."
            "test_nor_one_with_a_count_below_nothing",
        ],
    },
    {
        "what": "inventory.is_answers — a list of bare names is a list of "
                "sections not read",
        "why": "the readers print each entry's heading and locator. Two "
               "names in place of two entries still add up",
        "module": "inventory",
        "old": '                 and all(isinstance(entry, dict) for entry in value[key])\n',
        "new": '',
        "tests": [
            "tests.test_unanswered_sections.TheRecord."
            "test_nor_one_that_lists_names_where_it_should_list_entries",
        ],
    },
    {
        "what": "inventory.is_answers — True and False are counts",
        "why": "isinstance(True, int) holds, and {of: true, answered: true} "
               "adds up. No record answers() wrote counts that way",
        "module": "inventory",
        "old": 'type(value.get(key)) is int',
        "new": 'isinstance(value.get(key), int)',
        "tests": [
            "tests.test_unanswered_sections.TheRecord."
            "test_nor_one_that_counts_in_true_and_false",
        ],
    },
    {
        "what": "inventory.unanswered — a list of exactly `most` ends in "
                "\"and 0 more\"",
        "why": "the boundary of the cut. A line that says more follow, "
               "under a list that is whole",
        "module": "inventory",
        "old": '    if len(missing) > most:',
        "new": '    if len(missing) >= most:',
        "tests": [
            "tests.test_unanswered_sections.WhatIsSaidBesideTheResult."
            "test_a_list_of_exactly_that_many_is_given_whole",
        ],
    },
    {
        "what": "inventory.unanswered — the list of sections answered in "
                "fewer passes is not cut",
        "why": "only the list of sections not read was tested for its cut. "
               "A run where one pass fails throughout lists every section",
        "module": "inventory",
        "old": '                  for entry in short[:most]]',
        "new": '                  for entry in short]',
        "tests": [
            "tests.test_unanswered_sections.WhatIsSaidBesideTheResult."
            "test_the_list_of_those_answered_in_fewer_passes_is_cut_the_same",
        ],
    },
    {
        "what": "inventory.unanswered — and when cut it does not say how "
                "many more",
        "why": "a list that stops at twelve with nothing under it reads as "
               "twelve",
        "module": "inventory",
        "old": '        if len(short) > most:',
        "new": '        if False:',
        "tests": [
            "tests.test_unanswered_sections.WhatIsSaidBesideTheResult."
            "test_the_list_of_those_answered_in_fewer_passes_is_cut_the_same",
        ],
    },
    {
        "what": "claim.py — a run of no pass is run",
        "why": "`--runs 0` asked about no section and printed \"0 "
               "finding(s)\". With sections counted it named every one as "
               "\"extraction failed\", of calls that were never made, and "
               "`--runs -1` wrote a record no reader would take",
        "module": "claim",
        "old": '    if args.runs < 1:',
        "new": '    if False:',
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_a_run_of_no_pass_is_refused",
        ],
    },
    {
        "what": "claim.py — and a run of one pass is refused with it",
        "why": "the other side of that line. One pass is a run, and every "
               "test took the default of two",
        "module": "claim",
        "old": '    if args.runs < 1:',
        "new": '    if args.runs < 2:',
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_and_a_run_of_one_pass_is_not",
        ],
    },
    {
        "what": "claim.extract — the count of sections that did not answer "
                "carries into the next pass",
        "why": "a section that failed in pass 1 and answered in pass 2 was "
               "counted out of pass 2's line. The test had its section fail "
               "in the last pass, where there is nothing to carry",
        "module": "claim",
        "old": '        silent = 0\n',
        "new": '        silent = silent if run else 0\n',
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_the_count_of_answers_starts_again_with_each_pass",
        ],
    },
    {
        "what": "claim.py — the sections not read are named after the "
                "findings",
        "why": "said with the count, as the unjudged pairs are. Under a "
               "long report it is the last thing on the screen, and the "
               "test read everything after \"finding(s)\" as one piece",
        "module": "claim",
        "old": ('        for line in inventory.unanswered(read, NOT_READ, NOTHING_READ):\n'
                '            print(line)\n'
                '    print("=" * 74)'),
        "new": ('        pass\n'
                '    print("=" * 74)\n'
                '    for line in (inventory.unanswered(read, NOT_READ, NOTHING_READ)\n'
                '                 if read else ()):\n'
                '        print(line)'),
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_and_it_is_said_between_the_count_and_the_findings",
        ],
    },
    {
        "what": "claim.py — --reuse believes whatever stands under "
                "\"sections\"",
        "why": "only an index with no record was tested. One whose list of "
               "the unread was emptied says three of four answered and "
               "none is missing, and prints nothing",
        "module": "claim",
        "old": ('        read = saved.get("sections") \\\n'
                '            if inventory.is_answers(saved.get("sections")) else None'),
        "new": '        read = saved.get("sections")',
        "tests": [
            "tests.test_unanswered_sections.ClaimIndex."
            "test_nor_is_one_whose_record_does_not_add_up",
        ],
    },
    {
        "what": "undefined.py — a partial run is told what a run that read "
                "nothing is told",
        "why": "the consequence line is this tool's own: terms are missed, "
               "none is wrongly listed. No test asserted its words",
        "module": "undefined",
        "old": '    for line in inventory.unanswered(read, NOT_READ, NOTHING_READ):',
        "new": '    for line in inventory.unanswered(read, NOTHING_READ, NOTHING_READ):',
        "tests": [
            "tests.test_unanswered_sections.UndefinedTerms."
            "test_and_what_that_costs_the_list_is_said_with_it",
        ],
    },
    {
        "what": "bundle.py — the note is on the page and not in the sheet",
        "why": "the defect as the review found it. Two outputs of one pass: "
               "the page said 3 of 4 sections, and the sheet, built from the "
               "findings alone, listed the terms as the document's",
        "module": "bundle",
        "old": '    write_xlsx(xlsx_path, args.doc[:31], HEADERS, note_rows(notes) + rows)',
        "new": '    write_xlsx(xlsx_path, args.doc[:31], HEADERS, rows)',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_in_the_sheet_above_the_terms",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_one_made_from_an_older_list_keeps_its_terms_and_says_so",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_beside_other_findings_the_bundle_says_so_everywhere",
        ],
    },
    {
        "what": "bundle.py — the note is the last row of the sheet",
        "why": "a reviewer who stops halfway has read the terms and not "
               "the row that qualifies them",
        "module": "bundle",
        "old": '    write_xlsx(xlsx_path, args.doc[:31], HEADERS, note_rows(notes) + rows)',
        "new": '    write_xlsx(xlsx_path, args.doc[:31], HEADERS, rows + note_rows(notes))',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_in_the_sheet_above_the_terms",
        ],
    },
    {
        "what": "bundle.UNSAID — a file that does not say is called partial",
        "why": "\"NOT THE WHOLE DOCUMENT\" stood over every terms file "
               "written before 2026-10-04, the ones from complete runs "
               "among them. Not known is not all, and it is not some either",
        "module": "bundle",
        "old": 'UNSAID = ("NOT KNOWN TO BE THE WHOLE DOCUMENT: the undefined terms come from "',
        "new": 'UNSAID = ("NOT THE WHOLE DOCUMENT: the undefined terms come from "',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_file_that_does_not_say_is_not_called_partial",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_one_made_from_an_older_list_keeps_its_terms_and_says_so",
        ],
    },
    {
        "what": "bundle.terms_note — a run no section answered is noted "
                "like one that missed a few",
        "why": "the no-result case, in the reader. \"0 of the document's 4 "
               "sections\" under the words for a partial run lets an empty "
               "list of terms stand as a result",
        "module": "bundle",
        "old": ('    if not read["answered"]:\n'
                '        return (f"NOTHING WAS READ: undefined terms'),
        "new": ('    if False:\n'
                '        return (f"NOTHING WAS READ: undefined terms'),
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_run_no_section_answered_gets_words_of_its_own",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_run_no_section_answered_is_not_a_bundle_with_no_input",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_beside_other_findings_the_bundle_says_so_everywhere",
        ],
    },
    {
        "what": "bundle.py — a file that holds no finding was not found",
        "why": "the defect as the review found it: \"no inputs found — "
               "check --undefined\", of a terms file that was found and "
               "says no section answered. The record was dropped",
        "module": "bundle",
        "old": '    if not named:\n        return "no inputs found',
        "new": '    if True:\n        return "no inputs found',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_run_no_section_answered_is_not_a_bundle_with_no_input",
        ],
    },
    {
        "what": "bundle.undefined_terms — a file with no list of terms is a "
                "file with no terms",
        "why": "as this change first had it. A claim index passed as "
               "--undefined, whose record says every section answered, "
               "gave a bundle with no undefined term and not a word",
        "module": "bundle",
        "old": ('        raise ValueError(\'not a terms file: it is neither a list of terms \'\n'
                '                         \'nor an object with one under "terms"\')'),
        "new": '        terms = []',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_file_with_no_list_of_terms_is_not_a_terms_file",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_file_that_is_not_a_terms_file_stops_the_bundle",
        ],
    },
    {
        "what": "bundle.collect — a file that cannot be read as a terms "
                "file is left out of the bundle",
        "why": "the bundle is then made from the other inputs and says "
               "nothing of the terms it was given and could not read: a "
               "claim index passed by mistake, or a terms file cut off "
               "mid-write, which undefined.py can leave behind",
        "module": "bundle",
        "old": '                sys.exit(f"{args.undefined}: {error}")',
        "new": '                terms, read = [], None',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_file_that_is_not_a_terms_file_stops_the_bundle",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_nor_does_one_cut_off_mid_write",
        ],
    },
    {
        "what": "score-claims — a record that does not add up is scored as "
                "it stands",
        "why": "only an index with no record was tested. The same check as "
               "--reuse makes, in the other reader of the index",
        "module": "score-claims",
        "old": '    if not inventory.is_answers(read):\n        said.append(UNSAID)',
        "new": '    if read is None:\n        said.append(UNSAID)',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_of_one_whose_record_does_not_add_up",
        ],
    },
    {
        "what": "score-claims — an index no section answered is scored "
                "like one that missed a few",
        "why": "every MISSED under it is a defect nobody looked for. Said "
               "in the words for a partial index, the score reads as one",
        "module": "score-claims",
        "old": ('    elif not read["answered"]:\n'
                '        said.append(f"NOTHING WAS READ: the index was answered on 0 of "'),
        "new": ('    elif False:\n'
                '        said.append(f"NOTHING WAS READ: the index was answered on 0 of "'),
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_of_one_no_section_answered",
        ],
    },
    {
        "what": "score-claims — a score says nothing of the sections a "
                "pass failed on",
        "why": "the index lists them and the score did not. A claim only "
               "the pass that failed would have returned is not in the "
               "index, and the defect it belongs to is scored MISSED",
        "module": "score-claims",
        "old": '    if inventory.is_answers(read) and read["short"]:',
        "new": '    if False:',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_of_a_section_a_pass_failed_on",
        ],
    },
    {
        "what": "score-claims — a score says nothing of the pairs nobody "
                "judged",
        "why": "the defect as the review found it, and older than this "
               "change: six candidate pairs, every judging call failed, and "
               "the score read \"recall 0/1\" and MISSED over the pair that "
               "is the defect. A failure to measure is not a miss",
        "module": "score-claims",
        "old": ('    elif pairs:\n'
                '        said.append(f"{len(pairs)} candidate pair(s) could not be judged, "'),
        "new": ('    elif False:\n'
                '        said.append(f"{len(pairs)} candidate pair(s) could not be judged, "'),
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_and_of_the_pairs_nobody_judged",
        ],
    },
    {
        "what": "score-claims — an index that does not say which pairs "
                "were judged is taken to have judged them all",
        "why": "every index written before claim.py kept its unjudged "
               "pairs dropped them without a word. Not known is not none",
        "module": "score-claims",
        "old": '        said.append(UNJUDGED_UNSAID)',
        "new": '        pass',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_an_index_that_does_not_say_which_pairs_were_judged",
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_nor_one_whose_record_of_them_cannot_be_read",
        ],
    },
    {
        "what": "score-claims — a MISSED defect whose pair was never "
                "judged is not marked",
        "why": "\"may be one of them\" beside the score leaves the reader "
               "to work out which. The index holds the pairs, so the score "
               "can say which defect was not measured",
        "module": "score-claims",
        "old": '        if any(matches(pair, entry) for pair in not_judged):',
        "new": '        if False:',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_missed_defect_that_an_unjudged_pair_matches_says_so",
        ],
    },
    {
        "what": "score-claims — every MISSED defect is marked once any "
                "pair went unjudged",
        "why": "the mark is a claim about one defect. A defect no pair was "
               "ever made for is a miss, and marking it excuses it",
        "module": "score-claims",
        "old": '        if any(matches(pair, entry) for pair in not_judged):',
        "new": '        if not_judged:',
        "tests": [
            "tests.test_unanswered_sections.WhatReadsTheFiles."
            "test_a_missed_defect_that_an_unjudged_pair_matches_says_so",
        ],
    },
    {
        "what": "synthesize.moved — after an entry that says nothing, a "
                "blank line ties a heading with nothing in it",
        "why": "the one chunk in doubt is then the one that ties the text, "
               "and an inventory no chunk of which starts on a heading is "
               "accepted",
        "module": "synthesize",
        "old": '            if first.lstrip("#").strip() == heading and (heading or first):',
        "new": '            if first.lstrip("#").strip() == heading and (heading or first or blind):',
        "tests": [
            "tests.test_pointers.WhichDocument."
            "test_nor_after_an_entry_that_says_nothing",
        ],
    },
    # An inventory that is not as inventory.py writes one. synthesize.py took
    # the shape of its input on trust. It is held to it now at three levels:
    # the file, an entry, and an item of one of an entry's lists
    # (tests/test_malformed_inventory.py says what was measured).
    {
        "what": 'synthesize — a file that is not an inventory is reported on',
        "why": 'text that is not JSON, a list of sections with no mapping '
               'round it, a file that is not there: each was a traceback. '
               'Refused with one line and exit status 1, a run cannot be taken '
               'for one that looked and found nothing',
        "module": "synthesize",
        "old": '    if refused:\n'
               '        # Not a report',
        "new": '    if False:\n'
               '        # Not a report',
        "tests": [
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_a_file_that_is_not_there",
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_text_that_is_not_json",
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_json_that_is_not_a_mapping",
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_a_mapping_with_no_sections",
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_sections_that_are_not_a_list",
        ],
    },
    {
        "what": 'synthesize — a file that is refused exits 0',
        "why": 'the refusal is one line on stderr. A script that runs this and '
               'reads its exit status would have, at 0, a run that looked and '
               'found nothing',
        "module": "synthesize",
        "old": '        print(f"NOT RUN: {args.inventory} {refused}.", file=sys.stderr)\n'
               '        return 1',
        "new": '        print(f"NOT RUN: {args.inventory} {refused}.", file=sys.stderr)\n'
               '        return 0',
        "tests": [
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_a_file_that_is_not_there",
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_text_that_is_not_json",
        ],
    },
    {
        "what": 'synthesize.load_inventory — an inventory of no sections is '
                'reported on',
        "why": '`"sections": []` printed "0 of 0 sections read" and no '
               'finding, and exited 0: what a document with no defects would '
               'get, over nothing read',
        "module": "synthesize",
        "old": '    if not data["sections"]:',
        "new": '    if False:',
        "tests": [
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_a_list_of_sections_with_nothing_in_it",
        ],
    },
    {
        "what": 'synthesize.load_inventory — a byte-order mark makes the file '
                'not JSON',
        "why": 'an editor puts one in front of a file it saves. The inventory '
               'behind it is as inventory.py wrote it, and the parser will not '
               'have it',
        "module": "synthesize",
        "old": '        with open(path, encoding="utf-8-sig") as handle:',
        "new": '        with open(path, encoding="utf-8") as handle:',
        "tests": [
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_a_byte_order_mark_in_front_of_the_file_is_not_part_of_it",
        ],
    },
    {
        "what": 'synthesize.load_inventory — a file nested too deeply to parse '
                'is a traceback',
        "why": 'the parser gives up with RecursionError, which is not the '
               'error it raises for text that is not JSON, and at a depth that '
               'differs from one Python to the next',
        "module": "synthesize",
        "old": '    except RecursionError:',
        "new": '    except ZeroDivisionError:',
        "tests": [
            "tests.test_malformed_inventory.AFileThatIsNotAnInventory."
            "test_a_file_nested_too_deeply_for_python_to_parse",
        ],
    },
    {
        "what": 'synthesize.passes_asked — runs that are text are compared with '
                "a section's passes",
        "why": '`"runs": "three"` raised where a section\'s passes were '
               'compared with it, and every finding went with it. It decides '
               'one list in the report and no finding',
        "module": "synthesize",
        "old": '    return asked if isinstance(asked, int) and not '
               'isinstance(asked, bool) \\',
        "new": '    return asked if True and not isinstance(asked, bool) \\',
        "tests": [
            "tests.test_malformed_inventory.HowManyPassesWereAskedFor."
            "test_runs_that_are_not_a_whole_number_of_one_or_more",
        ],
    },
    {
        "what": 'synthesize — a run that read no section goes on to list its '
                'candidates',
        "why": 'with every section failed at extraction the report named them, '
               'listed no candidate and exited 0, which is what a document '
               'with no defect gets',
        "module": "synthesize",
        "old": '    if not sections:\n'
               '        # Each entry is named above',
        "new": '    if False:\n'
               '        # Each entry is named above',
        "tests": [
            "tests.test_malformed_inventory.NoSectionThatWasRead."
            "test_it_is_said_and_the_run_is_not_a_result",
        ],
    },
    {
        "what": 'synthesize — a run that read no section exits 0',
        "why": 'it says on stderr that nothing was read. A script that runs '
               'this and reads its exit status would have, at 0, a document in '
               'which nothing was found',
        "module": "synthesize",
        "old": '              f"{args.inventory}.", file=sys.stderr)\n'
               '        return 1',
        "new": '              f"{args.inventory}.", file=sys.stderr)\n'
               '        return 0',
        "tests": [
            "tests.test_malformed_inventory.NoSectionThatWasRead."
            "test_it_is_said_and_the_run_is_not_a_result",
        ],
    },
    {
        "what": 'dossier_cli.cmd_inventory — what synthesize.py exits with is '
                'thrown away',
        "why": '`dossier inventory` runs inventory.py and then synthesize.py, '
               "and returned the first one's status whatever the second did: "
               'an inventory that synthesize.py refuses, or reads no section '
               'of, came back as 0',
        "module": "dossier_cli",
        "old": '        code = run("synthesize.py", "--project", args.project,\n'
               '                   "--inventory", out)',
        "new": '        run("synthesize.py", "--project", args.project,\n'
               '            "--inventory", out)',
        "tests": [
            "tests.test_malformed_inventory.NoSectionThatWasRead."
            "test_and_the_status_reaches_whoever_ran_dossier_inventory",
        ],
    },
    {
        "what": 'synthesize.read_inventory — an entry that is not a section is '
                'read all the same',
        "why": 'a section given as a number raised, and one whose capabilities '
               'are null was read as a section that provides nothing: a claim '
               'that it holds something was asserted as untrue',
        "module": "synthesize",
        "old": '            why = why and f"not as inventory.py writes it: '
               '{why}"',
        "new": '            why = ""',
        "tests": [
            "tests.test_malformed_inventory.AnEntryThatIsNotASection."
            "test_a_number_where_a_section_should_be",
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_capabilities_of_null_are_not_a_section_that_provides_nothing",
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_each_of_the_six_lists_has_to_be_one",
        ],
    },
    {
        "what": 'synthesize.read_inventory — an entry with an error and no '
                'reason is one that was read',
        "why": 'taken as it stands, the reason of `"error": ""` beside '
               '`"skipped": true` is empty, and an empty reason reads as none: '
               'the entry goes on to be used as a section, with whatever '
               'lists it has',
        "module": "synthesize",
        "old": '                why = one_line(writable(said)) \\\n'
               '                    if isinstance(said, str) and said.strip() \\\n'
               '                    else "skipped, and the inventory does not say why"',
        "new": '                why = said',
        "tests": [
            "tests.test_malformed_inventory.AnEntryThatIsNotASection."
            "test_an_entry_with_an_error_was_not_read_whatever_the_error_says",
        ],
    },
    {
        "what": 'synthesize.read_inventory — an entry marked skipped, with no '
                'error, is read',
        "why": 'inventory.py marks an entry skipped only beside an error. One '
               'marked so with no error and its lists empty was read as a '
               'section that holds nothing, and the claim that it holds '
               'something was asserted as untrue',
        "module": "synthesize",
        "old": '        if isinstance(entry, dict) and ("error" in entry\n'
               '                                        or entry.get("skipped")):',
        "new": '        if isinstance(entry, dict) and "error" in entry:',
        "tests": [
            "tests.test_malformed_inventory.AnEntryThatIsNotASection."
            "test_an_entry_marked_skipped_was_not_read_error_or_no_error",
        ],
    },
    {
        "what": 'synthesize.unreadable — a heading may run over two lines',
        "why": 'inventory.py takes a heading from one line of the document. '
               'One with a line break in it ends the line of the report it is '
               'printed on and begins another, which reads as a line this '
               'program wrote',
        "module": "synthesize",
        "old": '        if "".join(entry[key].splitlines()) != entry[key]:',
        "new": '        if False:',
        "tests": [
            "tests.test_malformed_inventory.WhatAnEntrySaysOfItself."
            "test_a_heading_or_a_locator_with_a_line_break_in_it",
        ],
    },
    {
        "what": 'synthesize.unreadable — one count of passes may be given '
                'without the other',
        "why": 'inventory.py has written both counts or neither since it began '
               'to count. An entry with one of them was not written by it, '
               'and how its section was read cannot be told from the one that '
               'is left',
        "module": "synthesize",
        "old": '    if len(counts) == 1:',
        "new": '    if False:',
        "tests": [
            "tests.test_malformed_inventory.WhatAnEntrySaysOfItself."
            "test_one_count_without_the_other",
        ],
    },
    {
        "what": 'synthesize.unreadable — a section that no pass answered is '
                'read',
        "why": 'inventory.py writes that one as an error. `"passes": 0` beside '
               'six empty lists was a section that holds nothing: what '
               'another section defers to it had no owner, what it would '
               'consume had no consumer, and both were asserted',
        "module": "synthesize",
        "old": '        if passes < 1:',
        "new": '        if False:',
        "tests": [
            "tests.test_malformed_inventory.WhatAnEntrySaysOfItself."
            "test_a_section_that_no_pass_answered",
        ],
    },
    {
        "what": 'synthesize.unreadable — the counts of an entry need not agree',
        "why": '`"full_passes": 99` beside `"passes": 1`, in an entry marked '
               'degraded, was a section read in full where the fallback alone '
               'had read it: what it does not consume was asserted',
        "module": "synthesize",
        "old": '        if full != passes - fallback:',
        "new": '        if False:',
        "tests": [
            "tests.test_malformed_inventory.WhatAnEntrySaysOfItself."
            "test_passes_as_inventory_py_counts_them",
        ],
    },
    {
        "what": 'synthesize.unreadable — a list need not be a list',
        "why": '`"consumes": {}` and `"capabilities": null` were each read as '
               'a section with nothing in that list: an orphan and an untrue '
               'claim, asserted',
        "module": "synthesize",
        "old": '    for key, _ in READS:\n'
               '        if not isinstance(entry.get(key), list):\n',
        "new": '    for key, _ in READS:\n'
               '        if not isinstance(entry.get(key, []), (list, dict, type(None))):\n',
        "tests": [
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_each_of_the_six_lists_has_to_be_one",
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_capabilities_of_null_are_not_a_section_that_provides_nothing",
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_consumes_of_an_empty_mapping_are_not_a_section_that_consumes_nothing",
        ],
    },
    {
        "what": 'synthesize.unreadable — a list that is missing is an empty '
                'list',
        "why": 'an entry with no `consumes` was a section that consumes '
               'nothing. inventory.py gives every entry all eight lists, and '
               'nothing says this one was asked',
        "module": "synthesize",
        "old": '    for key, _ in READS:\n'
               '        if not isinstance(entry.get(key), list):\n',
        "new": '    for key, _ in READS:\n'
               '        if not isinstance(entry.get(key, []), list):\n',
        "tests": [
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_and_has_to_be_there",
        ],
    },
    {
        "what": 'synthesize.unreadable — identifiers and deferred need not be '
                'lists',
        "why": 'identifiers and deferred are asked whether they hold anything, '
               'by fully_read(). In an entry the fallback read, from before '
               'its passes were counted, `"identifiers": 7` was something '
               'held: the section counted as read in full, and what it does '
               'not consume was asserted',
        "module": "synthesize",
        "old": '        if key in entry and not isinstance(entry[key], list):\n'
               '            return f"its \'{key}\' is {worded(entry[key])}, not a list"\n'
               '    return ""',
        "new": '        if False:\n'
               '            return f"its \'{key}\' is {worded(entry[key])}, not a list"\n'
               '    return ""',
        "tests": [
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_the_other_two_lists_are_lists_where_an_entry_has_them",
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_a_number_there_is_no_sign_that_a_full_pass_read_the_section",
        ],
    },
    {
        "what": 'synthesize.read_items — an item that cannot be read is used '
                'all the same',
        "why": 'an authority entry whose owner is a list of two raised where '
               'owners are compared, and every finding went with it. '
               "inventory.py's validator lets one through",
        "module": "synthesize",
        "old": '            why = wrong(item)\n'
               '            if why:',
        "new": '            why = wrong(item)\n'
               '            if False:',
        "tests": [
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_an_authority_entry",
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_an_input_or_an_output_that_is_not_text",
        ],
    },
    {
        "what": 'synthesize.read_items — an item that cannot be read is dropped '
                'without a word',
        "why": 'a deferral that does not say what is deferred, a number among '
               "a section's inputs: each is passed over, and nothing in the "
               'report says that the section holds something that was not read',
        "module": "synthesize",
        "old": '                aside.append({"section": section, "list": key,\n'
               '                              "why": f"entry {position} of its \'{key}\' {why}"})',
        "new": '                pass',
        "tests": [
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_a_deferral",
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_it_is_named_by_its_section_and_where_it_stands_in_the_list",
        ],
    },
    {
        "what": 'synthesize.empty_place — a null in a list is an item',
        "why": 'a null holds nothing that could be anything. Set aside as an '
               "input that was not read, one null among one section's inputs "
               'makes a question of every orphan in the document, and beside '
               'each the report says that a section holds an input that was '
               'not read, where no input was',
        "module": "synthesize",
        "old": '    return item is None or (isinstance(item, str) and not '
               'item.strip())',
        "new": '    return isinstance(item, str) and not item.strip()',
        "tests": [
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_a_null_is_an_empty_place_in_a_list_and_not_an_item",
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_an_identifier_there_is_one_and_an_empty_place_is_not",
        ],
    },
    {
        "what": 'synthesize.empty_place — text with nothing in it is an item',
        "why": '`""` among a section\'s inputs says what a null says. Read as '
               'an input it is counted among them, and among the identifiers '
               'of a section the fallback read it is taken for the sign that a '
               'full pass read the section too',
        "module": "synthesize",
        "old": '    return item is None or (isinstance(item, str) and not '
               'item.strip())',
        "new": '    return item is None',
        "tests": [
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_a_null_is_an_empty_place_in_a_list_and_not_an_item",
            "tests.test_malformed_inventory.AFieldThatIsNotAList."
            "test_an_identifier_there_is_one_and_an_empty_place_is_not",
        ],
    },
    {
        "what": 'synthesize.not_texts — text with nothing in it says what an '
                'item is asked',
        "why": 'the fallback\'s validator lets through a capability named "" '
               'with what the section holds in its quote. Read as a capability '
               'it matches nothing, and a claim that the section holds it was '
               'asserted as untrue',
        "module": "synthesize",
        "old": '        if key in needed and not held.strip():',
        "new": '        if False:',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_a_capability_named_with_nothing_is_not_one_that_was_read",
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_a_capability",
        ],
    },
    {
        "what": 'synthesize.not_texts — an empty list or mapping is something, '
                'under a key that may be absent',
        "why": 'a deferral to {} still says what is deferred. Set aside, the '
               'finding that nobody owns it is not made, where it was made '
               'before this check was written',
        "module": "synthesize",
        "old": '        if key in optional and (held is None or held == [] or '
               'held == {}):',
        "new": '        if key in optional and held is None:',
        "tests": [
            "tests.test_malformed_inventory.WhatIsTakenOfAnItem."
            "test_nothing_under_a_key_that_may_be_absent",
        ],
    },
    {
        "what": 'synthesize.READS — an input or an output given by its name '
                'cannot be read',
        "why": '`{"name": ...}` among a section\'s inputs is how a capability '
               'is given, and it says what the input is. Set aside, it puts '
               'every orphan of the document in doubt over an item that can be '
               'read',
        "module": "synthesize",
        "old": '         ("consumes", not_named), ("produces", not_named))',
        "new": '         ("consumes", lambda item: "" if isinstance(item, str) else "is not text"),\n'
               '         ("produces", lambda item: "" if isinstance(item, str) else "is not text"))',
        "tests": [
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_an_input_or_an_output_given_by_its_name_is_read_by_it",
        ],
    },
    {
        "what": 'synthesize.taken — an input or an output given by its name is '
                'taken as the mapping it came in',
        "why": 'it was read by its name where --authority-as-dataflow folds '
               'data flow into ownership, and nowhere else. Without the flag '
               'it was no consumer, and what it names was an orphan, asserted',
        "module": "synthesize",
        "old": '    if key in ("consumes", "produces"):\n'
               '        return writable(item["name"])',
        "new": '    if False:\n'
               '        return writable(item["name"])',
        "tests": [
            "tests.test_malformed_inventory.AnItemThatCannotBeRead."
            "test_an_input_or_an_output_given_by_its_name_is_read_by_it",
        ],
    },
    {
        "what": 'synthesize.not_named — a name given as text alone cannot be '
                'read',
        "why": 'an input or an output is text by the schema, and the '
               "fallback's validator lets a capability through as text. Set "
               'aside, a capability leaves a claim that points to its section '
               'a question, where the section holds what the claim says',
        "module": "synthesize",
        "old": '    if isinstance(item, str):\n'
               '        return ""\n'
               '    if not isinstance(item, dict):',
        "new": '    if False:\n'
               '        return ""\n'
               '    if not isinstance(item, dict):',
        "tests": [
            "tests.test_malformed_inventory.WhatIsTakenOfAnItem."
            "test_a_capability_that_is_its_name_and_nothing_else",
        ],
    },
    {
        "what": 'synthesize.not_claim — a pointer that is a list of places '
                'cannot be read',
        "why": 'a pointer given as a list of two places was read as naming '
               'both before this check was written. Set aside, the claim is '
               'one D3 does not check',
        "module": "synthesize",
        "old": '    if isinstance(place, list) and all(isinstance(each, str) '
               'for each in place):',
        "new": '    if False:',
        "tests": [
            "tests.test_malformed_inventory.WhatIsTakenOfAnItem."
            "test_a_pointer_that_is_a_list_of_places_or_nothing",
            "tests.test_malformed_inventory.WhatIsTakenOfAnItem."
            "test_the_places_of_a_list_are_read_as_one_pointer",
        ],
    },
    {
        "what": 'synthesize.taken — every key of an item is taken',
        "why": '`owner` on an evidence claim is not what a claim is asked for, '
               'so nothing checks it. Taken with the claim, text under it is '
               'printed as who the finding names, where the place the claim '
               'points to belongs',
        "module": "synthesize",
        "old": '    for name in TAKEN[key]:',
        "new": '    for name in item:',
        "tests": [
            "tests.test_malformed_inventory.WhatIsTakenOfAnItem."
            "test_a_key_its_list_is_not_asked_for_is_not_carried",
            "tests.test_malformed_inventory.WhatIsTakenOfAnItem."
            "test_and_does_not_stand_in_for_one_that_is",
        ],
    },
    {
        "what": 'synthesize.taken — a quote that is not text is taken',
        "why": 'nothing is decided on a quote, so one that is a list of two is '
               'no reason to set the item aside. It is not text to be shown '
               'either, and taken as if it were it raises where it is made '
               'ready to be written',
        "module": "synthesize",
        "old": '    if isinstance(item.get("quote"), str):',
        "new": '    if "quote" in item:',
        "tests": [
            "tests.test_malformed_inventory.WhatIsTakenOfAnItem."
            "test_a_quote_that_is_not_text_is_not_shown",
        ],
    },
    {
        "what": 'synthesize — an input that was set aside does not put an '
                'orphan in doubt',
        "why": '`{"label": "calibrated gauge readings"}` among a section\'s '
               'inputs does not say what the input is where this program reads '
               'it. It was no consumer, and the output it may be was an '
               'orphan, asserted',
        "module": "synthesize",
        "old": '                    open_question(holding("consumes"),',
        "new": '                    open_question(holding(),',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_an_input_could_be_what_consumes_an_output",
        ],
    },
    {
        "what": 'synthesize — an output that was set aside puts an orphan in '
                'doubt',
        "why": "what a section produces cannot be what consumes another's "
               'output. The first version of this check refused the whole '
               'section over one item, and every absence in the document '
               'became a question',
        "module": "synthesize",
        "old": '                    open_question(holding("consumes"),',
        "new": '                    open_question(holding("consumes", '
               '"produces"),',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_each_list_answers_for_its_own_absence_and_no_other",
        ],
    },
    {
        "what": 'synthesize — an authority entry that was set aside does not '
                'put an owner in doubt',
        "why": "the fallback's validator lets an authority entry through as a "
               'sentence. It names no owner this program can read, and what '
               'another section defers to that one was asserted as owned by '
               'nobody',
        "module": "synthesize",
        "old": '        aside_owners = holding("authority", *(',
        "new": '        aside_owners = holding(*(',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_an_authority_entry_could_be_the_owner",
        ],
    },
    {
        "what": 'synthesize — under --authority-as-dataflow, an input or an '
                'output that was set aside does not put an owner in doubt',
        "why": 'that flag takes ownership from what a section produces and '
               'consumes as well, so an item of either that was not read could '
               'be the owner',
        "module": "synthesize",
        "old": '            ("produces", "consumes") if '
               'args.authority_as_dataflow else ()))',
        "new": '            ()))',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_under_authority_as_dataflow_an_input_or_an_output_could_own",
        ],
    },
    {
        "what": 'synthesize — without that flag, an input or an output that was '
                'set aside puts an owner in doubt',
        "why": 'an input is not an owner where ownership is read from '
               'authority alone, and a finding it cannot answer is not a '
               'question because of it',
        "module": "synthesize",
        "old": '            ("produces", "consumes") if '
               'args.authority_as_dataflow else ()))',
        "new": '            ("produces", "consumes")))',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_under_authority_as_dataflow_an_input_or_an_output_could_own",
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_each_list_answers_for_its_own_absence_and_no_other",
        ],
    },
    {
        "what": 'synthesize — a capability that was set aside does not put what '
                'a claim points to in doubt',
        "why": 'a capability named with a number is not one a claim can be '
               'matched against. The claim that its section holds it was '
               'asserted as untrue',
        "module": "synthesize",
        "old": '                                            '
               'holding("capabilities"))',
        "new": '                                            holding())',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_a_capability_could_be_what_a_claim_points_to",
        ],
    },
    {
        "what": 'synthesize — any item that was set aside in the place a claim '
                'points to puts the claim in doubt',
        "why": 'what a section consumes is not what it holds. A claim that the '
               'section holds something is answered by its capabilities, and '
               'by no other list',
        "module": "synthesize",
        "old": '                                            '
               'holding("capabilities"))',
        "new": '                                            holding(*(key for '
               'key, _ in READS)))',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_nor_an_item_of_another_list_in_the_section_it_does_point_to",
        ],
    },
    {
        "what": 'synthesize.self_claims — a capability that was set aside '
                'anywhere puts every claim in doubt',
        "why": 'a claim is about the place it points to. A capability that was '
               'not read in another section cannot be what that place holds',
        "module": "synthesize",
        "old": '                [section for section in hits\n'
               '                 if any(section is each for each in blurred)],',
        "new": '                list(blurred),',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_but_not_one_in_a_section_the_claim_does_not_point_to",
        ],
    },
    {
        "what": 'synthesize.self_claims — a capability that was set aside is '
                'the reason given before a section that was not read',
        "why": 'both stand between the claim and saying it is untrue, and one '
               'is given. The section is the larger doubt: it could hold the '
               'place itself',
        "module": "synthesize",
        "old": '                unread, "a section that was not read could hold it, as part "\n'
               '                        "of the place the pointer names") or open_question(\n'
               '                [section for section in hits\n'
               '                 if any(section is each for each in blurred)],\n'
               '                "the place it points to holds a capability that was not "\n'
               '                "read") or doubt))',
        "new": '                [section for section in hits\n'
               '                 if any(section is each for each in blurred)],\n'
               '                "the place it points to holds a capability that was not "\n'
               '                "read") or open_question(\n'
               '                unread, "a section that was not read could hold it, as part "\n'
               '                        "of the place the pointer names") or doubt))',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_a_section_that_was_not_read_is_the_reason_given_first",
        ],
    },
    {
        "what": 'synthesize.self_claims — a doubt about the pointer is the '
                'reason given before a capability that was set aside',
        "why": 'the capability is what the place could hold, and the line '
               "names the section to read again. The pointer's own doubt is "
               'the one given where the place was read whole',
        "module": "synthesize",
        "old": '                        "of the place the pointer names") or open_question(\n'
               '                [section for section in hits\n'
               '                 if any(section is each for each in blurred)],\n'
               '                "the place it points to holds a capability that was not "\n'
               '                "read") or doubt))',
        "new": '                        "of the place the pointer names") or doubt or open_question(\n'
               '                [section for section in hits\n'
               '                 if any(section is each for each in blurred)],\n'
               '                "the place it points to holds a capability that was not "\n'
               '                "read")))',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_and_the_capability_before_a_doubt_about_the_pointer_itself",
        ],
    },
    {
        "what": 'synthesize — a claim that was set aside drops out of the count',
        "why": "the report says how many of the document's evidence claims D3 "
               'checked. A claim given as a bare sentence is one of them. Left '
               'out, "checked 1 of 1" is printed over a section that holds '
               'three',
        "module": "synthesize",
        "old": '    unchecked[SET_ASIDE] += sum(each["list"] == "evidence_claims"\n'
               '                                for each in aside)',
        "new": '    unchecked[SET_ASIDE] += 0',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_a_claim_is_one_that_d3_did_not_check",
        ],
    },
    {
        "what": 'synthesize — the reasons a finding is not asserted are listed '
                'without the item that was set aside',
        "why": 'the line under the count of candidates says why a finding is '
               'marked unverifiable: a section not read, a line in no section, '
               'a place not looked up. A finding in doubt over an item was '
               'marked for a reason that list does not give',
        "module": "synthesize",
        "old": '        if aside:\n'
               '            print("   An item of a section\'s lists that could not be read is "',
        "new": '        if False:\n'
               '            print("   An item of a section\'s lists that could not be read is "',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_the_reasons_listed_under_the_count_take_an_item_in",
        ],
    },
    {
        "what": 'synthesize — a score does not say how many items it was made '
                'without',
        "why": 'a score over an inventory with items set aside is a score of '
               'what was read. The line that says what the inventory left '
               'unread was not printed for it',
        "module": "synthesize",
        "old": '        if unread or partial or short or no_section or aside:',
        "new": '        if unread or partial or short or no_section:',
        "tests": [
            "tests.test_malformed_inventory.WhatAnItemSetAsideCouldHaveAnswered."
            "test_and_a_score_says_how_many_items_it_was_made_without",
        ],
    },
    {
        "what": 'synthesize.consult — a name of the document that is not text '
                'is looked up',
        "why": '`"doc": ["d"]` was looked up in the manifest and printed as '
               'Python prints a list. Nested a hundred thousand deep, which '
               'Python 3.14 parses, it raised where it was printed',
        "module": "synthesize",
        "old": '    if not isinstance(slug, str) or not slug.isprintable():\n',
        "new": '    if False:\n',
        "tests": [
            "tests.test_malformed_inventory.WhichDocumentItWasBuiltFrom."
            "test_a_name_that_is_not_text_names_no_document",
        ],
    },
    {
        "what": 'synthesize.consult — a name of the document with a line break '
                'in it is looked up',
        "why": 'it names no document, and the line that says so printed it: '
               'what came after the break was a line of the report that this '
               'program did not write',
        "module": "synthesize",
        "old": '    if not isinstance(slug, str) or not slug.isprintable():\n',
        "new": '    if not isinstance(slug, str):\n',
        "tests": [
            "tests.test_malformed_inventory.WhichDocumentItWasBuiltFrom."
            "test_nor_does_one_with_a_character_that_cannot_be_printed",
            "tests.test_malformed_inventory.TextThatNothingCanEncode."
            "test_in_the_name_of_the_document",
        ],
    },
    {
        "what": 'synthesize.moved — a heading that is not text is looked for in '
                'the document',
        "why": 'an entry whose heading is a list is one that was not read. '
               'Compared all the same with the line its locator names, it is '
               'not that line, and the document is called a text other than '
               'the one the inventory was built from',
        "module": "synthesize",
        "old": '        if not isinstance(heading, str) or not span:',
        "new": '        if heading is None or not span:',
        "tests": [
            "tests.test_malformed_inventory.WhereAnEntryThatWasNotReadSaysItIs."
            "test_one_that_does_not_say_where_in_text_says_nothing",
        ],
    },
    {
        "what": 'synthesize.reached — every run of digits in a locator is a '
                'line number',
        "why": 'a locator with five thousand digits in a row is text. int() '
               'refuses them from Python 3.11 on, and a run scored against a '
               'ground truth raised. Twenty noughts and a 5 were line 5',
        "module": "synthesize",
        "old": '            for number in re.findall(r"(?<!\\d)\\d{1,9}(?!\\d)",',
        "new": '            for number in re.findall(r"\\d+",',
        "tests": [
            "tests.test_malformed_inventory.WhatAnEntrySaysOfItself."
            "test_a_locator_with_more_digits_than_a_line_number_has",
        ],
    },
    {
        "what": 'synthesize — a line break in a claim begins a line of the '
                'report',
        "why": 'a model copies a sentence that runs over two lines. Printed as '
               'it stands, the second line of it is a line of the report that '
               'this program did not write',
        "module": "synthesize",
        "old": '        print(f"[{kind}] {one_line(label)[:76]}")',
        "new": '        print(f"[{kind}] {label[:76]}")',
        "tests": [
            "tests.test_malformed_inventory.WhatIsTakenOfAnItem."
            "test_a_line_break_in_a_claim_does_not_begin_a_line_of_the_report",
        ],
    },
    {
        "what": 'synthesize.one_line — a control character that is not a line '
                'break is printed as it came',
        "why": 'an escape sequence in what a claim points to goes to the '
               'terminal with the finding, and can take back the line the '
               'terminal has shown above it',
        "module": "synthesize",
        "old": '    return "".join(" " if unicodedata.category(ch) in ("Cc", "Zl", "Zp")\n'
               '                   else ch for ch in text)',
        "new": '    return " ".join(text.splitlines())',
        "tests": [
            "tests.test_malformed_inventory.HowAFindingIsPrinted."
            "test_a_control_character_is_a_space_where_a_line_is_printed",
        ],
    },
    {
        "what": 'synthesize.writable — half of a surrogate pair is left as it '
                'came',
        "why": 'it is text of the right kind in every place of an inventory, '
               'and JSON spells it "\\ud83d". Left as it came it raises where a '
               'finding is printed, where the candidate file is written, and '
               'where a text is hashed for the embedding cache or a prompt for '
               'a model call',
        "module": "synthesize",
        "old": '    return text.encode("utf-8", '
               '"backslashreplace").decode("utf-8")',
        "new": '    return text',
        "tests": [
            "tests.test_malformed_inventory.TextThatNothingCanEncode."
            "test_in_a_claim_and_in_what_it_points_to",
            "tests.test_malformed_inventory.TextThatNothingCanEncode."
            "test_in_the_heading_of_a_section_that_was_not_read",
            "tests.test_malformed_inventory.TextThatNothingCanEncode."
            "test_in_what_is_embedded_and_in_what_is_put_to_a_model",
            "tests.test_malformed_inventory.TextThatNothingCanEncode."
            "test_and_in_the_file_of_candidates",
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


def imported(name):
    """The module object a mutation is applied to.

    A top-level tool imports by name. A script below the top level
    ("fixtures/sec/fetch") cannot, so it comes from tests.support.script, which
    hands the tests and the mutation the same object. Loading a second copy here
    would break a module no test is looking at, and every mutation of it would
    read NOT CAUGHT — the subprocess trap described above MUTATIONS, by another
    route.
    """
    if "/" in name:
        from tests.support import script
        return script(name + ".py")
    return __import__(name)


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

        module = imported(mutation["module"])
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
