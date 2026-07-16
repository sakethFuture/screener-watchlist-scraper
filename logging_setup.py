import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        # Already configured (e.g. re-entrant calls from tests/reload) - just adjust level.
        root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)

    # Playwright and apscheduler are chatty at INFO; keep them at WARNING unless
    # the whole app is running in DEBUG.
    if level.upper() != "DEBUG":
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
