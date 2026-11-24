"""Read HEAD from a reftable stack without starting Git.

Only the record named "HEAD" is looked up.  It sorts before every "refs/"
name, so it lives in the first ref block of a table, or in the next one
when uppercase root refs such as "AUTO_MERGE" fill the first block.

See Git's Documentation/technical/reftable.adoc for the format.
"""
from __future__ import annotations

import os

from typing import BinaryIO


HEAD = b"HEAD"
HASH_LENGTHS = {b"sha1": 20, b"s256": 32}
MAX_BLOCK_LENGTH = 0xFFFFFF


class ReftableError(Exception):
    """A table holds data this reader does not understand."""


def read_head(stack_dir: str) -> str:
    """Return HEAD as a loose HEAD file would spell it.

    That is "ref: <target>" for a symbolic HEAD and a hex object ID for a
    detached one.  Like an unreadable loose HEAD file, return "" when the
    stack is missing, has no HEAD, or holds data this reader doesn't know.
    """
    # Compaction replaces tables after rewriting tables.list.  A table
    # vanishing between both reads means we hold a stale list.
    for _ in range(3):
        try:
            with open(os.path.join(stack_dir, "tables.list"), encoding="utf-8") as f:
                tables = f.read().split()
        except (OSError, UnicodeError):
            return ""
        try:
            return _read_head_from_tables(stack_dir, tables)
        except OSError:
            # Missing, or on Windows still pending deletion.
            continue
        except ReftableError:
            return ""
    return ""


def _read_head_from_tables(stack_dir: str, tables: list[str]) -> str:
    # Newer tables shadow older ones; tables.list is ordered oldest first.
    for name in reversed(tables):
        with open(os.path.join(stack_dir, name), "rb") as f:
            try:
                found, value = _find_head(f)
            except (IndexError, UnicodeError) as error:
                raise ReftableError(f"malformed reftable {name}: {error}") from None
        if found:
            return value
    return ""


def _find_head(f: BinaryIO) -> tuple[bool, str]:
    """Return (found, value).  A deletion record is found with value ""."""
    header = f.read(28)
    if header[:4] != b"REFT":
        raise ReftableError("not a reftable")
    version = header[4]
    if version == 1:
        header_length, hash_length = 24, 20
    elif version == 2 and header[24:28] in HASH_LENGTHS:
        header_length, hash_length = 28, HASH_LENGTHS[header[24:28]]
    else:
        raise ReftableError(f"unsupported reftable version {version}")
    block_size = int.from_bytes(header[5:8], "big")

    # The first block shares its offsets with the file header.
    position, type_offset = 0, header_length
    while True:
        f.seek(position + type_offset)
        block_header = f.read(4)
        if block_header[:1] != b"r":
            # Logs, indexes, the footer, or EOF: no further ref records.
            return False, ""
        block_length = int.from_bytes(block_header[1:4], "big")
        records_start = type_offset + 4
        if not records_start + 2 <= block_length <= (block_size or MAX_BLOCK_LENGTH):
            raise ReftableError(f"invalid ref block length {block_length}")
        f.seek(position)
        block = f.read(block_length)
        if len(block) != block_length:
            raise ReftableError("truncated ref block")

        found, value, passed = _scan_ref_block(block, records_start, hash_length)
        if found or passed:
            return found, value

        # Git pads ref blocks to `block_size` only when another ref block
        # follows; padding is NUL while block types are letters.
        f.seek(position + block_length)
        padded = block_size > 0 and f.read(1) == b"\0"
        position += block_size if padded else block_length
        type_offset = 0


def _scan_ref_block(block: bytes, offset: int, hash_length: int) -> tuple[bool, str, bool]:
    """Return (found, value, passed) where passed means HEAD sorts earlier."""
    restart_count = int.from_bytes(block[-2:], "big")
    records_end = len(block) - 2 - 3 * restart_count
    if records_end < offset:
        raise ReftableError("invalid restart table")

    name = b""
    while offset < records_end:
        prefix_length, offset = _varint(block, offset)
        suffix_info, offset = _varint(block, offset)
        suffix_length, value_type = suffix_info >> 3, suffix_info & 7
        if prefix_length > len(name) or offset + suffix_length > records_end:
            raise ReftableError("invalid ref record name")
        name = name[:prefix_length] + block[offset:offset + suffix_length]
        offset += suffix_length
        _, offset = _varint(block, offset)  # update_index_delta

        if name > HEAD:
            return False, "", True

        if value_type == 0:
            value = ""
        elif value_type in (1, 2):
            value = block[offset:offset + hash_length].hex()
            offset += hash_length * value_type
        elif value_type == 3:
            target_length, offset = _varint(block, offset)
            value = "ref: " + block[offset:offset + target_length].decode("utf-8")
            offset += target_length
        else:
            raise ReftableError(f"unknown ref value type {value_type}")
        if offset > records_end:
            raise ReftableError("truncated ref record")

        if name == HEAD:
            return True, value, True
    return False, "", False


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    byte = data[offset]
    offset += 1
    value = byte & 0x7F
    while byte & 0x80:
        byte = data[offset]
        offset += 1
        value = ((value + 1) << 7) | (byte & 0x7F)
    return value, offset
