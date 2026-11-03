from __future__ import annotations

import json
import shlex
import sys
import threading

from .fns import filter_

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


LogEntry = Dict[str, Any]

# Preserve state of `enabled` during hot-reloads.
enabled: bool = globals().get("enabled", False)

_log: List[Any] = []
ENCODING_NOT_UTF8 = "{} was sent as binaries and we dont know the encoding, not utf-8"
last_working_dir = ""


def start_logging() -> None:
    global _log
    from . import worker_occupancy

    _log = []
    set_logging_enabled(True)
    worker_occupancy.enable()


def stop_logging() -> None:
    from . import worker_occupancy

    was_recording = worker_occupancy.enabled
    set_logging_enabled(False)
    worker_occupancy.disable()
    if was_recording:
        try:
            worker_occupancy.export_to_user()
        except OSError as error:
            print("GitSavvy: could not export worker occupancy: {}".format(error))


def set_logging_enabled(value: bool) -> None:
    global enabled
    enabled = value
    if pkg := sys.modules.get("GitSavvy"):
        setattr(pkg, "_debug_enabled", value)


def get_log() -> str:
    return json.dumps(_log, indent=2)


def add_to_log(obj: Any) -> None:
    if enabled:
        _log.append(obj)


def make_log_message(_type: str, **kwargs: Any) -> LogEntry:
    """Create a log message dictionary for the JSON-formatted debug log."""
    message = {"type": _type}
    message.update(kwargs)
    return message


def dprint(*args: Any, **kwargs: Any) -> None:
    if enabled:
        print(*args, **kwargs)


def print_cwd_change(cwd: str, left_space: int) -> None:
    global last_working_dir
    if cwd != last_working_dir:
        last_working_dir = cwd
        print('\n', ' ' * left_space, '  [{}]'.format(cwd))


def pretty_git_command(args: Sequence[Optional[str]]) -> str:
    return ' '.join(['git'] + list(map(_quote, filter_(args))))


def log_git(
    command: Sequence[Optional[str]],
    cwd: str,
    stdin: Optional[Union[str, bytes]],
    stdout: Optional[Union[str, bytes]],
    stderr: Optional[Union[str, bytes]],
    seconds: float
) -> None:
    """Add git command details to the debug log."""
    if enabled:
        pre_info = "({thread}) [{runtime:3.0f}ms]".format(
            thread=threading.current_thread().name[0],
            runtime=seconds * 1000,
        )
        print_cwd_change(cwd, left_space=len(pre_info))
        print(' {pre_info} $ {cmd}'.format(
            pre_info=pre_info,
            cmd=pretty_git_command(command)
        ))

    message = make_log_message(
        'git', command=command, stdin=stdin, stdout=stdout, stderr=stderr,
        seconds=seconds
    )
    for field in ['stdin', 'stdout', 'stderr']:
        if isinstance(locals()[field], bytes):
            message[field] = try_to_decode(locals()[field], field)
    add_to_log(message)


def log_process(command: Any, cwd: str, env: Any, startupinfo: Any) -> None:
    """Add Popen call details to the debug log."""
    message = make_log_message(
        'subprocess.Popen', command=command, cwd=cwd, env=env,
        startupinfo=startupinfo
    )
    add_to_log(message)


def try_to_decode(message: bytes, name: str) -> Union[Tuple[str], str]:
    try:
        return message.decode(),
    except UnicodeDecodeError:
        return ENCODING_NOT_UTF8.format(name)


def log_error(err: object) -> None:
    add_to_log({
        "type": "error",
        "error": repr(err)
    })


def _quote(arg: str) -> str:
    return "=".join(map(shlex.quote, arg.split("=")))
