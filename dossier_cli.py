#!/usr/bin/env python3
"""Console-script shim, so `pip install` yields a `dossier` command.

The dispatcher itself is the extensionless `dossier` script, and it stays that
way on purpose: `./dossier review fixtures/floodtwin ...` has to work in a fresh
clone with nothing installed, which is the thirty-second path the README opens
with. An entry point needs something importable to aim at, and renaming the
dispatcher to provide one would have touched the README, both CI steps and two
gates for no benefit a five-line module cannot give.
"""

import os
import runpy
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "dossier")
    sys.argv[0] = script
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
