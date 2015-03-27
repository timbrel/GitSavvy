import re
from difflib import SequenceMatcher
from collections import namedtuple

Change = namedtuple("Change", (
    "type",
    "old_start",
    "old_end",
    "new_start",
    "new_end"
    ))
REPLACE = "replace"
DELETE = "delete"
INSERT = "insert"


boundary = re.compile(r"(\W)")


def get_indices(chunks):
    idx = 0
    indices = []
    for chunk in chunks:
        indices.append(idx)
        idx += len(chunk)
    indices.append(idx)
    return indices


def get_changes(old, new):
    old_chunks = tuple(filter(lambda x: x, boundary.split(old)))
    new_chunks = tuple(filter(lambda x: x, boundary.split(new)))
    old_indices = get_indices(old_chunks)
    new_indices = get_indices(new_chunks)

    matcher = SequenceMatcher(a=old_chunks, b=new_chunks, autojunk=False)

    if matcher.quick_ratio() < 0.85:
        return []

    return [Change(change_type, old_indices[os], old_indices[oe], new_indices[ns], new_indices[ne])
            for change_type, os, oe, ns, ne in matcher.get_opcodes()
            if not change_type == "equal"]
