import json


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
