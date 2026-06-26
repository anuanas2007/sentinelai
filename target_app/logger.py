import sys
import os
import structlog

def setup_logger(log_path: str = None) -> None:
    """
    Sets up structlog to write to both stdout and a log file.

    Called once at app startup — keeps main.py clean.

    Why separate file:
    Logging setup is infrastructure, not business logic.
    main.py should only contain the app and its endpoints.
    Mixing infrastructure setup with business logic violates
    the single responsibility principle.
    """
    # LOG_PATH env var lets docker-compose point this at a shared volume
    # without changing the local dev default (run from target_app/).
    if log_path is None:
        log_path = os.environ.get("LOG_PATH", "../logs/app.log")

    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # "w" not "a" -- truncate on every fresh startup. app.log has no size
    # cap and no rotation; without this it grows forever across restarts
    # and especially traffic-simulator runs (33k+ lines in one 30s burst).
    log_file = open(log_path, "w", buffering=1)

    class MultiWriter:
        def write(self, msg):
            sys.stdout.write(msg)
            log_file.write(msg)
        def flush(self):
            sys.stdout.flush()
            log_file.flush()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.WriteLoggerFactory(file=MultiWriter()),
    )