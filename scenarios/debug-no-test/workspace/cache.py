"""Fixed-capacity LRU cache."""

from __future__ import annotations

from typing import Any


class LRUCache:
    """Least-Recently-Used cache holding up to ``capacity`` (key, value) pairs.

    Behavior:
      * The cache holds AT MOST ``capacity`` items at any time. Storing
        ``capacity`` items is fine; only the (capacity + 1)-th put causes
        an eviction.
      * On ``get(key)`` for a present key, the entry moves to the
        most-recently-used end. Returns the value.
      * On ``get(key)`` for an absent key, returns ``None``.
      * On ``put(key, value)`` when the key is present, the existing entry
        is replaced and moved to the most-recently-used end.
      * On ``put(key, value)`` when the key is new, the entry is appended
        at the most-recently-used end. If this brings the size above
        capacity, the least-recently-used entry is evicted.
      * ``self._items`` is ordered LRU -> MRU
        (so ``self._items[0]`` is the next eviction candidate).
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._items: list[tuple[str, Any]] = []

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str) -> Any:
        for i, (k, v) in enumerate(self._items):
            if k == key:
                self._items.pop(i)
                self._items.append((k, v))
                return v
        return None

    def put(self, key: str, value: Any) -> None:
        for i, (k, _) in enumerate(self._items):
            if k == key:
                self._items.pop(i)
                break
        self._items.append((key, value))
        if len(self._items) >= self.capacity:
            self._items.pop(0)
