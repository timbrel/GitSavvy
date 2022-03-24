import functools
import json
import pprint as _pprint
import threading


MYPY = False
if MYPY:
    from typing import Dict, List, Optional, Sequence, Union
    LogEntry = Dict


# Preserve state of `enabled` during hot-reloads
try:
    enabled  # type: ignore[has-type]
except NameError:
    enabled = False

_log = []  # type: List[LogEntry]
ENCODING_NOT_UTF8 = "{} was sent as binaries and we dont know the encoding, not utf-8"


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


@functools.lru_cache(maxsize=1)
def print_cwd_change(cwd, left_space):
    # type: (str, int) -> None
    print('\n', ' ' * left_space, '  [{}]'.format(cwd))


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
            cmd=' '.join(['git'] + list(filter(None, command))),
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


def dump_var(name, value, width=79, end='\n', **kwargs):
    is_str = isinstance(value, str)

    prefix = "{}{}".format(name, ': ' if is_str else '=')
    line_prefix = end + ' ' * len(prefix)
    if not is_str:
        value = _pprint.pformat(value, width=max(49, width - len(prefix)))

    print(prefix + line_prefix.join(value.splitlines()), end=end, **kwargs)


def dump(*args, **kwargs):
    for i, arg in enumerate(args):
        dump_var("_arg{}".format(i), arg)
    for name, arg in sorted(kwargs.items()):
        dump_var(name, arg)


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
