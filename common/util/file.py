from contextlib import contextmanager
import os

import sublime

from GitSavvy.core.fns import maybe
from typing import Dict


syntax_file_map = {}  # type: Dict[str, str]


def guess_syntax_for_file(window, filename):
    # type: (sublime.Window, str) -> str
    view = window.find_open_file(filename)
    if view:
        syntax = view.settings().get("syntax")
        remember_syntax_choice(filename, syntax)
        return syntax
    return get_syntax_for_file(filename)


def remember_syntax_choice(filename, syntax):
    # type: (str, str) -> None
    syntax_file_map[get_file_extension(filename) or filename] = syntax


def get_syntax_for_file(filename, default="Packages/Text/Plain text.tmLanguage"):
    # type: (str, str) -> str
    return (
        syntax_file_map.get(filename)
        or syntax_file_map.get(get_file_extension(filename))
        or maybe(lambda: sublime.find_syntax_for_file(filename).path)  # type: ignore[union-attr]
        or default
    )


def get_file_extension(filename):
    # type: (str) -> str
    return os.path.splitext(filename)[1][1:]


def get_file_contents_binary(repo_path, file_path):
    """
    Given an absolute file path, return the binary contents of that file
    as a string.
    """
    file_path = os.path.join(repo_path, file_path)
    with safe_open(file_path, "rb") as f:
        binary = f.read()
        binary = binary.replace(b"\r\n", b"\n")
        binary = binary.replace(b"\r", b"")
        return binary


def get_file_contents(repo_path, file_path):
    """
    Given an absolute file path, return the text contents of that file
    as a string.
    """
    binary = get_file_contents_binary(repo_path, file_path)
    try:
        return binary.decode('utf-8')
    except UnicodeDecodeError:
        return binary.decode('latin-1')


@contextmanager
def safe_open(filename, mode, *args, **kwargs):
    try:
        with open(filename, mode, *args, **kwargs) as file:
            yield file
    except PermissionError as e:
        sublime.ok_cancel_dialog("GitSavvy could not access file: \n{}".format(e))
        raise e
    except OSError as e:
        sublime.ok_cancel_dialog("GitSavvy encountered an OS error: \n{}".format(e))
        raise e
