"""Generic JSON tree-walking helpers shared by the recommendation and comment
parsers. Kept in one place so there is exactly one implementation of "find
this key wherever it appears" to keep in sync with YouTube's payload shape."""

from collections.abc import Iterator
from typing import Any


def find_all_by_key(node: Any, key: str) -> Iterator[Any]:
    """Yield the value of every occurrence of `key` in any dict found while
    walking `node` depth-first, in encounter order."""
    if isinstance(node, dict):
        if key in node:
            yield node[key]
        for value in node.values():
            yield from find_all_by_key(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from find_all_by_key(item, key)


def find_first_by_key(node: Any, key: str) -> Any | None:
    for value in find_all_by_key(node, key):
        return value
    return None
