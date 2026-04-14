# core/logging.py
import logging
import structlog
from config import settings


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log.level.upper()),
        format="%(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(settings.log.file),
        ],
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer()   # JSONRenderer() en prod
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log.level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


log = structlog.get_logger()
