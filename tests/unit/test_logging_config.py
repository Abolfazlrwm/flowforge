"""``core.worker`` INFO must reach a handler under the real LOGGING config.

The earlier ``caplog``-based test could not catch the production bug where
``core.worker`` had no INFO config and the "connected to the broker" line was
silently dropped: ``caplog.set_level`` lowers the effective level itself, so the
line looked fine in the test while remaining invisible in a real process.

These tests load ``settings.LOGGING`` through ``logging.config.dictConfig`` — the
same call Django makes at startup — and assert the configured level is honoured
end to end. They also pin the scoping decision: INFO is enabled for ``core.worker``
only, never for all of ``core``, so ``core.repositories.scheduler``'s own INFO call
must stay filtered out.
"""

from __future__ import annotations

import logging
import logging.config
from unittest.mock import MagicMock, patch

from core.worker.consumer import Worker
from django.conf import settings


class _CaptureHandler(logging.Handler):
    """A handler that records every record it is asked to emit."""

    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


def _worker() -> Worker:
    return Worker(url="fake://", lease_seconds=30, worker_id="w1")


class TestCoreWorkerLogging:
    """The worker's INFO line must be emitted under the real config."""

    def test_connected_to_broker_is_emitted_at_info(self) -> None:
        """Loading ``settings.LOGGING`` emits the "connected" line at INFO."""
        logging.config.dictConfig(settings.LOGGING)
        core_worker = logging.getLogger("core.worker")
        records: list[logging.LogRecord] = []
        handler = _CaptureHandler(records)
        core_worker.addHandler(handler)
        try:
            worker = _worker()
            channel = MagicMock()

            def connect() -> MagicMock:
                # Stop right after the connect is logged so ``run()`` exits cleanly.
                worker.stop()
                return channel

            with patch.object(worker, "_connect", side_effect=connect):
                worker.run()

            infos = [r.getMessage() for r in records if r.levelno == logging.INFO]
            assert "Worker w1 connected to the broker" in infos, infos
        finally:
            core_worker.removeHandler(handler)

    def test_core_scheduler_info_stays_filtered(self) -> None:
        """Scoping INFO to ``core.worker`` must not enable ``core.scheduler``.

        The scheduler has its own INFO call; enabling INFO for all of ``core``
        would start leaking it into the logs. Under the real config (only
        ``core.worker`` at INFO) a ``core.scheduler`` INFO record is filtered out
        and never reaches a handler.
        """
        logging.config.dictConfig(settings.LOGGING)
        scheduler = logging.getLogger("core.scheduler")
        records: list[logging.LogRecord] = []
        handler = _CaptureHandler(records)
        scheduler.addHandler(handler)
        try:
            scheduler.info("compensation_started run_id=x compensator=y")
            assert records == [], records
        finally:
            scheduler.removeHandler(handler)
