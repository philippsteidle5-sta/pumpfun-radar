"""Daily agent logs. Secret values are scrubbed before they hit disk."""

from __future__ import annotations

import logging
from pathlib import Path

from desk.scrub import ScrubFilter, scrub


class DailyFileHandler(logging.Handler):
    def __init__(self, directory: Path, agent: str, clock, secrets: list[str]):
        super().__init__()
        self.directory = Path(directory)
        self.agent = agent
        self.clock = clock
        self.secrets = secrets
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = self.clock.now().strftime("%Y-%m-%d")
            path = self.directory / f"{self.agent}-{day}.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            line = scrub(self.format(record), self.secrets)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            self.handleError(record)


def configure_logging(directory: Path, clock, secrets: list[str]) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    scrubber = ScrubFilter(secrets)
    for agent in ("scanner", "auditor", "analyst", "executor", "risk", "desk", "links"):
        logger = logging.getLogger(f"desk.{agent}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()
        logger.filters.clear()
        logger.addFilter(scrubber)
        logger.addHandler(DailyFileHandler(directory, agent, clock, secrets))


def write_raw(directory: Path, name: str, text: str, secrets: list[str]) -> Path:
    path = Path(directory) / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scrub(text, secrets), encoding="utf-8")
    return path
