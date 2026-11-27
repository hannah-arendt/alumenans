"""Where errors are reported: the terminal the program was started from.

Every module logs through the standard `logging` module, and this sets up
the one place the messages go, the standard error stream. Started as a
window with no console, the program has no such stream, and the messages go
nowhere. They are deliberately not written to a file: an error message may
quote the interview, and nothing of an interview is kept on disk
unencrypted.
"""

import logging
import sys


def configure() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
