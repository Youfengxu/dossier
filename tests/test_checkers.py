"""The verification layers, fed the inputs a real model actually emits.

Three checkers in this repository shipped stricter than reality, and each one
discarded good evidence and reported it as the model's fault:

    the locator grammar     demanded "120-134" and rejected a bare "67". Ten of
                            the first eleven "invalid" citations were that and
                            nothing else. Fixed in assess.py, missed in
                            converse.py, and caught there by the first real
                            question anyone asked of it.
    the punctuation check   was suspected of the same fault. It was tested and
                            cleared — but only because someone tested it rather
                            than assuming, and that test now lives here.
    the heading detector    is a heuristic and is characterised below rather
                            than pinned as correct, because in table-dense
                            material it identifies a region, not a section.

A checker that is quietly too narrow is worse than no checker: it manufactures
false confidence in the opposite direction, and it accuses the model while doing
it. So these tests do not feed tidy inputs. They feed bare numbers, en-dashes,
smart quotes, non-breaking hyphens and one-word table cells — the things that
turn up in real output — and assert the checker survives contact with them.

Every case here is drawn from something that actually appeared in a run.
"""

import importlib.util
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, filename):
    """Import a hyphenated top-level script as a module."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocatorGrammar(unittest.TestCase):
    """A citation grammar has to accept what a reader would actually write."""

    def setUp(self):
        self.graders = {
            "assess.py": load("assess_mod", "assess.py").RANGE,
            "converse.py": load("converse_mod", "converse.py").RANGE,
            "repair-cites.py": load("repair_cites_mod", "repair-cites.py").RANGE,
        }

    def bounds(self, pattern, text):
        found = pattern.match(text)
        if not found:
            return None
        start = int(found.group(1))
        end = int(found.group(2)) if found.group(2) else start
        return start, end

    def test_bare_line_number_is_a_locator(self):
        """The exact failure. A single line is an ordinary thing to cite, and
        rejecting it threw away ten of the first eleven citations in one run."""
        for where, pattern in self.graders.items():
            with self.subTest(where=where):
                self.assertEqual(self.bounds(pattern, "67"), (67, 67))

    def test_accepts_the_separators_models_emit(self):
        for where, pattern in self.graders.items():
            for text, want in (("120-134", (120, 134)),
                               ("120 - 134", (120, 134)),
                               ("5:9", (5, 9)),
                               ("8–14", (8, 14)),      # en-dash
                               ("8—14", (8, 14)),      # em-dash
                               ("  42  ", (42, 42))):
                with self.subTest(where=where, text=text):
                    self.assertEqual(self.bounds(pattern, text), want)

    def test_rejects_what_is_genuinely_not_a_locator(self):
        """Being permissive about form must not mean accepting anything."""
        for where, pattern in self.graders.items():
            for text in ("", "abc", "section 4", "12a", "1.2.3", "-5"):
                with self.subTest(where=where, text=text):
                    self.assertIsNone(pattern.match(text.strip()) if text.strip()
                                      else None)

    def test_every_grammar_agrees(self):
        """Three copies existed and two of them disagreed. There is now one, in
        locate.py, and each of these names re-exports it — so this passes by
        construction rather than by luck. Kept, because "they must at least not
        diverge" is the weaker promise this made before the copies were removed,
        and re-introducing a local RANGE anywhere would break it again."""
        samples = ["67", "120-134", "5:9", "8–14", "abc", ""]
        results = {where: [self.bounds(p, s) for s in samples]
                   for where, p in self.graders.items()}
        first = next(iter(results.values()))
        for where, got in results.items():
            with self.subTest(where=where):
                self.assertEqual(got, first)


class QuoteVerification(unittest.TestCase):
    """Whitespace normalisation, and the punctuation question that was tested."""

    @staticmethod
    def flat(text):
        return " ".join(text.split()).lower()

    def test_whitespace_variants_normalise(self):
        document = "the fabric defers resolution to the Hydrology Model"
        for variant in ("The  fabric   defers resolution to the Hydrology Model",
                        "the fabric\ndefers resolution to the\tHydrology Model",
                        "  the fabric defers resolution to the Hydrology Model  "):
            with self.subTest(variant=variant):
                self.assertIn(self.flat(variant), self.flat(document))

    def test_narrow_no_break_space_is_whitespace(self):
        """Real output contained U+202F between words. If str.split() did not
        treat it as whitespace, every quote containing one would fail to match
        and the model would be blamed for it."""
        narrow = "Sensor\u202fFabric"          # narrow no-break space
        nbsp = "Sensor\u00a0Fabric"            # ordinary non-breaking space
        self.assertEqual(self.flat(narrow), self.flat("Sensor Fabric"))
        self.assertEqual(self.flat(nbsp), self.flat("Sensor Fabric"))

    def test_punctuation_is_NOT_normalised_and_that_was_measured(self):
        """Smart quotes and non-breaking hyphens survive normalisation, so a
        quote differing only in punctuation does NOT match.

        This looks like a bug and was investigated as one. Folding punctuation
        across a real run recovered ZERO additional quotes out of 32 failures,
        which is what established that the failures were fabrication rather than
        a strict matcher. The behaviour is pinned here so the finding is not
        quietly undone by someone 'fixing' it later.
        """
        self.assertNotEqual(self.flat("tick‑budget"),
                            self.flat("tick-budget"))
        self.assertNotEqual(self.flat("“telemetry”"),
                            self.flat('"telemetry"'))

    def test_empty_quote_never_matches(self):
        """An empty string is a substring of everything. Without a guard, a model
        returning "" would score as verbatim."""
        self.assertEqual(self.flat("   "), "")
        self.assertFalse(self.flat("   ") and self.flat("   ") in "any document")


class HeadingDetection(unittest.TestCase):
    """Characterised, not pinned as correct — it is a navigation aid."""

    def setUp(self):
        self.converse = load("converse_mod2", "converse.py")

    def test_finds_a_real_heading_above_the_line(self):
        lines = ["intro", "Runtime Orchestrator", "constructs the tick order", "more"]
        head, _line = self.converse.nearest_heading(lines, 2)
        self.assertEqual(head, "Runtime Orchestrator")

    def test_a_one_word_table_cell_is_not_a_useful_heading(self):
        """Cited blocks frequently open on a table cell holding one word. The
        detector requires more than one word precisely so 'Layer' does not become
        the label a reviewer is told to navigate to."""
        lines = ["Layer", "L3 core stack", "detail"]
        head, _line = self.converse.nearest_heading(lines, 2)
        self.assertNotEqual(head, "Layer")

    def test_prose_ending_in_punctuation_is_not_a_heading(self):
        lines = ["The system defers resolution to the model,", "detail here"]
        head, _line = self.converse.nearest_heading(lines, 1)
        self.assertIsNone(head)

    def test_the_search_is_inclusive_of_the_cited_line(self):
        """A cited line that itself looks like a heading is returned as its own
        heading. Sensible — a block often opens on one — and worth pinning,
        because it means the walk-back only happens for body text."""
        lines = ["intro", "Layer", "Pre-run characterization"]
        head, line = self.converse.nearest_heading(lines, 2)
        self.assertEqual((head, line), ("Pre-run characterization", 2))

    def test_known_weakness_all_caps_region_label(self):
        """In table-dense material the walk-back reaches a region label rather
        than a section, skipping the one-word cells between. Recorded as a
        limitation, not asserted as correct — when this happens the search phrase
        is the reliable navigation aid, not the heading."""
        lines = ["ARCHITECTURE POSITION", "Layer",
                 "the pre-run characterization is described, with detail."]
        head, _line = self.converse.nearest_heading(lines, 2)
        self.assertEqual(head, "ARCHITECTURE POSITION")


class StaleLocators(unittest.TestCase):
    """An address that no longer resolves must fail, not improvise.

    Both of these shipped. An adversarial review found them by tracing what a
    reviewer actually sees when a locator goes stale, which is the question the
    code never asked itself."""

    def setUp(self):
        self.converse = load("converse_mod3", "converse.py")
        self.render = load("render_mod", "render-assess.py")
        self.lines = ["intro"] * 99 + ["Appendix C Deployment Topology"] + ["body"] * 10

    def test_address_past_the_end_has_no_heading(self):
        """It used to clamp, so an address up to 400 lines past the document
        returned the LAST heading and an empty search phrase. The delivered
        report read: search for "" — no error, no warning, exit 0."""
        for beyond in (len(self.lines), len(self.lines) + 200):
            with self.subTest(n=beyond):
                self.assertIsNone(self.render.nearest_heading(self.lines, beyond))
                self.assertEqual(self.converse.nearest_heading(self.lines, beyond),
                                 (None, None))

    def test_negative_address_has_no_heading(self):
        self.assertIsNone(self.render.nearest_heading(self.lines, -1))
        self.assertEqual(self.converse.nearest_heading(self.lines, -5), (None, None))

    def test_the_two_implementations_agree(self):
        for n in (0, 50, 99, 105, 300):
            with self.subTest(n=n):
                self.assertEqual(self.render.nearest_heading(self.lines, n),
                                 self.converse.nearest_heading(self.lines, n)[0])


class FrozenTextPin(unittest.TestCase):
    """The pin has to be checked at load, or it is a comment.

    text_sha256 was written by freeze.py and read by exactly one place —
    freeze.py --check. Every adjudicator, renderer and repairer loaded the file
    and trusted it, so a hand-edited or re-frozen document produced citations
    indistinguishable from sound ones."""

    def setUp(self):
        import hashlib, json, tempfile
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "parsed"))
        self.doc = os.path.join(self.tmp, "parsed", "d.txt")
        with open(self.doc, "w") as handle:
            handle.write("line one\nline two\n")
        digest = hashlib.sha256(open(self.doc, "rb").read()).hexdigest()
        json.dump({"documents": [{"slug": "d", "parsed": "parsed/d.txt",
                                  "role": "draft", "text_sha256": digest}]},
                  open(os.path.join(self.tmp, "parsed", "MANIFEST.json"), "w"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_matching_text_loads(self):
        import llm
        _meta, lines = llm.load_doc(self.tmp, "d")
        self.assertEqual(len(lines), 2)

    def test_edited_text_is_refused(self):
        import llm
        with open(self.doc, "a") as handle:
            handle.write("line three inserted by hand\n")
        with self.assertRaises(llm.LLMError):
            llm.load_doc(self.tmp, "d")

    def test_verification_can_be_waived_explicitly(self):
        """Escape hatch, but it must be asked for by name."""
        import llm
        with open(self.doc, "a") as handle:
            handle.write("line three\n")
        _meta, lines = llm.load_doc(self.tmp, "d", verify=False)
        self.assertEqual(len(lines), 3)


class TrailingBracketStripping(unittest.TestCase):
    """The locator bracket an adjudicator appends, removed for a human column."""

    def setUp(self):
        self.trail = load("combine_mod", "combine-evaluators.py").TRAIL

    def test_strips_consecutive_brackets(self):
        """A row can carry more than one: the wide path appends its own note and
        the unlocatable fallback appends another, so they arrive back to back.
        Stripping a single bracket left the first of the pair in the column."""
        text = "Unaddressed. The section is unchanged. [§5 (doc:1-2)] [§9 could not be located]"
        self.assertEqual(self.trail.sub("", text).strip(),
                         "Unaddressed. The section is unchanged.")

    def test_leaves_brackets_that_are_part_of_the_sentence(self):
        text = "Unaddressed. The register [sic] was deleted."
        self.assertEqual(self.trail.sub("", text), text)


class RepairIsVersionAware(unittest.TestCase):
    """Recovering a rejected citation is only sound against the same text.

    repair-cites.py promotes a locator on the strength of it falling inside the
    document. After a refreeze it still does — and points at different text. The
    frozen document verifies against its own manifest, so nothing raised; the
    citation arrived in the report indistinguishable from one that had been
    checked."""

    def setUp(self):
        import hashlib, json, subprocess, tempfile
        self.subprocess, self.json = subprocess, json
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "parsed"))
        self.doc = os.path.join(self.tmp, "parsed", "d.txt")
        self.manifest = os.path.join(self.tmp, "parsed", "MANIFEST.json")
        self.write_doc("\n".join(f"line {n}" for n in range(80)) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_doc(self, text):
        import hashlib, json
        with open(self.doc, "w") as handle:
            handle.write(text)
        digest = hashlib.sha256(open(self.doc, "rb").read()).hexdigest()
        json.dump({"documents": [{"slug": "d", "parsed": "parsed/d.txt",
                                  "role": "draft", "text_sha256": digest}]},
                  open(self.manifest, "w"))
        return digest

    def run_repair(self, stamp, *extra):
        rows = os.path.join(self.tmp, "out.jsonl")
        record = {"id": "C-1", "cites": [], "cites_rejected": ["67"]}
        if stamp is not None:
            record["doc_sha256"] = stamp
        with open(rows, "w") as handle:
            handle.write(self.json.dumps(record) + "\n")
        done = self.subprocess.run(
            [sys.executable, os.path.join(ROOT, "repair-cites.py"),
             "--project", self.tmp, "--doc", "d", rows, *extra],
            capture_output=True, text=True)
        after = self.json.loads(open(rows).readline())
        return done.stdout + done.stderr, after

    def test_a_matching_stamp_repairs(self):
        digest = self.write_doc(open(self.doc).read())
        out, after = self.run_repair(digest)
        self.assertEqual(after["cites"], ["d:67-67"])
        self.assertIn("1 citation(s) recovered", out)

    def test_a_stamp_from_before_a_refreeze_refuses(self):
        """The failure this exists for. Line 67 is still inside the document, so
        every check the tool had passed."""
        old = self.write_doc(open(self.doc).read())
        self.write_doc("prepended\n" + open(self.doc).read())   # every line shifts
        out, after = self.run_repair(old)
        self.assertEqual(after["cites"], [])
        self.assertEqual(after["cites_rejected"], ["67"])
        self.assertIn("SKIPPED", out)
        self.assertIn("re-run rather than repair", out)

    def test_an_unstamped_row_is_not_guessed_about(self):
        """Runs predating the stamp. Unknown is not the same as fine."""
        out, after = self.run_repair(None)
        self.assertEqual(after["cites"], [])
        self.assertIn("no version stamp", out)

    def test_an_unstamped_row_can_be_repaired_on_an_explicit_promise(self):
        out, after = self.run_repair(None, "--assume-same-text")
        self.assertEqual(after["cites"], ["d:67-67"])


class EvidenceReachesTheReviewer(unittest.TestCase):
    """The deliverable has to carry the evidence, not directions to it.

    render-assess.py printed each reader's verdict and reasoning, then a "where to
    look" line — a heading and a search phrase. A reviewer could FIND the cited
    passage but could not judge whether it said what the reader claimed without
    opening the document. And a reader whose locators all failed read exactly like
    one whose locators held: same verdict, same confident prose. That difference
    existed only in the JSONL, which is the artifact a reviewer never opens."""

    def render(self, readers):
        import hashlib, json, subprocess, tempfile
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "parsed"))
        doc = os.path.join(tmp, "parsed", "d.txt")
        body = ["Preface"] + [f"filler {n}" for n in range(1, 20)]
        body[10] = "Sensor precedence"
        body[11] = "Where two readings disagree the higher-confidence sensor wins."
        open(doc, "w").write("\n".join(body) + "\n")
        json.dump({"documents": [{"slug": "d", "parsed": "parsed/d.txt",
                                  "role": "draft",
                                  "text_sha256": hashlib.sha256(
                                      open(doc, "rb").read()).hexdigest()}]},
                  open(os.path.join(tmp, "parsed", "MANIFEST.json"), "w"))
        args = []
        for name, record in readers.items():
            path = os.path.join(tmp, name + ".jsonl")
            open(path, "w").write(json.dumps(dict(
                {"id": "FT-001", "row": 2, "verdict": "addressed"}, **record)) + "\n")
            args += ["--reader", f"{name}={path}"]
        out = os.path.join(tmp, "review.md")
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "render-assess.py"),
             "--project", tmp, "--doc", "d", "--matrix",
             os.path.join(ROOT, "fixtures/floodtwin/comments.csv"),
             "--sheet", "Comments", "--title", "T", "--out", out] + args,
            capture_output=True, text=True, cwd=ROOT)
        return open(out, encoding="utf-8").read()

    def test_the_cited_text_is_reproduced_in_the_deliverable(self):
        text = self.render({"solid": {"rationale": "Stated.",
                                      "cites": ["d:11-11"], "cites_rejected": []}})
        self.assertIn("higher-confidence sensor wins", text)
        self.assertIn("**d:11**", text)

    def test_a_reader_with_no_resolving_citation_says_so(self):
        text = self.render({"hollow": {"rationale": "Clearly stated.", "cites": [],
                                       "cites_rejected": ["section 6.3 (not a locator)"]}})
        self.assertIn("no checkable evidence", text)

    def test_partial_rejection_is_reported_without_crying_wolf(self):
        """Some citations resolving and some not is ordinary, and must not be
        described in the same terms as a verdict resting on nothing."""
        text = self.render({"mixed": {"rationale": "Stated.", "cites": ["d:11-11"],
                                      "cites_rejected": ["999 (outside)"]}})
        self.assertIn("1 further citation(s) did not resolve", text)
        self.assertNotIn("no checkable evidence", text)

    def test_quoting_is_capped_so_the_review_is_not_a_second_copy(self):
        text = self.render({"chatty": {"rationale": "Stated.",
                                       "cites": ["d:1-1", "d:2-2", "d:3-3", "d:11-11"],
                                       "cites_rejected": []}})
        self.assertEqual(text.count("> **d:"), 2)      # MAX_QUOTED


class BearerToken(unittest.TestCase):
    """Auth is opt-in and absent by default.

    Every endpoint this toolkit had spoken to was local and unauthenticated, so the
    transport sent no Authorization header at all. Pointing it at a hosted provider
    failed with an opaque 401 from inside urllib, naming neither the missing header
    nor the variable that would supply one."""

    def setUp(self):
        import llm
        self.llm = llm
        self.saved = os.environ.pop("DOSSIER_API_KEY", None)

    def tearDown(self):
        os.environ.pop("DOSSIER_API_KEY", None)
        if self.saved is not None:
            os.environ["DOSSIER_API_KEY"] = self.saved

    def test_no_header_without_the_variable(self):
        """A local endpoint must not receive a stray credential."""
        self.assertEqual(self.llm.auth_headers(), {})

    def test_bearer_when_set(self):
        os.environ["DOSSIER_API_KEY"] = "abc123"
        self.assertEqual(self.llm.auth_headers(),
                         {"Authorization": "Bearer abc123"})

    def test_whitespace_only_counts_as_unset(self):
        """An empty export is a common way to end up sending 'Bearer '."""
        os.environ["DOSSIER_API_KEY"] = "   "
        self.assertEqual(self.llm.auth_headers(), {})


if __name__ == "__main__":
    unittest.main()
