from __future__ import annotations

from functools import partial
import math
import time
from typing import Optional

import sublime

from .runtime import enqueue_on_ui
from .ui__busy_spinner import BusyIndicators


STATUS_KEY = "git_savvy.git_activity"
SHOW_AFTER_MS = 5000
FRAME_INTERVAL_MS = 200

# Lifecycle admission is shared with process observers. Capture this identity
# when enqueueing, so work from before shutdown cannot enter a later session.
_session: Optional[object] = None

# All remaining state belongs to the UI thread. Slow commands are a subset of
# running commands, not a separate lifecycle stage.
_running: set[int] = set()
_slow: set[int] = set()
_view: Optional[sublime.View] = None
_frame = 0
_tick_pending = False


def initialize() -> None:
    global _session
    _session = object()


def start(execution_id: int) -> None:
    session = _session
    if session is not None:
        deadline = time.monotonic() + SHOW_AFTER_MS / 1000
        enqueue_on_ui(_start, execution_id, deadline, session)


def finish(execution_id: int) -> None:
    enqueue_on_ui(_finish, execution_id)


def refresh() -> None:
    """On the UI thread, move the current frame to the globally active view."""
    window = sublime.active_window()
    view = window.active_view() if window and _slow else None
    _move_to(view)
    if _view:
        indicator = BusyIndicators.ROLLING[_frame % len(BusyIndicators.ROLLING)]
        _view.set_status(
            STATUS_KEY,
            "{} · Git is running (open Git Command Log for details)".format(indicator)
        )


def clear_view(view: sublime.View) -> None:
    """On the UI thread, clear the status from a deactivated or closing view."""
    if view == _view:
        _move_to(None)


def shutdown() -> None:
    global _session
    _session = None  # Close admission before queueing UI cleanup.
    enqueue_on_ui(_reset)


def _start(execution_id: int, deadline: float, session: object) -> None:
    if session is not _session:
        return
    _running.add(execution_id)
    delay = max(0, math.ceil((deadline - time.monotonic()) * 1000))
    sublime.set_timeout(partial(_reveal, execution_id, session), delay)


def _finish(execution_id: int) -> None:
    _running.discard(execution_id)
    _slow.discard(execution_id)
    if not _slow:
        _move_to(None)
    # Leave a pending tick in place: another reveal can reuse it. Otherwise it
    # will see no slow commands and stop without scheduling a successor.


def _reveal(execution_id: int, session: object) -> None:
    if session is not _session or execution_id not in _running:
        return
    _slow.add(execution_id)
    if not _tick_pending:
        _tick(session)


def _tick(session: object) -> None:
    global _frame, _tick_pending
    if session is not _session:
        return
    _tick_pending = False
    if not _slow:
        return
    refresh()
    _frame += 1
    _tick_pending = True
    sublime.set_timeout(partial(_tick, session), FRAME_INTERVAL_MS)


def _move_to(view: Optional[sublime.View]) -> None:
    global _view
    if view and not view.is_valid():
        view = None
    if view != _view:
        if _view and _view.is_valid():
            _view.erase_status(STATUS_KEY)
        _view = view


def _reset() -> None:
    global _frame, _tick_pending
    _tick_pending = False
    _running.clear()
    _slow.clear()
    _frame = 0
    _move_to(None)
