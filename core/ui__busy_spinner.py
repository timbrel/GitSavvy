from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

import sublime


__all__ = (
    "BusyIndicators",
    "BusyIndicatorConfig",
    "busy_indicator",
    "start_busy_indicator",
    "stop_busy_indicator",
)


class BusyIndicators:
    ROLLING = ["◐", "◓", "◑", "◒"]
    STARS = ["◇", "◈", "◆"]
    BRAILLE = "⣷⣯⣟⡿⢿⣻⣽⣾"


@dataclass
class BusyIndicatorConfig:
    start_after: float
    timeout_after: float
    cycle_time: int
    indicators: Sequence[str]


STATUS_BUSY_KEY = "gitsavvy-x-is-busy"
running_busy_indicators: Dict[Tuple[sublime.View, str], BusyIndicatorConfig] = {}


@contextmanager
def busy_indicator(view: sublime.View, status_key: str = STATUS_BUSY_KEY, **options):
    start_busy_indicator(view, status_key, **options)
    try:
        yield
    finally:
        stop_busy_indicator(view, status_key)


def start_busy_indicator(
    view: sublime.View,
    status_key: str = STATUS_BUSY_KEY,
    *,
    start_after: float = 2.0,  # [seconds]
    timeout_after: float = 120.0,  # [seconds]
    cycle_time: int = 200,  # [milliseconds]
    indicators: Sequence[str] = BusyIndicators.ROLLING
) -> None:
    key = (view, status_key)
    is_running = key in running_busy_indicators
    config = BusyIndicatorConfig(start_after, timeout_after, cycle_time, indicators)
    running_busy_indicators[key] = config
    if not is_running:
        _busy_indicator(view, status_key, time.time())


def stop_busy_indicator(view: sublime.View, status_key: str = STATUS_BUSY_KEY) -> None:
    try:
        running_busy_indicators.pop((view, status_key))
    except KeyError:
        pass


def _busy_indicator(view: sublime.View, status_key: str, start_time: float) -> None:
    try:
        config = running_busy_indicators[(view, status_key)]
    except KeyError:
        view.erase_status(status_key)
        return

    now = time.time()
    elapsed = now - start_time
    if config.start_after <= elapsed < config.timeout_after:
        num = len(config.indicators)
        text = config.indicators[int(elapsed * 1000 / config.cycle_time) % num]
        view.set_status(status_key, text)
    else:
        view.erase_status(status_key)

    if elapsed < config.timeout_after and view.is_valid():
        sublime.set_timeout(
            lambda: _busy_indicator(view, status_key, start_time),
            config.cycle_time
        )
    else:
        stop_busy_indicator(view, status_key)
