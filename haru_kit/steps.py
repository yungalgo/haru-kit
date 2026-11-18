"""Steps that more than one way uses: install the engine, download the model, run the web server."""

from __future__ import annotations

import signal
import time

from . import envs, model, paths, ui
from .detect import Facts
from .procs import CommandError, Failure, Runtime, run_logged

ENGINE_TEXT = {"mac": "Install the Mac engine", "nvidia": "Install the NVIDIA engine",
               "modal": "Install the Modal tool"}


def install_env(way: str, facts: Facts, verbose: bool) -> None:
    text = ENGINE_TEXT[way]
    if envs.installed(way, facts):
        ui.done(text, "already done")
        return
    log = paths.log_file(way)
    started = time.monotonic()
    with ui.Step(text) as step:
        try:
            envs.ensure(way, facts, lambda args: run_logged(args, log, step.update, verbose))
        except CommandError as error:
            raise Failure(f"{text}: failed.", "\n".join(error.tail[-8:]),
                          f"Check the internet connection and run haru-kit again.\nFull log: {log}") from None
    size = envs.size_bytes(paths.env_dir(way)) / 1e9
    ui.done(text, f"{ui.gb(size)} · {time.monotonic() - started:.0f} s")


def download_model(way: str, verbose: bool) -> None:
    text = "Download the Haru model"
    if model.downloaded():
        ui.done(text, "already done")
        return
    runtime = Runtime(paths.env_python(way), paths.runtime_script("download.py"), [], paths.log_file(way), verbose)
    try:
        with ui.download_progress() as progress:
            task = progress.add_task(f"  {text}", total=None)
            for event in runtime.events():
                if event.get("event") == "progress":
                    progress.update(task, completed=event["done"], total=event["total"])
                elif event.get("event") == "error":
                    raise Failure(event.get("what", "Download failed."), event.get("why", ""), event.get("fix", ""))
        code = runtime.proc.wait()
    finally:
        runtime.stop()
    if code != 0 or not model.downloaded():
        raise Failure("The model download stopped.", "\n".join(runtime.tail(6)),
                      "Run haru-kit again. The download continues where it stopped.")
    ui.done(text, ui.gb(model.size_gb()))


def start_server(
    way: str,
    args: list[str],
    verbose: bool,
    *,
    script: str = "web_server.py",
) -> tuple[Runtime, str]:
    """Start a runtime server script in the way's env; wait for "ready". Returns the runtime and the URL."""
    runtime = Runtime(paths.env_python(way), paths.runtime_script(script), args, paths.log_file(way), verbose)
    started = time.monotonic()
    try:
        with ui.Step("Load the model") as step:
            for event in runtime.events():
                kind = event.get("event")
                if kind == "ready":
                    ui.done("Load the model", f"{time.monotonic() - started:.0f} s")
                    return runtime, event["url"]
                if kind == "error":
                    raise Failure(event.get("what", "The server stopped."), event.get("why", ""),
                                  event.get("fix", "") + f"\nLog: {paths.log_file(way)}")
                if kind == "step":
                    step.update(event.get("text", ""))
        raise Failure("The Haru server stopped while it was loading.", "\n".join(runtime.tail(8)),
                      f"Read the log: {paths.log_file(way)}\nThen run: haru-kit doctor")
    except BaseException:
        runtime.stop()
        raise


def wait_until_stopped(runtime: Runtime) -> None:
    """Block until Ctrl+C / SIGTERM or the server ends; then stop it cleanly."""
    prev_term = signal.getsignal(signal.SIGTERM)

    def _on_term(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_term)
    try:
        for event in runtime.events():
            if event.get("event") == "error":
                ui.error(event.get("what", ""), event.get("why", ""), event.get("fix", ""))
        runtime.proc.wait()
    except KeyboardInterrupt:
        ui.console.print("\n  Stopping Haru…")
    finally:
        signal.signal(signal.SIGTERM, prev_term)
        runtime.stop()
    ui.done("Haru stopped.")
