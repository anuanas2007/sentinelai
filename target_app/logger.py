import sys
import os
import structlog

def setup_logger(log_path: str = "../logs/app.log"):
    """
    Sets up structlog to write to both stdout and a log file.
    
    Called once at app startup — keeps main.py clean.
    
    Why separate file:
    Logging setup is infrastructure, not business logic.
    main.py should only contain the app and its endpoints.
    Mixing infrastructure setup with business logic violates
    the single responsibility principle.
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    
    log_file = open(log_path, "a", buffering=1)

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