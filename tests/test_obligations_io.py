"""Obligations are read with a YAML parser, not something shaped like one.

`trace.py` had a hand-rolled line reader that required continuation keys to be
indented by exactly four spaces — the indentation `obligations.py` happens to
emit. Any other valid YAML for the same data, PyYAML's own two-space output
included, parsed into entries carrying nothing but `id`.

It did not raise. It returned obligations with no text, and the run would have
gone on to ask a model about nothing and write the answers to a coverage file.
Only `--scope` surfaced it, and then as "no obligations with scope X", which names
neither the file nor the cause.
"""

import importlib.util
import os
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def trace():
    spec = importlib.util.spec_from_file_location("trace_mod",
                                                  os.path.join(ROOT, "trace.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ITEMS = [{"id": "O-001", "modality": "SHALL", "text": "The system shall log.",
          "scope": "process", "source_ref": "a.i"},
         {"id": "O-002", "modality": "SHALL", "text": "The system shall alert.",
          "scope": "process", "source_ref": "a.ii"}]


class Reading(unittest.TestCase):

    def write(self, text):
        path = os.path.join(tempfile.mkdtemp(), "obligations.yaml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_pyyaml_two_space_output_is_read(self):
        """The shape that silently produced id-only entries."""
        path = self.write(yaml.safe_dump({"obligations": ITEMS}, sort_keys=False))
        got = trace().load_obligations(path)
        self.assertEqual([o["id"] for o in got], ["O-001", "O-002"])
        self.assertEqual(got[0]["text"], "The system shall log.")
        self.assertEqual(got[1]["scope"], "process")

    def test_four_space_output_is_still_read(self):
        """What obligations.py emits. The fix must not trade one for the other."""
        body = "obligations:\n" + "".join(
            f"  - id: {o['id']}\n" + "".join(
                f"    {k}: {v}\n" for k, v in o.items() if k != "id")
            for o in ITEMS)
        got = trace().load_obligations(self.write(body))
        self.assertEqual(got[0]["text"], "The system shall log.")

    def test_a_bare_list_is_read(self):
        got = trace().load_obligations(self.write(yaml.safe_dump(ITEMS)))
        self.assertEqual(len(got), 2)

    def test_an_obligation_with_no_text_is_refused_by_name(self):
        """The silent-garbage case: tracing an obligation with no text asks a
        model a question that is not there, and it answers confidently."""
        bad = [dict(ITEMS[0]), {"id": "O-009", "modality": "SHALL"}]
        with self.assertRaises(SystemExit) as caught:
            trace().load_obligations(self.write(yaml.safe_dump({"obligations": bad})))
        self.assertIn("O-009", str(caught.exception))

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(SystemExit):
            trace().load_obligations(self.write("obligations: []\n"))


if __name__ == "__main__":
    unittest.main()
