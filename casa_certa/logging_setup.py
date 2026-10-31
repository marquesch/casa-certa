"""Progress logging for the command line: short lines on stderr, with the chatty libraries kept quiet."""

import logging
import sys

# Libraries whose INFO output (one line per HTTP request, actor log streams...) would drown the progress lines.
NOISY_LOGGERS = ("httpx", "httpcore", "apify", "apify_client", "openai")


def quiet_noisy_loggers() -> None:
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    quiet_noisy_loggers()
