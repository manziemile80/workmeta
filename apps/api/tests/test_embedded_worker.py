"""In-process background worker tests (no external services required)."""

from __future__ import annotations

import asyncio

import pytest

from app import main as main_module
from app.core.config import Settings
from app.main import lifespan
from app.workers import embedded


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "worker_poll_seconds": 0.1,
        "embedded_worker_billing_seconds": 3600.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def test_worker_drains_the_queue_then_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_process_next_job(**_: object) -> bool:
        calls.append("job")
        # Two jobs are waiting, then the queue is empty.
        return calls.count("job") <= 2

    async def fake_billing_sweep() -> dict[str, int]:
        calls.append("billing")
        return {}

    monkeypatch.setattr(embedded, "process_next_job", fake_process_next_job)
    monkeypatch.setattr(embedded, "billing_sweep", fake_billing_sweep)
    monkeypatch.setattr(embedded, "get_settings", _settings)

    stop = asyncio.Event()
    task = asyncio.create_task(embedded.run_embedded_worker(stop))

    for _ in range(100):
        if calls.count("job") >= 3:
            break
        await asyncio.sleep(0.02)

    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert calls.count("job") >= 3, "the worker must keep polling for jobs"
    assert calls.count("billing") == 1, "billing must not be swept on every poll"


async def test_worker_survives_a_failing_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    polls: list[int] = []

    async def failing_process_next_job(**_: object) -> bool:
        polls.append(1)
        raise RuntimeError("database is unreachable")

    async def fake_billing_sweep() -> dict[str, int]:
        return {}

    monkeypatch.setattr(embedded, "process_next_job", failing_process_next_job)
    monkeypatch.setattr(embedded, "billing_sweep", fake_billing_sweep)
    monkeypatch.setattr(embedded, "get_settings", _settings)

    stop = asyncio.Event()
    task = asyncio.create_task(embedded.run_embedded_worker(stop))

    for _ in range(100):
        if polls:
            break
        await asyncio.sleep(0.02)

    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert polls, "the worker must have attempted a poll"


async def test_lifespan_starts_and_stops_the_embedded_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def fake_worker(stop: asyncio.Event) -> None:
        events.append("started")
        await stop.wait()
        events.append("stopped")

    monkeypatch.setattr(main_module.settings, "embedded_worker_enabled", True)
    monkeypatch.setattr(main_module, "run_embedded_worker", fake_worker)

    async with lifespan(main_module.app):
        await asyncio.sleep(0.05)
        assert events == ["started"]

    assert events == ["started", "stopped"]


async def test_lifespan_leaves_the_worker_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = False

    async def fake_worker(stop: asyncio.Event) -> None:
        nonlocal started
        started = True
        await stop.wait()

    monkeypatch.setattr(main_module.settings, "embedded_worker_enabled", False)
    monkeypatch.setattr(main_module, "run_embedded_worker", fake_worker)

    async with lifespan(main_module.app):
        await asyncio.sleep(0.05)

    assert started is False
