"""Unit tests for app.utils.scheduler module."""

from __future__ import annotations

import asyncio

import pytest

from app.utils.scheduler import BackgroundScheduler, PeriodicTask


class TestPeriodicTask:
    def test_defaults(self) -> None:
        async def noop() -> None:
            pass

        task = PeriodicTask(name="t", func=noop, interval_seconds=60)
        assert task.name == "t"
        assert task.interval_seconds == 60
        assert task.run_on_start is True

    def test_run_on_start_false(self) -> None:
        async def noop() -> None:
            pass

        task = PeriodicTask(name="t", func=noop, interval_seconds=10, run_on_start=False)
        assert task.run_on_start is False


class TestBackgroundScheduler:
    def test_register_adds_task(self) -> None:
        scheduler = BackgroundScheduler()

        async def noop() -> None:
            pass

        task = PeriodicTask(name="test", func=noop, interval_seconds=60)
        scheduler.register(task)
        assert task in scheduler._tasks

    @pytest.mark.asyncio
    async def test_start_runs_task_immediately_when_run_on_start(self) -> None:
        call_count = 0

        async def increment() -> None:
            nonlocal call_count
            call_count += 1

        scheduler = BackgroundScheduler()
        scheduler.register(PeriodicTask(name="inc", func=increment, interval_seconds=100, run_on_start=True))

        await scheduler.start()
        # Give the task time to execute
        await asyncio.sleep(0.05)
        await scheduler.stop()

        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_start_skips_immediate_run_when_run_on_start_false(self) -> None:
        call_count = 0

        async def increment() -> None:
            nonlocal call_count
            call_count += 1

        scheduler = BackgroundScheduler()
        scheduler.register(PeriodicTask(name="inc", func=increment, interval_seconds=100, run_on_start=False))

        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

        assert call_count == 0

    @pytest.mark.asyncio
    async def test_periodic_execution(self) -> None:
        call_count = 0

        async def increment() -> None:
            nonlocal call_count
            call_count += 1

        scheduler = BackgroundScheduler()
        scheduler.register(PeriodicTask(name="fast", func=increment, interval_seconds=0.05, run_on_start=True))

        await scheduler.start()
        # run_on_start=1 call + wait for ~2 intervals
        await asyncio.sleep(0.18)
        await scheduler.stop()

        # Should have run at least 3 times (initial + 2 intervals)
        assert call_count >= 3

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self) -> None:
        scheduler = BackgroundScheduler()

        async def noop() -> None:
            pass

        scheduler.register(PeriodicTask(name="noop", func=noop, interval_seconds=1))

        await scheduler.start()
        assert len(scheduler._running) == 1

        await scheduler.stop()
        assert len(scheduler._running) == 0

    @pytest.mark.asyncio
    async def test_task_error_does_not_crash_loop(self) -> None:
        call_count = 0

        async def failing_task() -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("intentional failure")

        scheduler = BackgroundScheduler()
        scheduler.register(PeriodicTask(name="fail", func=failing_task, interval_seconds=0.05, run_on_start=True))

        await scheduler.start()
        await asyncio.sleep(0.18)
        await scheduler.stop()

        # Should have been called multiple times despite raising each time
        assert call_count >= 3

    @pytest.mark.asyncio
    async def test_multiple_tasks_run_independently(self) -> None:
        calls_a = 0
        calls_b = 0

        async def task_a() -> None:
            nonlocal calls_a
            calls_a += 1

        async def task_b() -> None:
            nonlocal calls_b
            calls_b += 1

        scheduler = BackgroundScheduler()
        scheduler.register(PeriodicTask(name="a", func=task_a, interval_seconds=0.05, run_on_start=True))
        scheduler.register(PeriodicTask(name="b", func=task_b, interval_seconds=0.05, run_on_start=True))

        await scheduler.start()
        assert len(scheduler._running) == 2
        await asyncio.sleep(0.12)
        await scheduler.stop()

        assert calls_a >= 2
        assert calls_b >= 2

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self) -> None:
        scheduler = BackgroundScheduler()
        # Should not raise
        await scheduler.stop()
        assert scheduler._running == []

    @pytest.mark.asyncio
    async def test_task_error_is_logged(self, caplog) -> None:
        async def bad_task() -> None:
            raise RuntimeError("boom")

        scheduler = BackgroundScheduler()
        scheduler.register(PeriodicTask(name="boom", func=bad_task, interval_seconds=100, run_on_start=True))

        with caplog.at_level("ERROR", logger="app.utils.scheduler"):
            await scheduler.start()
            await asyncio.sleep(0.05)
            await scheduler.stop()

        assert "boom" in caplog.text
        assert "Periodic task 'boom' failed" in caplog.text
