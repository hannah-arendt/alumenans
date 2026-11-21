#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Starts the program and, if it hangs, writes down exactly where.

The preparation window says «Открываю окно программы» ("Opening the
program window") and stays like that forever. That message tells you
nothing: it is the last one known, and after it comes waiting for who
knows what.

Here the program starts as usual, but after 45 seconds a snapshot of all
its threads goes into the file startup_stack.txt next to it. The snapshot
shows the line each thread is stuck on. That is the answer.

The file itself is ours, for debugging. It does not go into the installer.
"""

from __future__ import annotations

import faulthandler
import runpy
import sys

from alumenans.paths import INSTALL_ROOT

REPORT = INSTALL_ROOT / "startup_stack.txt"
WAIT_SECONDS = 45


def main() -> int:
    file = REPORT.open("w", encoding="utf-8")
    file.write(f"The program did not open its window within {WAIT_SECONDS} seconds.\n")
    file.write("Below is where each thread is stuck.\n\n")
    file.flush()

    # exit=True: after the snapshot the process exits, otherwise the
    # preparation window would just stay hanging on the screen.
    faulthandler.dump_traceback_later(WAIT_SECONDS, exit=True, file=file)

    sys.argv = ["alumenans"]
    try:
        runpy.run_module("alumenans", run_name="__main__", alter_sys=True)
    except BaseException:
        # Without a console (pythonw) there is nowhere to print the error
        # message, and the program simply vanishes. We write it to the same
        # file.
        import traceback

        file.write("\nThe program exited with an error:\n")
        traceback.print_exc(file=file)
        file.flush()
        raise

    # We only get this far if the program opened and was closed by hand.
    faulthandler.cancel_dump_traceback_later()
    file.close()
    REPORT.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
