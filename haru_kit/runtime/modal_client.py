"""Runs in haru-kit's "modal" env: start, stop or check Haru on Modal, after `modal deploy modal_app.py`.

    python modal_client.py start --key KEY     # stops a running Haru first; prints events until Haru is ready
    python modal_client.py stop
    python modal_client.py status              # prints {"running": bool, "url": ..., "region": ...}

Prints JSON lines on stdout. Events of start, in this order:
    {"event": "tick"}                          about every 2 s, while it waits and loads
    {"event": "modal_log", "text": ...}        a Modal system log line (for example: waiting for a GPU)
    {"event": "gpu", "region": ...}            a container started
    {"event": "stage", "stage": ..., "cached": bool}  the container's loading stage (see STAGES in modal_app.py);
                                               cached: kernels from an earlier start are used
    {"event": "url", "url": ...}               the tunnel is open
    {"event": "ready"}                         Haru answers on the link
    {"event": "error", "what": ..., "why": ...}
haru-kit decides the time limits; this script waits until ready or a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import urllib.request
from datetime import UTC, datetime, timedelta

import modal

APP_NAME = "haru-kit"
LINKS_NAME = "haru-kit-links"  # same as in modal_app.py
CURRENT = "current"  # links[CURRENT] = {"call": function call id, "link": link id}
LAST_STOP = "last_stop"  # same as in modal_app.py: {"at": time, "reason": ...} when Haru stopped by itself
POLL_SECONDS = 2
_print_lock = threading.Lock()


def emit(**event) -> None:
    with _print_lock:
        print(json.dumps(event), flush=True)


def running(links) -> dict:
    """The link entry of the current start while its container runs ({} if none)."""
    current = links.get(CURRENT) or {}
    link = (links.get(current.get("link", "")) if current else None) or {}
    return {} if link.get("error") else link


def stop(links) -> bool:
    """Stop the current start; True if its container ran."""
    current = links.get(CURRENT)
    was_running = bool(running(links))
    if current:
        try:
            modal.FunctionCall.from_id(current["call"]).cancel(terminate_containers=True)
        except Exception:  # noqa: BLE001 - already finished
            pass
    # Also the entries of containers that Modal ended before they could remove their own (a cancelled start).
    links.clear()
    return was_running


def follow_system_logs(function, since: datetime, gpu_found: threading.Event) -> None:
    """Send Modal's system log lines of the function (for example "waiting to be scheduled") until a GPU is found.
    Lines of a container (for example "Runner terminated." of a cancelled start) are not about the wait."""
    try:
        for entry in function.logs.stream():
            if gpu_found.is_set():
                return
            if (entry.source == "system" and entry.timestamp >= since and entry.message.strip()
                    and not any(context.startswith("ta-") for context in entry.context_ids)):
                emit(event="modal_log", text=entry.message.strip())
    except Exception as error:  # noqa: BLE001 - the logs only explain the wait; the start goes on without them
        print(f"modal_client: no Modal logs: {error}", file=sys.stderr, flush=True)


def ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/ready", timeout=10) as response:
            return bool(json.load(response).get("ready"))
    except Exception:  # noqa: BLE001 - not up yet
        return False


def ended(call) -> str | None:
    """None while the call runs (waits POLL_SECONDS); else why it ended."""
    try:
        call.get(timeout=POLL_SECONDS)  # returns only when the container already ended
        return ""
    except Exception as error:  # noqa: BLE001
        name = type(error).__name__.lower()
        if "timeout" in name or "connection" in name or "unavailable" in name:
            return None
        return str(error)[:300]


def start(links, key: str) -> int:
    if not key:
        emit(event="error", what="Missing access key.", why="Start Haru with `haru-kit start --on modal`.")
        return 1
    stop(links)  # never two GPUs at once
    link = secrets.token_hex(8)
    function = modal.Function.from_name(APP_NAME, "haru")
    since = datetime.now(UTC) - timedelta(seconds=5)
    call = function.spawn(key=key, link=link)
    links[CURRENT] = {"call": call.object_id, "link": link}
    gpu_found = threading.Event()
    threading.Thread(target=follow_system_logs, args=(function, since, gpu_found), daemon=True).start()

    stage, url = ("", None), ""
    while True:
        try:
            info = links.get(link) or {}
        except Exception:  # noqa: BLE001 - transient Modal RPC blip
            info = {}
        if info and not gpu_found.is_set():
            gpu_found.set()
            emit(event="gpu", region=info.get("region", ""))
        if info.get("stage") and (info["stage"], info.get("cached")) != stage:
            stage = (info["stage"], info.get("cached"))
            emit(event="stage", stage=stage[0], cached=bool(stage[1]))
        if info.get("error"):
            emit(event="error", what=info["error"], why=info.get("tail", ""))
            return 1
        if info.get("url") and not url:
            url = info["url"]
            emit(event="url", url=url, region=info.get("region", ""))
        if url and ready(url):
            emit(event="ready")
            return 0
        why = ended(call)
        if why is not None:
            emit(event="error", what="Haru on Modal stopped while it started.", why=why)
            return 1
        emit(event="tick")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "stop", "status"))
    parser.add_argument("--key", default=os.environ.get("HARU_KIT_KEY"), help="default: $HARU_KIT_KEY")
    args = parser.parse_args()
    links = modal.Dict.from_name(LINKS_NAME, create_if_missing=True)

    if args.command == "stop":
        last_stop = None if running(links) else links.get(LAST_STOP)
        emit(event="stopped", was_running=stop(links), last_stop=last_stop)
        return 0

    if args.command == "status":
        link = running(links)
        emit(running=bool(link), url=link.get("url"), region=link.get("region"),
             last_stop=None if link else links.get(LAST_STOP))
        return 0

    return start(links, args.key)


if __name__ == "__main__":
    sys.exit(main())
