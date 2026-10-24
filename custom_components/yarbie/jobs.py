"""Long robot calls as jobs: started once, followed by any number of panels.

A route preview can take the robot over a minute. Held open as one call it dies with a
sleeping phone or a reload, and clicking again would send the robot a second preview
while it still works on the first. A job runs once per key, keeps its result, and lets a
reconnecting panel pick it up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import Any
import uuid

# Finished jobs are kept this long so a panel that was away can still read the result.
KEEP_S = 3600.0


@dataclass
class Job:
    key: str
    kind: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    status: str = "running"  # running, done, error
    result: Any = None
    error: str | None = None
    _listeners: list[Callable[[Job], None]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "result": self.result,
            "error": self.error,
        }

    def listen(self, cb: Callable[[Job], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb(self)


class Jobs:
    def __init__(self) -> None:
        self._by_key: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self._by_key.values() if j.id == job_id), None)

    def start(self, kind: str, key: str, work: Callable[[], Awaitable[Any]]) -> Job:
        """The job for ``key``: the running or kept one if there is one, else a new one."""
        self._expire()
        full = f"{kind}:{key}"
        existing = self._by_key.get(full)
        if existing is not None and existing.status != "error":
            return existing
        job = Job(full, kind)
        self._by_key[full] = job

        async def run() -> None:
            try:
                job.result = await work()
                job.status = "done"
            except Exception as err:  # reported to whoever follows the job
                job.status = "error"
                job.error = str(err) or type(err).__name__
            job.finished = time.time()
            job._notify()

        task = asyncio.get_running_loop().create_task(run(), name=f"yarbie-{kind}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def cancel_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _expire(self) -> None:
        now = time.time()
        for key, job in list(self._by_key.items()):
            if job.finished is not None and now - job.finished > KEEP_S:
                del self._by_key[key]
