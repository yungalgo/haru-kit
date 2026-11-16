"""Screens and terminal prompts. Each function prints one screen part; nothing here runs commands."""

from __future__ import annotations

import time

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, Prompt

from . import paths
from .detect import Facts
from .ways import Item, Space, Way

console = Console(highlight=False)
TIP = "Tip: use wired headphones. With speakers, Haru can hear itself."


def gb(value: float) -> str:
    return f"{value:.1f} GB" if value < 10 else f"{value:.0f} GB"


def header() -> None:
    console.print()
    console.print("  [bold]Haru[/] — a voice model that listens and talks at the same time.")
    console.print()


def facts(f: Facts) -> None:
    console.print("  [bold]This computer[/]")
    console.print(f"    {escape(f.describe())}")
    free = min(f.free_gb.values()) if f.free_gb else 0
    console.print(f"    Free space: {gb(free)}")
    console.print()


def select(title: str, rows: list[tuple[str, str, bool]], default: int) -> int:
    """A choice. rows: (label, note, possible). Returns the index of the chosen row.

    In a terminal: move with ↑/↓ (or j/k), choose with Enter, or press its number. Rows that are not possible are
    shown with their reason and skipped. Without a terminal (a script, a coding agent): a numbered question."""
    choices = [i for i, row in enumerate(rows) if row[2]]
    if not interactive():
        _print_rows(title, rows, default, numbers=True)
        try:
            answer = Prompt.ask("  Choose", choices=[str(n) for n in range(1, len(choices) + 1)],
                                default=str(choices.index(default) + 1), show_choices=False, console=console)
        except EOFError:
            answer = str(choices.index(default) + 1)
        console.print()
        return choices[int(answer) - 1]
    from rich.live import Live

    current = default
    with Live(_rows_text(title, rows, current), console=console, auto_refresh=False, transient=True) as live:
        while True:
            key = _read_key()
            if key in ("up", "down"):
                step = -1 if key == "up" else 1
                current = choices[(choices.index(current) + step) % len(choices)]
            elif key.isdigit() and 1 <= int(key) <= len(choices):
                current = choices[int(key) - 1]
                break
            elif key == "enter":
                break
            live.update(_rows_text(title, rows, current), refresh=True)
    _print_rows(title, rows, current, numbers=False, chosen=True)
    return current


def _rows_text(title: str, rows: list[tuple[str, str, bool]], current: int, numbers: bool = False,
               chosen: bool = False):
    from rich.text import Text

    text = Text()
    text.append(f"  {title}\n", style="bold")
    number = 0
    for i, (label, note, possible) in enumerate(rows):
        if not possible:
            if not chosen:
                text.append(f"  – {label:<24} {note}\n", style="dim")
            continue
        number += 1
        selected = i == current
        if chosen and not selected:
            continue
        mark = "✓" if chosen else ("❯" if selected else " ")
        prefix = f"{number}. " if numbers else ""
        text.append(f"  {mark} {prefix}{label:<24} ", style="bold cyan" if selected and not chosen else "")
        text.append(f"{note}\n", style="dim")
    if not chosen and not numbers:
        text.append("  ↑/↓ to move, Enter to choose\n", style="dim")
    return text


def _print_rows(title: str, rows, current: int, numbers: bool, chosen: bool = False) -> None:
    console.print(_rows_text(title, rows, current, numbers=numbers, chosen=chosen))


def interactive() -> bool:
    import os
    import sys

    return sys.stdin.isatty() and console.is_terminal and os.environ.get("TERM") != "dumb"


def _read_key() -> str:
    """One key: "up", "down", "enter", a digit, or another character. Ctrl+C raises KeyboardInterrupt."""
    import sys

    if sys.platform.startswith("win"):
        import msvcrt

        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
    else:
        import select as sel
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            key = sys.stdin.read(1)
            if key == "\x1b":
                if not sel.select([fd], [], [], 0.05)[0]:
                    return ""
                return {"[A": "up", "[B": "down", "OA": "up", "OB": "down"}.get(sys.stdin.read(2), "")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if key == "\x03":
        raise KeyboardInterrupt
    if key in ("\r", "\n"):
        return "enter"
    return {"k": "up", "j": "down"}.get(key, key)


def choose_way(ways: list[Way]) -> Way:
    """The way list (ways that are not possible show their reason); returns the chosen way."""
    from .ways import NOTES

    rows = [(w.label, (("recommended · " if w.recommended else "") + NOTES[w.key]) if w.possible
             else f"not possible: {w.reason}", w.possible) for w in ways]
    default = next(i for i, w in enumerate(ways) if w.recommended)
    return ways[select("Ways to run Haru", rows, default)]


def choose_front() -> str:
    rows = [("Mac app", "recommended · lowest delay · needs Apple's Command Line Tools", True),
            ("Browser", "no build · a little more delay", True)]
    return ("app", "browser")[select("How do you want to talk to Haru?", rows, 0)]


def plan(items: list[Item], spaces: list[Space], minutes: str | None, resumable: bool = True,
         os_name: str | None = None) -> bool:
    """The plan screen. Returns False (after the message) when a disk is too small."""
    os_name = os_name or paths.system()
    console.print("  [bold]Haru will now:[/]")
    for n, item in enumerate(items, 1):
        if item.done:
            size = "[green]✓ already done[/]"
        elif item.note:
            size = escape(item.note)
        else:
            size = ("about " if item.text.startswith("Install") else "") + gb(item.size_gb)
        folder = escape(paths.short(item.folder)) if item.folder else ""
        console.print(f"    {n}. {escape(item.text):<34} {size:<14} [dim]{folder}[/]")
    console.print()
    hf_example = ('$env:HF_HOME = "D:\\huggingface"' if os_name == "windows"
                  else "export HF_HOME=/mnt/disk/huggingface" if os_name == "linux"
                  else "export HF_HOME=/Volumes/External/huggingface")
    for space in spaces:
        if not space.ok:
            error("Not enough free space.",
                  f"Haru needs {gb(space.need_gb)} in {paths.short(space.folder)}, "
                  f"and that disk has {gb(space.free_gb)} free.",
                  f"Free some space, or move the model to another disk:\n  {hf_example}\nThen run haru-kit again.")
            return False
    if len(spaces) == 1:
        console.print(f"  Space needed: {gb(spaces[0].need_gb)} · free: {gb(spaces[0].free_gb)} [green]✓[/]")
    elif spaces:
        parts = [f"{escape(paths.short(s.folder))}: {gb(s.need_gb)} needed ({gb(s.free_gb)} free)" for s in spaces]
        console.print(f"  Space: {' · '.join(parts)} [green]✓[/]")
    if minutes and resumable:
        console.print(f"  Time: {escape(minutes)}. You can stop at any time;")
        console.print("  the next start continues where it stopped.")
    elif minutes:
        console.print(f"  Time: {escape(minutes)}.")
    console.print()
    return True


def confirm(question: str = "Continue?", yes: str = "Continue", no: str = "Cancel", default: bool = True) -> bool:
    """A yes/no question as a choice of two rows. Without a terminal: a [y/n] question (scripts can pipe "y")."""
    if not interactive():
        try:
            answer = Confirm.ask(f"  {escape(question)}", default=default, console=console)
        except EOFError:
            answer = default
        console.print()
        return answer
    return select(question, [(yes, "", True), (no, "", True)], 0 if default else 1) == 0


def done(text: str, detail: str = "") -> None:
    console.print(f"  [green]✓[/] {escape(text):<40} [dim]{escape(detail)}[/]")


def error(what: str, why: str = "", fix: str = "") -> None:
    console.print(f"  [red]✗ {escape(what)}[/]")
    for line in why.splitlines():
        console.print(f"    {escape(line)}")
    for line in fix.splitlines():
        console.print(f"    {escape(line)}")
    console.print()


class Step:
    """A spinner line for one step; becomes ✓ when done."""

    def __init__(self, text: str):
        self.text = text
        self.started = time.monotonic()
        self.status = console.status(f"  {text}", spinner="dots")

    def __enter__(self) -> Step:
        self.status.__enter__()
        return self

    def update(self, detail: str, note: str = "") -> None:
        from rich.markup import escape

        line = f"  {self.text}  [dim]{escape(detail[:70])}[/]"
        self.status.update(f"{line}\n    [dim]{escape(note)}[/]" if note else line)

    def __exit__(self, kind, value, tb) -> None:
        self.status.__exit__(kind, value, tb)


class TimedStep:
    """A step with no progress of its own (a compile): a bar on the expected time, the time so far,
    and a plain note. The bar stops at 95% until the step really ends."""

    def __init__(self, text: str, expected_seconds: float, note: str):
        self.text, self.expected, self.note = text, expected_seconds, note
        self.progress = Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(bar_width=20),
            TextColumn("[dim]{task.fields[clock]}[/]"), console=console, transient=True,
        )
        self.task = self.progress.add_task(f"  {text}", total=100, clock="")
        self.started = time.monotonic()
        self._stop = False

    def _tick(self) -> None:
        while not self._stop:
            waited = time.monotonic() - self.started
            minutes = max(1, round(self.expected / 60))
            clock = f"{int(waited // 60)} min {int(waited % 60):02d} s of about {minutes} min · {self.note}"
            self.progress.update(self.task, completed=min(95.0, 100 * waited / self.expected), clock=clock)
            time.sleep(0.5)

    def __enter__(self) -> TimedStep:
        import threading

        self.progress.__enter__()
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()
        return self

    def update(self, _detail: str) -> None:
        """Log lines of the step are not shown (they look like errors to users); they stay in the log."""

    def __exit__(self, kind, value, tb) -> None:
        self._stop = True
        self._thread.join(timeout=1)
        self.progress.__exit__(kind, value, tb)


class StageStep:
    """A step in named stages that a child process reports: a bar (stage n of N), the stage, the time so far,
    and a plain note."""

    def __init__(self, text: str, stages: list[str], note: str):
        self.stages, self.note, self.index = stages, note, 0
        self.progress = Progress(
            SpinnerColumn(), TextColumn("{task.description}"), BarColumn(bar_width=20),
            TextColumn("[dim]{task.fields[clock]}[/]"), console=console, transient=True,
        )
        self.task = self.progress.add_task(f"  {text}", total=len(stages), clock="")
        self.started = time.monotonic()
        self._stop = False

    def stage(self, index: int) -> None:
        self.index = index

    def _tick(self) -> None:
        while not self._stop:
            clock = (f"{self.stages[self.index]} ({self.index + 1} of {len(self.stages)}) · "
                     f"{duration(time.monotonic() - self.started)} · {self.note}")
            self.progress.update(self.task, completed=self.index + 0.5, clock=clock)
            time.sleep(0.5)

    def __enter__(self) -> StageStep:
        import threading

        self.progress.__enter__()
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, kind, value, tb) -> None:
        self._stop = True
        self._thread.join(timeout=1)
        self.progress.__exit__(kind, value, tb)


def duration(seconds: float) -> str:
    """45 s, 3 min 05 s."""
    seconds = int(seconds)
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min {seconds % 60:02d} s"


def download_progress() -> Progress:
    return Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(bar_width=14), DownloadColumn(),
        TransferSpeedColumn(), TimeRemainingColumn(), console=console, transient=True,
    )


def ready_browser(url: str, opened: bool) -> None:
    console.print()
    note = "(opened in your browser)" if opened else ""
    console.print(f"  [green]✓[/] Haru is ready: [bold]{url}[/]   [dim]{note}[/]")
    console.print()
    console.print("  Press Ctrl+C here to stop Haru.")
    console.print(f"  {TIP}")
    console.print()


def ready_ssh(port: int, user: str, host: str) -> None:
    console.print()
    console.print(f"  [green]✓[/] Haru is ready on this computer, port {port}.")
    console.print()
    console.print("  You are connected over SSH. On your laptop, run:")
    console.print(f"    [bold]ssh -L {port}:localhost:{port} {escape(user)}@{escape(host)}[/]")
    console.print(f"  Then open [bold]http://localhost:{port}[/] in your laptop's browser.")
    console.print()
    console.print("  Press Ctrl+C here to stop Haru.")
    console.print(f"  {TIP}")
    console.print()


def ready_app() -> None:
    console.print()
    console.print("  [green]✓[/] Haru is ready. The Mac app is open.")
    console.print()
    console.print("  Next time: open ~/Applications/Haru.app, or run `haru-kit start`.")
    console.print(f"  {TIP}")
    console.print()


def ready_modal(url: str, gpu: str, price: float, region: str, open_by: str, idle_minutes: int) -> None:
    console.print()
    console.print(f"  [green]✓[/] Haru is ready: [bold]{escape(url)}[/]")
    console.print("    Open this link in any browser. Keep it private: anyone with it can use your GPU.")
    console.print()
    console.print(f"  GPU: {escape(gpu)} in {escape(region)} · about ${price:.2f} an hour while it runs.")
    console.print(f"  If you do not start a conversation by {escape(open_by)}, Haru stops by itself.")
    console.print(f"  After a conversation, it stops after {idle_minutes} minutes with no conversation.")
    console.print("  Stop now: [bold]haru-kit stop[/]")
    console.print()
