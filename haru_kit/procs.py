"""Child processes: commands with a log, runtime scripts with JSON lines, clean stop on Ctrl+C."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

WINDOWS = sys.platform.startswith("win")


class Failure(Exception):
    """An error for the user: what happened, why, what to do."""

    def __init__(self, what: str, why: str = "", fix: str = ""):
        super().__init__(what)
        self.what, self.why, self.fix = what, why, fix


class CommandError(Exception):
    def __init__(self, args: list[str], code: int, tail: list[str]):
        super().__init__(f"{Path(args[0]).name} exited with code {code}")
        self.args_list, self.code, self.tail = args, code, tail


def popen_flags() -> dict:
    """Own process group, so Ctrl+C reaches us first and we stop the child cleanly."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    return {"start_new_session": True}


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("VIRTUAL_ENV", None)  # uv must not pick the user's active env
    env.update(extra or {})
    return env


def run_logged(args: list[str], log: Path, on_line: Callable[[str], None] | None = None,
               verbose: bool = False, env: dict[str, str] | None = None) -> None:
    """Run a command to completion; its output goes to ``log``. Raise CommandError on failure."""
    log.parent.mkdir(parents=True, exist_ok=True)
    tail: list[str] = []
    with open(log, "a", encoding="utf-8") as out:
        out.write("$ " + " ".join(args) + "\n")
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=child_env(env), **popen_flags())
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                out.write(line)
                line = line.rstrip()
                tail = (tail + [line])[-20:]
                if verbose:
                    print(line, file=sys.stderr)
                if on_line:
                    on_line(line)
            code = proc.wait()
        except BaseException:
            stop(proc)
            raise
    if code != 0:
        raise CommandError(args, code, tail)


class Runtime:
    """A runtime script in a way's env. Reads its JSON lines; other output goes to the log."""

    def __init__(self, python: Path, script: Path, args: list[str], log: Path, verbose: bool = False,
                 env: dict[str, str] | None = None):
        log.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = log
        self._log = open(log, "a", encoding="utf-8")
        cmd = [str(python), str(script), *args]
        self._log.write("$ " + " ".join(cmd) + "\n")
        self._log.flush()
        self.verbose = verbose
        # stderr (logs) goes straight to the file; stdout carries the JSON lines.
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=self._log, text=True,
                                     encoding="utf-8", errors="replace", env=child_env(env), **popen_flags())
        if verbose:
            threading.Thread(target=self._follow, daemon=True).start()

    def _follow(self) -> None:
        import time

        with open(self.log_path, encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            while self.proc.poll() is None:
                line = f.readline()
                if line:
                    print(line.rstrip(), file=sys.stderr)
                else:
                    time.sleep(0.2)

    def events(self) -> Iterator[dict]:
        """JSON events until the script ends."""
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                self._log.write(line + "\n")
                continue
            if isinstance(event, dict):
                yield event

    def tail(self, lines: int = 15) -> list[str]:
        self._log.flush()
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        except OSError:
            return []

    def stop(self) -> None:
        stop(self.proc)
        self._log.close()


def stop(proc: subprocess.Popen, timeout: float = 10) -> None:
    """Ask the child to stop (Ctrl+C), then terminate, then kill."""
    if proc.poll() is not None:
        return
    try:
        if WINDOWS:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            os.killpg(proc.pid, signal.SIGINT)
        proc.wait(timeout=timeout)
        return
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        if WINDOWS:
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        if WINDOWS:
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
        proc.wait()
