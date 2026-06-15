# This sets up logging for the whole app.
# Every file uses this to print logs — never use plain print() statements.
# Logs come out as clean JSON which Grafana can read easily later.

import logging
import structlog
from app.core.config import settings


def setup_logging():
    # In debug mode show everything, in production show only warnings and errors
    log_level = logging.DEBUG if settings.DEBUG else logging.WARNING

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
    )

    # This is the pipeline every log message goes through before printing
    structlog.configure(
        processors=[
            # Adds "info" / "error" / "warning" label to every log line
            structlog.stdlib.add_log_level,
            # Adds timestamp to every log line
            structlog.processors.TimeStamper(fmt="iso"),
            # Formats any exceptions nicely if they happen
            structlog.processors.format_exc_info,
            # Prints everything as JSON
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# Every file uses this to get a logger
# Usage:
#   from app.core.logging import get_logger
#   logger = get_logger(__name__)
#   logger.info("file uploaded", filename="sales.csv", user_id=3)
def get_logger(name: str = __name__):
    return structlog.get_logger(name)