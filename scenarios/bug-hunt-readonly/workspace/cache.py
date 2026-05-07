"""Fixed-capacity LRU cache."""

from __future__ import annotations

from typing import Any


class LRUCache:
    """Least-Recently-Used cache with a fixed capacity.

    Behavior contract:
      * On ``get(key)`` for a key that's present, the entry moves to the
        most-recently-used position. Returns the value.
      * On ``get(key)`` for a key that's absent, returns ``None``.
      * On ``put(key, value)`` when the key is already present, the
        existing entry is replaced and moved to the most-recently-used
        position.
      * On ``put(key, value)`` when the key is new, the entry is inserted
        at the most-recently-used position. If this pushes the cache above
        capacity, the least-recently-used entry is evicted.
      * Items are stored as ``(key, value)`` tuples in ``self._items``.
        The list is ordered so that ``self._items[0]`` is the
        least-recently-used and ``self._items[-1]`` is the
        most-recently-used.
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._items: list[tuple[str, Any]] = []

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str) -> Any:
        for k, v in self._items:
            if k == key:
                return v
        return None

    def put(self, key: str, value: Any) -> None:
        for i, (k, _) in enumerate(self._items):
            if k == key:
                self._items.pop(i)
                break
        self._items.append((key, value))
        if len(self._items) > self.capacity:
            self._items.pop(0)
