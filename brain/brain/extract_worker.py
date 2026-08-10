"""Killable process boundary for document extraction.

Thread-backed extractors cannot be stopped safely when a PDF parser or helper
hangs. A short-lived spawned process gives TaskRuntime a child it can terminate
and reap without taking down the Brain.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
from contextlib import suppress
from pathlib import Path

from brain import extract
from brain.task_runtime import TaskStopped

_POLL_SECONDS = 0.05
_REAP_SECONDS = 0.25


def _extract_child(path: str, send) -> None:
    try:
        delay = float(os.environ.get("HALO_EXTRACT_STUB_DELAY", "0"))
        if delay > 0:
            time.sleep(delay)
        send.send(("ok", extract.extract_text(Path(path))))
    except BaseException as exc:  # child errors are data for the parent
        with suppress(Exception):
            send.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        send.close()


async def _stop_and_reap(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        await asyncio.to_thread(process.join, 0)
        return
    process.terminate()
    await asyncio.to_thread(process.join, _REAP_SECONDS)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join, _REAP_SECONDS)


async def extract_pdf_isolated(
    path: Path,
    cancelled: asyncio.Event,
    timeout: float = 60.0,
) -> str:
    """Extract one PDF with cooperative cancellation and a hard deadline."""
    target = path.resolve(strict=False)
    mp = multiprocessing.get_context("spawn")
    receive, send = mp.Pipe(duplex=False)
    process = mp.Process(
        target=_extract_child,
        args=(str(target), send),
        name=f"halo-extract-{target.stem[:32]}",
    )
    started = False
    deadline = time.monotonic() + timeout
    try:
        process.start()
        started = True
        send.close()
        while True:
            if cancelled.is_set():
                raise TaskStopped()
            if receive.poll():
                kind, payload = receive.recv()
                await asyncio.to_thread(process.join, _REAP_SECONDS)
                if kind == "ok":
                    return payload
                raise ValueError(f"could not extract {target.name}: {payload}")
            if not process.is_alive():
                await asyncio.to_thread(process.join, 0)
                raise ValueError(
                    f"could not extract {target.name}: worker exited without a result"
                )
            if time.monotonic() >= deadline:
                raise ValueError(
                    f"could not extract {target.name}: exceeded {timeout:g}s deadline"
                )
            await asyncio.sleep(_POLL_SECONDS)
    finally:
        if started:
            await _stop_and_reap(process)
        with suppress(Exception):
            receive.close()
        with suppress(Exception):
            send.close()
