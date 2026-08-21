from __future__ import annotations
import json
import os
import threading
import traceback

import sublime


from typing import Any


SAVE_DELAY = 1000
CACHE_PATH = os.path.join(sublime.cache_path(), "GitSavvy")
STATE_PATH = os.path.join(CACHE_PATH, "GitSavvy-app-state.json")
TEMP_PATH = STATE_PATH + ".tmp"

_lock = threading.Lock()
_save_lock = threading.Lock()
_save_scheduled = False
_repo_state_pruned = False
_state: dict[str, Any] = {}


def load() -> None:
    global _state
    with _lock:
        _state = _load()


def get(key: str, default: Any = None) -> Any:
    with _lock:
        return _state.get(key, default)


def set(key: str, value: Any) -> None:
    global _save_scheduled
    with _lock:
        _state[key] = value
        if _save_scheduled:
            return
        _save_scheduled = True

    sublime.set_timeout_async(save, SAVE_DELAY)


def save() -> None:
    global _save_scheduled
    with _save_lock:
        with _lock:
            _remove_stale_repo_state()
            try:
                contents = json.dumps(_state, ensure_ascii=False, indent=2, sort_keys=True)
            except (TypeError, ValueError):
                _save_scheduled = False
                traceback.print_exc()
                return
            _save_scheduled = False

        try:
            os.makedirs(CACHE_PATH, exist_ok=True)
            with open(TEMP_PATH, "w", encoding="utf-8") as file:
                file.write(contents)
                file.write("\n")
            os.replace(TEMP_PATH, STATE_PATH)
        except OSError:
            traceback.print_exc()


def _remove_stale_repo_state() -> None:
    global _repo_state_pruned
    if _repo_state_pruned:
        return
    _repo_state_pruned = True

    by_repo = _state.get("by_repo")
    if isinstance(by_repo, dict):
        _state["by_repo"] = {
            repo_path: repo_state
            for repo_path, repo_state in by_repo.items()
            if isinstance(repo_path, str) and os.path.isdir(repo_path)
        }


def _load() -> dict[str, Any]:
    try:
        with open(STATE_PATH, encoding="utf-8") as file:
            state = json.load(file)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        traceback.print_exc()
        return {}

    if isinstance(state, dict):
        return state

    print("GitSavvy: app state must be a JSON object; ignoring it.")
    return {}
