"""haru-kit: the guide and the commands start, stop, status, doctor, uninstall.

Imports no engine: only the standard library and rich.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from rich.markup import escape

from . import __version__, envs, model, paths, ui, ways
from .detect import Facts, collect
from .procs import Failure
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--yes", "-y", action="store_true", help="accept the recommendation, ask nothing")
    common.add_argument("--verbose", "-v", action="store_true", help="show details and logs")
    parser = argparse.ArgumentParser(prog="haru-kit", parents=[common],
                                     description="Run Haru, the voice model that listens and talks at the same time.")
    parser.add_argument("--version", action="version", version=f"haru-kit {__version__}")
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("start", parents=[common], help="start Haru (first time: the guide)")
    start.add_argument("--on", choices=ways.WAYS, help="where Haru runs")
    front = start.add_mutually_exclusive_group()
    front.add_argument("--app", action="store_true", help="Mac: the Mac app")
    front.add_argument("--browser", action="store_true", help="Mac: the browser")
    start.add_argument("--port", type=int, help="port of the web app (default 8998)")
    start.add_argument("--quantize", choices=("8", "none"), help="Mac: 8-bit weights (default) or none")
    start.add_argument("--region", choices=("auto", "any", "us", "eu", "ap"),
                       help="Modal: GPU region (default auto: the nearest one, from this computer's time zone)")
    sub.add_parser("stop", parents=[common], help="stop Haru on Modal")
    sub.add_parser("status", parents=[common], help="what is installed and running, folders, sizes")
    sub.add_parser("doctor", parents=[common], help="check this computer and each way")
    sub.add_parser("uninstall", parents=[common], help="remove what haru-kit installed")
    return parser


# -------------------------------------------------------------------- guide

def _status(way: str, facts: Facts, settings: Settings) -> dict[str, bool]:
    status = {"env": envs.installed(way, facts), "model": model.downloaded()}
    if way == "mac":
        from .mac import app_current

        status["app"] = app_current(settings)
    return status


def _time_text(way: str, status: dict[str, bool]) -> str | None:
    if way == "modal":
        return "about 5–15 minutes for each start (the GPU loads Haru each time)"
    if not status.get("model"):
        return "about 15 minutes on a fast connection"
    if not status.get("env"):
        return "a few minutes"
    return None


def guide(args, settings: Settings, repeat: bool) -> int:
    if getattr(args, "port", None) is not None and not (1 <= args.port <= 65535):
        ui.error("Invalid port.", f"Port must be between 1 and 65535 (got {args.port}).",
                 "Use a valid port, for example: haru-kit start --port 8999")
        return 1
    facts = collect()
    ui.header()
    ui.facts(facts)
    all_ways = ways.evaluate(facts)
    saved_way = settings.way if settings.way in ways.WAYS else None
    chosen = getattr(args, "on", None) or (saved_way if repeat else None)
    if chosen:
        way = next(w for w in all_ways if w.key == chosen)
        if not way.possible:
            ui.error(f"Haru cannot run with “{ways.SHORT[way.key]}” on this computer.", way.reason.capitalize() + ".",
                     "Run haru-kit to see the ways that work here.")
            return 1
    else:
        way = ways.recommended(all_ways) if args.yes else ui.choose_way(all_ways)

    same_way = repeat and way.key == saved_way
    front = None
    if way.key == "mac":
        if getattr(args, "app", False):
            front = "app"
        elif getattr(args, "browser", False):
            front = "browser"
        elif same_way and settings.front in ("app", "browser"):
            front = settings.front
        elif args.yes:
            front = "app"
        else:
            front = ui.choose_front()

    if getattr(args, "port", None) is not None:
        settings.port = args.port
    if getattr(args, "quantize", None):
        settings.quantize = args.quantize
    if getattr(args, "region", None):
        settings.modal_region = args.region

    status = _status(way.key, facts, settings)
    items = ways.plan(way.key, front, status)
    if way.key == "modal":
        from .modal import choose_region, price, region_text

        region = choose_region(settings.modal_region)
        items[-1].text = "Start a GPU"
        items[-1].note = (f"{region_text(region, settings.modal_region)} · about ${price(region):.2f} an hour; "
                          "change it with --region")
    if not all(item.done for item in items if item.folder is not None) or not same_way:
        if not ui.plan(items, ways.space(items), _time_text(way.key, status),
                       resumable=way.key != "modal", os_name=facts.os):
            return 1
        if not args.yes and not ui.confirm("Start now?", yes="Continue", no="Cancel"):
            ui.console.print("  Nothing was changed.")
            return 0
    settings.way, settings.front = way.key, front
    settings.save()
    return run_way(way.key, front, facts, settings, args.verbose, ask=not args.yes)


def run_way(way: str, front: str | None, facts: Facts, settings: Settings, verbose: bool, ask: bool) -> int:
    try:
        if way == "mac":
            from . import mac

            (mac.app if front == "app" else mac.browser)(facts, settings, verbose)
        elif way == "nvidia":
            from . import nvidia

            nvidia.run(facts, settings, verbose)
        else:
            from . import modal

            modal.run(facts, settings, verbose, ask)
    except Failure as error:
        ui.console.print()
        ui.error(error.what, error.why, error.fix)
        return 1
    except KeyboardInterrupt:
        ui.console.print("\n  Stopped. The next start continues where it stopped.")
        return 130
    return 0


# --------------------------------------------------------------- commands

def status_cmd(_args) -> int:
    facts = collect()
    settings = Settings.load()
    ui.header()
    ui.console.print(f"  This computer: {escape(facts.describe())}")
    ui.console.print()
    rows = [("haru-kit folder", paths.home_dir())]
    for way in ways.WAYS:
        rows.append((f"{ways.SHORT[way]} engine", paths.env_dir(way)))
    rows.append(("Model", paths.model_dir()))
    if facts.os == "mac":
        rows.append(("Mac app", paths.app_path()))
    for name, folder in rows:
        shown = escape(paths.short(folder))
        if folder.exists():
            size = envs.size_bytes(folder) / 1e9
            ui.console.print(f"  [green]✓[/] {name:<16} {ui.gb(size):>8}   [dim]{shown}[/]")
        else:
            ui.console.print(f"  – {name:<16} {'not installed':>8}   [dim]{shown}[/]")
    ui.console.print()
    if settings.way in ways.WAYS:
        front = f" ({settings.front})" if settings.front else ""
        ui.console.print(f"  Last choice: {ways.SHORT[settings.way]}{front}, port {settings.port}. "
                         "Run it again: haru-kit start")
    if facts.modal_login and envs.installed("modal", facts):
        from . import modal

        ui.console.print(f"  Modal: {modal.running_text()}")
    ui.console.print()
    return 0


def doctor_cmd(_args) -> int:
    facts = collect()
    settings = Settings.load()
    ui.header()
    free = min(facts.free_gb.values()) if facts.free_gb else 0
    ui.console.print(f"  This computer: {escape(facts.describe())}, {ui.gb(free)} free")
    ui.console.print()
    ok = "[green]✓[/]"
    bad = "[red]✗[/]"

    def line(label: str, text: str) -> None:
        ui.console.print(f"  {label:<10} {text}")

    hub = escape(paths.short(paths.hub_cache()))
    model_line = (f"{ok} model downloaded ({ui.gb(model.size_gb())}, {hub})"
                  if model.downloaded() else "– model not downloaded yet (10.5 GB; haru-kit downloads it)")
    for way in ways.evaluate(facts):
        label = ways.SHORT[way.key]
        if not way.possible:
            line(label, f"– not possible: {escape(way.reason)}")
            continue
        line(label, f"{ok} possible")
        pad = " " * 10
        if way.key in ("mac", "nvidia"):
            if envs.installed(way.key, facts):
                size = envs.size_bytes(paths.env_dir(way.key)) / 1e9
                line(pad, f"{ok} engine installed ({ui.gb(size)})")
            else:
                line(pad, "– engine not installed yet (haru-kit installs it)")
            line(pad, model_line)
        if way.key == "mac":
            from .mac import app_current, command_line_tools

            if command_line_tools():
                line(pad, f"{ok} Command Line Tools (for the Mac app)")
            else:
                line(pad, f"{bad} Command Line Tools missing → run: xcode-select --install")
            if app_current(settings):
                line(pad, f"{ok} Mac app installed ({escape(paths.short(paths.app_path()))})")
        if way.key == "modal":
            if facts.modal_login:
                line(pad, f"{ok} logged in")
            else:
                line(pad, f"{bad} not logged in → run: haru-kit start --on modal (it helps you log in)")
    if facts.uv:
        line("uv", f"{ok} found")
    else:
        line("uv", f"{bad} missing → run: {envs.uv_install_line(facts.os)}")
    for name, url in (("Network", "https://huggingface.co"), ("", "https://modal.com")):
        reach = _reachable(url)
        line(name, f"{ok if reach else bad} {url.split('//')[1]} {'reachable' if reach else 'not reachable'}")
    ui.console.print()
    return 0


def _reachable(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5):
            return True
    except Exception as error:  # an HTTP error still means the network works
        return hasattr(error, "code")


def stop_cmd(args) -> int:
    from . import modal

    try:
        modal.stop()
    except Failure as error:
        ui.error(error.what, error.why, error.fix)
        return 1
    return 0


def uninstall_cmd(args) -> int:
    ui.header()
    home = paths.home_dir()
    library = paths.app_library_dir()
    has_library = library.is_dir() and any(library.rglob("*"))
    targets = [home]
    if paths.app_path().exists():
        targets.append(paths.app_path())
    ui.console.print("  haru-kit will remove:")
    for target in targets:
        note = "  (engines, settings, logs)" if target == home and has_library else ""
        ui.console.print(f"    {escape(paths.short(target))}{note}")
    ui.console.print()
    if not args.yes and not ui.confirm("Remove these?", yes="Remove", no="Keep them", default=False):
        ui.console.print("  Nothing was removed.")
        return 0
    if paths.env_python("modal").is_file():
        from . import modal

        modal.uninstall(ask=not args.yes)
    if paths.app_path().exists():
        shutil.rmtree(paths.app_path(), ignore_errors=True)
        ui.done("Removed", paths.short(paths.app_path()))
    if home.exists():
        if has_library:
            for child in home.iterdir():
                if child != library:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
            ui.done("Removed", f"{paths.short(home)} (engines, settings, logs)")
            if not args.yes and ui.confirm(
                f"Also delete your characters and voices ({paths.short(library)})?",
                yes="Delete characters and voices", no="Keep them", default=False,
            ):
                shutil.rmtree(home, ignore_errors=True)
                ui.done("Removed", paths.short(library))
            else:
                ui.console.print(f"  Your characters and voices stay in {escape(paths.short(library))}.")
        else:
            shutil.rmtree(home, ignore_errors=True)
            ui.done("Removed", paths.short(home))
    if model.downloaded() or paths.model_dir().exists():
        size = ui.gb(model.size_gb())
        if not args.yes and ui.confirm(f"Also delete the Haru model ({size}, {paths.short(paths.model_dir())})?",
                                       yes="Delete the model", no="Keep the model", default=False):
            model.remove()
            ui.done("Removed", paths.short(paths.model_dir()))
        else:
            ui.console.print(f"  The model stays in {escape(paths.short(paths.model_dir()))}.")
    ui.console.print("  To remove haru-kit itself: uv tool uninstall haru-kit")
    ui.console.print()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command is None:
            return guide(args, Settings.load(), repeat=False)
        if args.command == "start":
            settings = Settings.load()
            return guide(args, settings, repeat=settings.way is not None)
        return {"stop": stop_cmd, "status": status_cmd, "doctor": doctor_cmd, "uninstall": uninstall_cmd}[
            args.command](args)
    except KeyboardInterrupt:
        ui.console.print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
