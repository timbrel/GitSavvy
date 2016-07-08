import functools
import json
import pprint as _pprint

import sublime


_log = []
enabled = False


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


def log_git(command, stdin, stdout, stderr):
    add_to_log({
        "type": "git",
        "command": command,
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr
        })


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
    line_prefix = end + ' '*len(prefix)
    if not is_str:
        value = _pprint.pformat(value, width=max(49, width-len(prefix)))

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
    savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
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
