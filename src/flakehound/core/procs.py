"""Safe subprocess handling.

Rules (see SECURITY.md):
* commands are always argument lists, never ``shell=True``;
* every child runs in its own process group so we can kill the whole tree
  (pytest may spawn its own children);
* every live child is tracked, and :meth:`ProcessRegistry.kill_all` is called on
  timeout, Ctrl+C and interpreter exit, so nothing is left running.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class ProcOutput:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class StoppedError(RuntimeError):
    """Raised when spawning after the registry has been shut down."""


class ProcessRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen[bytes]] = set()
        self._stopped = False
        atexit.register(self.kill_all)

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: float | None,
    ) -> ProcOutput:
        if isinstance(args, str):  # defence in depth: never build shell strings
            raise TypeError("args must be a sequence of strings, not a string")
        kwargs: dict[str, object] = {}
        if _IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        with self._lock:
            if self._stopped:
                raise StoppedError("flakehound is shutting down")
            proc = subprocess.Popen(  # noqa: S603 - args list, no shell
                list(args),
                cwd=str(cwd),
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **kwargs,  # type: ignore[call-overload]
            )
            self._procs.add(proc)
        timed_out = False
        try:
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                out, err = proc.communicate()
        finally:
            with self._lock:
                self._procs.discard(proc)
        return ProcOutput(
            returncode=proc.returncode,
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
            timed_out=timed_out,
        )

    def kill_all(self) -> None:
        """Stop accepting work and kill every running child process tree."""
        with self._lock:
            self._stopped = True
            procs = list(self._procs)
        for proc in procs:
            _kill_tree(proc)


def _taskkill_path() -> str:
    # Absolute path: never resolve system tools through PATH (it could be hijacked).
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return os.path.join(system_root, "System32", "taskkill.exe")


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            # taskkill /T kills the child and all its descendants.
            subprocess.run(  # noqa: S603 - fixed args list, absolute path
                [_taskkill_path(), "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=15,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    with contextlib.suppress(OSError):
        proc.kill()
