"""Thread-safe lazily-built instance attribute (Phase 1T).

``functools.cached_property`` only locks on Python 3.11; from 3.12 it does not,
so two threads that miss at the same time would each construct the value. The
provider clients are built both by the start-up warm-up thread and, if a
request arrives first, by the event-loop thread, so construction must happen
exactly once on every interpreter.

It is a *non-data* descriptor: once built the value lives in the instance
``__dict__`` and the descriptor is no longer consulted, and ``obj.attr = x``
(tests, fakes) simply replaces it, exactly like ``cached_property``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class LazyAttribute(Generic[T]):
    def __init__(self, factory: Callable[[Any], T]) -> None:
        self.func = factory  # name kept for parity with cached_property
        self.attr_name = factory.__name__
        self.__doc__ = factory.__doc__
        self._lock = threading.RLock()

    def __set_name__(self, owner: type, name: str) -> None:
        self.attr_name = name

    def __get__(self, instance: Any, owner: type | None = None) -> T:
        if instance is None:
            return self
        cache = instance.__dict__
        try:
            return cache[self.attr_name]
        except KeyError:
            pass
        with self._lock:
            if self.attr_name not in cache:  # re-check: another thread may have won
                cache[self.attr_name] = self.func(instance)
            return cache[self.attr_name]
