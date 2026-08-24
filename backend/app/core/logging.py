import sys

from loguru import logger

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level, backtrace=False, diagnose=False)
    logger.add(
        settings.log_dir / "app.log",
        level=settings.log_level,
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        backtrace=False,
        diagnose=False,
    )
