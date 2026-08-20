from __future__ import annotations
import json
import os
import threading
import traceback

import sublime


from typing import Any


SAVE_DELAY = 1000
STATE_FILE = "GitSavvy-app-state.json"


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
            state_path = _state_path()
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            temporary_path = state_path + ".tmp"
            with open(temporary_path, "w", encoding="utf-8") as file:
                file.write(contents)
                file.write("\n")
            os.replace(temporary_path, state_path)
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
        with open(_state_path(), encoding="utf-8") as file:
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


def _state_path() -> str:
    return os.path.join(sublime.cache_path(), "GitSavvy", STATE_FILE)


_lock = threading.Lock()
_save_lock = threading.Lock()
_save_scheduled = False
_repo_state_pruned = False
_state = _load()
