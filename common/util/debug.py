import json
import shlex
import threading

from GitSavvy.core.fns import filter_


from typing import Dict, List, Optional, Sequence, Union
LogEntry = Dict


# Preserve state of `enabled` during hot-reloads
try:
    enabled  # type: ignore[used-before-def]
except NameError:
    enabled = False

_log = []  # type: List[LogEntry]
ENCODING_NOT_UTF8 = "{} was sent as binaries and we dont know the encoding, not utf-8"
last_working_dir = ""


def start_logging():
    global _log
    global enabled
    _log = []
    enabled = True


def stop_logging():
    global enabled
    enabled = False


def get_log():
    return json.dumps(_log, indent=2)


def add_to_log(obj):
    if enabled:
        _log.append(obj)


def make_log_message(_type, **kwargs):
    """
    Create a log message dictionary to be stored in JSON formatted debug log
    """
    message = {"type": _type}
    message.update(kwargs)

    return message


def dprint(*args, **kwargs):
    global enabled
    if enabled:
        print(*args, **kwargs)


def print_cwd_change(cwd, left_space):
    # type: (str, int) -> None
    global last_working_dir
    if cwd != last_working_dir:
        last_working_dir = cwd
        print('\n', ' ' * left_space, '  [{}]'.format(cwd))


def pretty_git_command(args):
    # type: (Sequence[Optional[str]]) -> str
    return ' '.join(['git'] + list(map(_quote, filter_(args))))


def _quote(arg):
    # type: (str) -> str
    return "=".join(map(shlex.quote, arg.split("=")))


def log_git(
    command,  # type: Sequence[Optional[str]]
    cwd,      # type: str
    stdin,    # type: Optional[Union[str, bytes]]
    stdout,   # type: Optional[Union[str, bytes]]
    stderr,   # type: Optional[Union[str, bytes]]
    seconds   # type: float
):
    # type: (...) -> None
    """ Add git command details to debug log """
    global enabled
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
        if isinstance(locals()[field], bytes):  # decode standard I/O bytes
            message[field] = try_to_decode(locals()[field], field)
    add_to_log(message)


def log_process(command, cwd, env, startupinfo):
    """ Add Popen call details to debug log """
    message = make_log_message(
        'subprocess.Popen', command=command, cwd=cwd, env=env,
        startupinfo=startupinfo
    )
    add_to_log(message)


def try_to_decode(message, name):
    try:
        return message.decode(),
    except UnicodeDecodeError:
        return ENCODING_NOT_UTF8.format(name)


def log_error(err):
    add_to_log({
        "type": "error",
        "error": repr(err)
    })


class StackMeter:
    """Reentrant context manager counting the reentrancy depth."""

    def __init__(self, depth=0):
        super().__init__()
        self.depth = depth

    def __enter__(self):
        depth = self.depth
        self.depth += 1
        return depth

    def __exit__(self, *exc_info):
        self.depth -= 1
