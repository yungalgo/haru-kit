"""NVIDIA way (Windows and Linux): the web app, in the browser here or through an SSH tunnel."""

from __future__ import annotations

import getpass
import socket
import webbrowser

from . import steps, ui
from .detect import Facts
from .settings import Settings


def tunnel_host() -> str:
    return socket.gethostname()


def run(facts: Facts, settings: Settings, verbose: bool) -> None:
    steps.install_env("nvidia", facts, verbose)
    steps.download_model("nvidia", verbose)
    args = ["--engine", "torch", "--codec-backend", "auto", "--port", str(settings.port), "--network-profile", "local"]
    runtime, url = steps.start_server("nvidia", args, verbose)
    if facts.ssh:
        ui.ready_ssh(settings.port, getpass.getuser(), tunnel_host())
    else:
        opened = webbrowser.open(url) if facts.display else False
        ui.ready_browser(url, opened)
    steps.wait_until_stopped(runtime)
