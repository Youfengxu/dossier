"""The install has to ship what the toolkit is.

`pip install .` produced a `dossier` command that died on FileNotFoundError
looking for itself inside site-packages: the console-script shim did
`runpy.run_path(os.path.join(here, "dossier"))`, which works in a clone where the
two files sit side by side, and cannot work once installed, because pip copies
.py modules and leaves an extensionless file behind. Twelve tools were also
missing from `py-modules` — the list was hand-maintained, and every tool added
since it was written had been forgotten.

Neither was caught, because no gate had ever installed the package. Same shape as
the extractor no CI step ran and the four tools that never answered `--help`: the
documented path and the tested path were different paths.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def declared_modules():
    """py-modules from pyproject.toml, without needing tomllib (3.11+)."""
    text = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    block = re.search(r"py-modules\s*=\s*\[(.*?)\]", text, re.S)
    return {m for m in re.findall(r'"([^"]+)"', block.group(1))}


class Packaging(unittest.TestCase):

    def test_every_top_level_tool_is_declared(self):
        """A hand-maintained list drifts. This is what stops it shipping."""
        present = {f[:-3] for f in os.listdir(ROOT)
                   if f.endswith(".py") and not f.startswith("_")}
        missing = sorted(present - declared_modules())
        self.assertEqual(missing, [], f"not in pyproject py-modules: {missing}. "
                                      f"An installed user does not get these.")

    def test_nothing_is_declared_that_does_not_exist(self):
        declared = declared_modules()
        present = {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}
        stale = sorted(declared - present)
        self.assertEqual(stale, [], f"declared but absent: {stale}")

    def test_the_console_script_aims_at_something_importable(self):
        """The entry point must name a module pip actually installs — not the
        extensionless dispatcher, which is what broke it."""
        text = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
        target = re.search(r"dossier\s*=\s*\"([^:]+):", text).group(1)
        self.assertIn(target, declared_modules())
        self.assertTrue(os.path.exists(os.path.join(ROOT, target + ".py")))

    def test_the_dispatcher_is_the_module_not_the_script(self):
        """`dossier` is a shim over dossier_cli, not the other way round. Inverted,
        the installed command cannot find the dispatcher at all."""
        shim = open(os.path.join(ROOT, "dossier"), encoding="utf-8").read()
        self.assertIn("from dossier_cli import main", shim)
        self.assertNotIn("runpy", shim)
        cli = open(os.path.join(ROOT, "dossier_cli.py"), encoding="utf-8").read()
        self.assertIn("def cmd_review", cli, "the dispatcher is not in the module")


if __name__ == "__main__":
    unittest.main()
