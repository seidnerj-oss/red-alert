"""
Unified service orchestrator for alert inputs and outputs.

Routes alert events from multiple inputs (HFC API, CBS) to multiple outputs
(UniFi LEDs, Telegram, etc.) through a central event bus. Each output maintains
its own state tracking with per-source hold timers and area filtering.

Architecture:
    Orchestrator (single asyncio event loop)
      |-- Inputs: produce AlertEvents (HFC poll, CBS push)
      |-- Outputs: consume AlertEvents, own their state tracking
      |-- Event routing: fan-out from inputs to all outputs
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from red_alert.core.state import AlertState, AlertStateTracker

logger = logging.getLogger('red_alert.orchestrator')

STATE_SEVERITY: dict[AlertState, int] = {
    AlertState.ROUTINE: 0,
    AlertState.ALL_CLEAR: 1,
    AlertState.PRE_ALERT: 2,
    AlertState.ALERT: 3,
}


@dataclass
class AlertEvent:
    """Produced by inputs when they detect alert data."""

    source: str
    state: AlertState
    data: dict | None = None
    timestamp: float = field(default_factory=time.monotonic)
    alert_time: float | None = None


class AlertInput(ABC):
    """Base class for alert sources."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def run(self, emit: Callable[[AlertEvent], Awaitable[None]]) -> None:
        """Run the input loop. Call emit() on each event. Runs until cancelled."""

    async def stop(self) -> None:
        """Optional cleanup."""


class AlertOutput(ABC):
    """Base class for alert consumers."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def start(self) -> None:
        """Initialize (connect to controller, etc.)."""

    @abstractmethod
    async def handle_event(self, event: AlertEvent) -> None:
        """Process an alert event from any input."""

    async def stop(self) -> None:
        """Optional cleanup."""


class MultiSourceStateTracker:
    """Tracks each input source separately, merges via max severity.

    Each output owns one of these. Different outputs can monitor different
    areas with different hold timers, each maintaining independent per-source
    state tracking.
    """

    def __init__(
        self,
        areas_of_interest: list[str] | None = None,
        hold_seconds: dict[str, float] | None = None,
        logger: logging.Logger | None = None,
    ):
        self._areas = areas_of_interest
        self._hold = hold_seconds
        self._logger = logger
        self._trackers: dict[str, AlertStateTracker] = {}
        self._merged_state = AlertState.ROUTINE
        self._merged_data: dict | None = None

    @property
    def state(self) -> AlertState:
        return self._merged_state

    @property
    def alert_data(self) -> dict | None:
        return self._merged_data

    def _get_tracker(self, source: str) -> AlertStateTracker:
        if source not in self._trackers:
            child_logger = self._logger.getChild(source) if self._logger else None
            self._trackers[source] = AlertStateTracker(
                areas_of_interest=self._areas,
                hold_seconds=self._hold,
                logger=child_logger,
            )
        return self._trackers[source]

    def update(self, event: AlertEvent) -> tuple[AlertState, AlertState]:
        """Update the source's tracker and recompute merged state.

        Also pokes all other source trackers with empty data so their hold
        timers are checked. Without this, push-based sources (CBS) that stop
        emitting events would never have their holds expire.

        Returns:
            Tuple of (old_merged_state, new_merged_state).
        """
        old_merged = self._merged_state

        tracker = self._get_tracker(event.source)
        tracker.update(event.data, alert_time=event.alert_time)

        for source, t in self._trackers.items():
            if source != event.source:
                t.update(None)

        new_merged = AlertState.ROUTINE
        merged_data = None
        for t in self._trackers.values():
            if STATE_SEVERITY[t.state] > STATE_SEVERITY[new_merged]:
                new_merged = t.state
                merged_data = t.alert_data

        self._merged_state = new_merged
        self._merged_data = merged_data
        return old_merged, new_merged

    def get_source_state(self, source: str) -> AlertState:
        if source in self._trackers:
            return self._trackers[source].state
        return AlertState.ROUTINE


class PeriodicTask:
    """A named coroutine that runs on a fixed interval."""

    def __init__(self, name: str, interval: float, coro):
        self.name = name
        self.interval = interval
        self.coro = coro

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.coro()
            except Exception:
                logger.debug('Periodic task %s failed', self.name, exc_info=True)


class Orchestrator:
    """Routes alert events from inputs to outputs."""

    def __init__(
        self,
        inputs: list[AlertInput],
        outputs: list[AlertOutput],
        periodic_tasks: list[PeriodicTask] | None = None,
    ):
        self._inputs = inputs
        self._outputs = outputs
        self._periodic_tasks = periodic_tasks or []

    async def run(self) -> None:
        for output in self._outputs:
            try:
                await output.start()
                logger.info('Started output: %s', output.name)
            except Exception:
                logger.exception('Failed to start output: %s', output.name)
                raise

        async def emit(event: AlertEvent) -> None:
            results = await asyncio.gather(
                *(self._deliver(output, event) for output in self._outputs),
                return_exceptions=True,
            )
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error('Output %s failed on event from %s: %s', self._outputs[i].name, event.source, result)

        input_tasks = [asyncio.create_task(inp.run(emit), name=f'input-{inp.name}') for inp in self._inputs]
        bg_tasks = [asyncio.create_task(pt.run(), name=f'periodic-{pt.name}') for pt in self._periodic_tasks]

        logger.info(
            'Orchestrator running: %d input(s), %d output(s), %d periodic task(s)', len(self._inputs), len(self._outputs), len(self._periodic_tasks)
        )

        try:
            done, _ = await asyncio.wait(input_tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.exception():
                    logger.error('Input %s failed: %s', task.get_name(), task.exception())
        finally:
            for task in input_tasks + bg_tasks:
                task.cancel()
            await asyncio.gather(*input_tasks, *bg_tasks, return_exceptions=True)
            for inp in self._inputs:
                try:
                    await inp.stop()
                except Exception:
                    logger.debug('Error stopping input %s', inp.name, exc_info=True)
            for output in self._outputs:
                try:
                    await output.stop()
                except Exception:
                    logger.debug('Error stopping output %s', output.name, exc_info=True)

    @staticmethod
    async def _deliver(output: AlertOutput, event: AlertEvent) -> None:
        await output.handle_event(event)
