"""Opt-in timing hooks used only by the local synthetic load benchmark."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter


_active_stages = ContextVar("synthetic_load_stages", default=None)


@contextmanager
def capture_stages():
    timings = {}
    token = _active_stages.set(timings)
    try:
        yield timings
    finally:
        _active_stages.reset(token)


@contextmanager
def measure_stage(name):
    timings = _active_stages.get()
    if timings is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        timings[name] = timings.get(name, 0.0) + (perf_counter() - started) * 1000

