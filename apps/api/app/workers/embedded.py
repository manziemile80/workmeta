"""In-process background worker.

A dedicated worker (``python -m app.workers.ingestion``) is the preferred
deployment, but the Render Free plan has no worker service. Without a
worker, uploaded documents stay ``queued`` forever: the knowledge base never
fills up and retrieval finds nothing. This module runs the same job loop
inside the API process so a single-instance deployment still works.

Enable it with ``EMBEDDED_WORKER_ENABLED=true``.

Running the embedded loop next to a dedicated worker is safe: ingestion jobs
are claimed with ``FOR UPDATE SKIP LOCKED``, so two workers never claim the
same job. The billing sweep is idempotent and advances each subscription at
most one step per pass.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog

from app.core.config import get_settings
from app.services.ai import AIProvider, build_ai_provider
from app.services.ocr import OCREngine, build_ocr_engine
from app.services.storage import StorageProvider, build_storage
from app.workers.billing import sweep as billing_sweep
from app.workers.ingestion import process_next_job

logger = structlog.get_logger(__name__)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    """Sleep, but return as soon as the worker is asked to stop."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


async def run_embedded_worker(stop: asyncio.Event) -> None:
    """Drain the ingestion queue and sweep billing until ``stop`` is set.

    The loop keeps draining while jobs are available and only sleeps when the
    queue is empty, so a burst of uploads is processed back-to-back.
    """
    settings = get_settings()
    poll = max(settings.worker_poll_seconds, 0.5)
    billing_interval = max(settings.embedded_worker_billing_seconds, poll)
    loop = asyncio.get_running_loop()
    next_billing_at = 0.0
    providers: tuple[AIProvider, StorageProvider, OCREngine | None] | None = None

    logger.info("embedded_worker_started", poll_seconds=poll)
    try:
        while not stop.is_set():
            if providers is None:
                # Built once, like the standalone worker does. A failure here
                # (a misconfigured provider) must not kill the loop.
                try:
                    providers = (
                        build_ai_provider(),
                        build_storage(),
                        build_ocr_engine(),
                    )
                except Exception:  # noqa: BLE001 - retry on the next tick
                    logger.exception("embedded_worker_setup_failed")
                    await _wait(stop, poll)
                    continue
            ai, storage, ocr = providers

            try:
                processed = await process_next_job(ai=ai, storage=storage, ocr=ocr)
            except Exception:  # noqa: BLE001 - a failed poll must not kill the loop
                logger.exception("embedded_ingestion_poll_failed")
                processed = False

            if processed:
                # Another job is waiting: keep going without sleeping.
                continue

            now = loop.time()
            if now >= next_billing_at:
                try:
                    await billing_sweep()
                except Exception:  # noqa: BLE001 - billing must not kill the loop
                    logger.exception("embedded_billing_sweep_failed")
                next_billing_at = now + billing_interval

            await _wait(stop, poll)
    finally:
        logger.info("embedded_worker_stopped")
