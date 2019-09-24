from contextlib import contextmanager
import functools
import json
import pprint as _pprint
import threading

from ...core.settings import GitSavvySettings


# Preserve state of `enabled` during hot-reloads
try:
    enabled
except NameError:
    enabled = False

_log = []
ENCODING_NOT_UTF8 = "{} was sent as binaries and we dont know the encoding, not utf-8"


def start_logging():
    global _log
    global enabled
    _log = []
    enabled = True


def stop_logging():
    global enabled
    enabled = False


@contextmanager
def disable_logging():
    global enabled
    previous_state = enabled
    enabled = False
    try:
        yield
    finally:
        enabled = previous_state


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


def log_git(command, stdin, stdout, stderr, seconds):
    """ Add git command details to debug log """
    global enabled
    if enabled:
        print(' ({thread}) [{runtime:3.0f}ms] $ {cmd}'.format(
            thread=threading.current_thread().name[0],
            cmd=' '.join(['git'] + list(filter(None, command))),
            runtime=seconds * 1000,
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


def log_on_exception(fn):
    def wrapped_fn(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception as e:
            add_to_log({
                "type": "exception",
                "exception": repr(e)
            })
            raise e


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


# backward-compatibility
def pprint(*args, **kwargs):
    """
    Pretty print since we can not use debugger
    """
    dump(*args, **kwargs)


def get_trace_tags():
    savvy_settings = GitSavvySettings()
    if savvy_settings.get("dev_mode"):
        return savvy_settings.get("dev_trace", [])
    else:
        return []


def trace(*args, tag="debug", fill=None, fill_width=60, **kwargs):
    """
    Lightweight logging facility. Provides simple print-like interface with
    filtering by tags and pretty-printed captions for delimiting output
    sections.

    See the "dev_trace" setting for possible values of the "tag" keyword.
    """
    if tag not in get_trace_tags():
        return

    if fill is not None:
        sep = str(kwargs.get('sep', ' '))
        caption = sep.join(args)
        args = "{0:{fill}<{width}}".format(caption and caption + sep,
                                           fill=fill, width=fill_width),
    print("GS [{}]".format(tag), *args, **kwargs)


def trace_for_tag(tag):
    return functools.partial(trace, tag=tag)


trace.for_tag = trace_for_tag


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
