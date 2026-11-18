"""Modal way: deploy runtime/modal_app.py with the Modal tool of the "modal" env, start Haru in the region closest
to the user, wait, and print the private link (a direct tunnel to the GPU)."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time

from . import paths, steps, ui
from .detect import Facts, modal_logged_in
from .procs import CommandError, Failure, Runtime, child_env, run_logged
from .settings import Settings

APP_NAME = "haru-kit"
VOLUME = "haru-kit-model"  # same name as in runtime/modal_app.py
LINKS = "haru-kit-links"  # same name as in runtime/modal_app.py
GPU = "A10"  # nearly the same frame time as L40S, at a much lower hourly cost
PRICE = {"L4": 0.80, "A10": 1.10, "L40S": 1.95}
IDLE_MINUTES = 5  # same as in runtime/modal_app.py: after a conversation, 5 min with no conversation
FIRST_IDLE_MINUTES = 15  # same as in runtime/modal_app.py: the wait for the first conversation
ASK_AFTER = 3 * 60  # in a terminal: ask what to do after 3 min with no GPU, and again each 3 min
WAIT_LIMIT = 15 * 60  # without a terminal (or --yes): stop waiting for a GPU after 15 min
LOAD_LIMIT = 20 * 60  # from the GPU to ready

# Modal container regions that haru-kit chooses from (Modal: a broad region costs 1.15x, a narrow one 1.75x).
REGIONS = {"us": "North America", "eu": "Europe", "ap": "Asia-Pacific"}
REGION_CHOICES = ("auto", "any", *REGIONS)
# The loading stages that runtime/modal_app.py reports (its STAGES), as the user sees them.
STAGES = {"engine": "Start the engine", "load": "Load the model", "compile": "Compile", "warmup": "Warm up"}
LOAD_NOTE = "usually 5–12 min"
LOAD_NOTE_CACHED = "kernels from an earlier start · usually 2–4 min"
LOGS_FIX = (f"The GPU is stopped; nothing more is billed.\n"
            f"See the Modal logs: modal app logs {APP_NAME} (or https://modal.com/apps)\n"
            f"Then try again: haru-kit start --on modal")
_WAITING = re.compile(r"waiting to be scheduled on an? (?:GPU_)?(\w+) worker", re.I)
_LOG_REGION = re.compile(r"region=([\w-]+)")


def region_from_time_zone(zone: str | None, utc_offset_hours: float) -> str:
    """The broad Modal region nearest to the user, from the computer's time zone (no network, nothing sent)."""
    if zone:
        area = zone.split("/")[0]
        if area in ("America", "US", "Canada", "Brazil", "Chile", "Mexico"):
            return "us"
        if area in ("Europe", "Africa", "Atlantic", "Arctic", "GB", "Eire"):
            return "eu"
        if area in ("Asia", "Australia", "Pacific", "Indian", "Antarctica", "Japan", "ROK", "PRC", "Singapore",
                    "Hongkong", "NZ"):
            return "ap"
    if utc_offset_hours <= -2.5:
        return "us"
    return "eu" if utc_offset_hours <= 4 else "ap"


def local_time_zone() -> str | None:
    """IANA name of this computer's time zone (macOS, Linux), else None."""
    if os.environ.get("TZ"):
        return os.environ["TZ"].lstrip(":")
    target = os.path.realpath("/etc/localtime")
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else None


def choose_region(setting: str) -> str:
    """"" (any region) or a Modal region; "auto" picks from the time zone."""
    if setting == "any":
        return ""
    if setting != "auto":
        return setting
    return region_from_time_zone(local_time_zone(), time.localtime().tm_gmtoff / 3600)


def price(region: str, gpu_type: str = GPU) -> float:
    factor = 1.0 if not region else (1.75 if "-" in region else 1.15)
    return round(PRICE.get(gpu_type, PRICE[GPU]) * factor + 1e-9, 2)  # 1.265 -> 1.27


def region_text(region: str, setting: str) -> str:
    name = REGIONS.get(region, region) if region else "any region"
    return f"{name} (from your time zone)" if setting == "auto" and region else name


def _modal(way_args: list[str]) -> list[str]:
    return [str(paths.env_python("modal")), "-m", "modal", *way_args]


def _client(*args: str) -> list[str]:
    return [str(paths.env_python("modal")), str(paths.runtime_script("modal_client.py")), *args]


def login(facts: Facts) -> None:
    if facts.modal_login:
        return
    ui.console.print("  Modal is the cloud that runs Haru's GPU. You need a free Modal account.")
    ui.console.print("  New accounts get $30 of use each month.")
    ui.console.print()
    # Without a terminal (a coding agent, --yes in a script) nobody can answer: log in at once.
    if sys.stdin.isatty() and not ui.confirm("Log in now? A browser window opens.", yes="Log in", no="Not now"):
        raise Failure("Modal login is needed.", "Without a Modal account, haru-kit cannot start the GPU.",
                      "Run haru-kit start --on modal when you are ready.")
    subprocess.run(_modal(["setup"]), env=child_env())
    if not modal_logged_in():
        raise Failure("The Modal login did not finish.", "No Modal token was found.",
                      "Run haru-kit start --on modal again.")


def schedule_text(line: str) -> str:
    """A Modal system log line as a short plain message for the waiting step."""
    waiting = _WAITING.search(line)
    if waiting:
        gpu = {"A10G": "A10"}.get(waiting[1].upper(), waiting[1])
        region = _LOG_REGION.search(line)
        where = f" in {REGIONS.get(region[1], region[1])}" if region else ""
        return f"No {gpu} GPU free{where} yet; Modal is looking for one"
    text = " ".join(re.sub(r"^Function '[^']*' \([\w-]+\) ", "", line.strip()).split())
    return text if len(text) <= 70 else text[:69] + "…"


def wait_action(waited: float, asked_at: float, ask: bool) -> str | None:
    """While no GPU is found: "ask" the user (terminal), "timeout" (no terminal), or None to keep waiting."""
    if ask:
        return "ask" if waited - asked_at >= ASK_AFTER else None
    return "timeout" if waited >= WAIT_LIMIT else None


def _deploy(region: str, log, verbose: bool, *, gpu_type: str = GPU) -> None:
    text = "Send Haru to Modal" if region else "Send Haru to Modal (any region)"
    started = time.monotonic()
    with ui.Step(text) as step:
        try:
            run_logged(_modal(["deploy", str(paths.runtime_script("modal_app.py"))]), log, step.update, verbose,
                       {"HARU_KIT_GPU": gpu_type, "HARU_KIT_REGION": region})
        except CommandError as error:
            raise Failure("The Modal deploy failed.", "\n".join(error.tail[-8:]),
                          f"Full log: {log}\nCheck your Modal account, then run haru-kit start --on modal again.") \
                from None
    ui.done(text, ui.duration(time.monotonic() - started))


def _stop_call(client: Runtime) -> bool:
    """Stop the client and cancel its Modal call: True when confirmed stopped."""
    client.stop()
    try:
        res = subprocess.run(_client("stop"), capture_output=True, env=child_env(), timeout=30)
        if res.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        res = subprocess.run(_modal(["app", "stop", "--yes", APP_NAME]), capture_output=True,
                             env=child_env(), timeout=30)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ask(region: str, waited: float) -> str:
    rows, keys = [], []
    if region:
        rows.append(("Try any region", f"about ${price(''):.2f} an hour; the delay can be higher", True))
        keys.append("any")
    rows += [("Keep waiting", "ask again after 3 more min", True), ("Stop", "nothing is billed", True)]
    keys += ["wait", "stop"]
    title = f"No {GPU} GPU free in {region_text(region, 'any')} after {ui.duration(waited)}. What now?"
    return keys[ui.select(title, rows, 0)]


def _get_gpu(client: Runtime, region: str, setting: str, ask: bool) -> str:
    """Wait for a GPU. Returns "gpu:<region>", or the user's choice "any" or "stop"."""
    text = f"Get a GPU in {region_text(region, setting)}"
    started, asked_at, note = time.monotonic(), 0.0, "Modal is looking for a GPU"
    step = ui.Step(text).__enter__()
    try:
        for event in client.events():
            kind = event.get("event")
            waited = time.monotonic() - started
            if kind == "gpu":
                step.__exit__(None, None, None)
                step = None
                where = event.get("region") or region_text(region, "any")
                ui.done("Get a GPU", f"{ui.duration(waited)} · {GPU} in {where}")
                return f"gpu:{where}"
            if kind == "error":
                raise Failure(event.get("what", "Haru did not start on Modal."), event.get("why", ""), LOGS_FIX)
            if kind == "modal_log":
                note = schedule_text(event.get("text", ""))
            action = wait_action(waited, asked_at, ask)
            if action == "timeout":
                fix = ("Try any region: haru-kit start --on modal --region any" if region else
                       "Try again later: haru-kit start --on modal")
                raise Failure(f"Modal found no {GPU} GPU in {region_text(region, 'any')} in "
                              f"{WAIT_LIMIT // 60} minutes.", "Nothing was billed.", fix)
            if action == "ask":
                step.__exit__(None, None, None)
                step = None
                choice = _ask(region, waited)
                if choice != "wait":
                    return choice
                asked_at = time.monotonic() - started
                step = ui.Step(text).__enter__()
            step.update(f"{ui.duration(waited)} · usually under 3 min · you pay nothing while you wait", note)
    finally:
        if step is not None:
            step.__exit__(None, None, None)
    raise Failure("Haru did not start on Modal.", "\n".join(client.tail(6)), LOGS_FIX)


def _load(client: Runtime) -> str:
    """After the GPU is found: show the loading stages until Haru answers. Returns the link."""
    started, url, stage = time.monotonic(), "", "engine"
    with ui.StageStep("Load Haru", list(STAGES.values()), LOAD_NOTE) as step:
        for event in client.events():
            kind = event.get("event")
            if kind == "stage" and event.get("stage") in STAGES:
                stage = event["stage"]
                step.stage(list(STAGES).index(stage))
                step.note = LOAD_NOTE_CACHED if event.get("cached") else LOAD_NOTE
            elif kind == "url":
                url = event["url"].rstrip("/")
            elif kind == "ready" and url:
                break
            elif kind == "error":
                raise Failure(event.get("what", "Haru did not start on Modal."), event.get("why", ""), LOGS_FIX)
            if time.monotonic() - started > LOAD_LIMIT:
                raise Failure(f"Haru did not load on Modal in {LOAD_LIMIT // 60} minutes.",
                              f"It stopped at: {STAGES[stage]}.", LOGS_FIX)
        else:
            raise Failure("Haru did not start on Modal.", "\n".join(client.tail(6)), LOGS_FIX)
    ui.done("Load Haru", ui.duration(time.monotonic() - started))
    return url


def start_tunnel(
    facts: Facts,
    settings: Settings,
    verbose: bool,
    ask: bool,
    *,
    function: str = "haru",
    key: str | None = None,
    options: dict | None = None,
    gpu_override: str | None = None,
    retry_cmd: str = "haru-kit start --on modal",
) -> tuple[str, str, str, str] | None:
    """Deploy and start a Modal GPU container; returns ``(url, key, where, region)`` or ``None`` if cancelled."""
    steps.install_env("modal", facts, verbose)
    login(facts)
    region, setting = choose_region(settings.modal_region), settings.modal_region
    ask = ask and ui.interactive()
    key = key or secrets.token_urlsafe(24)
    gpu_type = gpu_override or GPU
    log = paths.log_file("modal")
    _deploy(region, log, verbose, gpu_type=gpu_type)
    while True:
        # Secrets go by environment: command lines are written to the log.
        env = {"HARU_KIT_KEY": key}
        if options:
            env["HARU_KIT_OPTIONS"] = json.dumps(options)
        client = Runtime(
            paths.env_python("modal"),
            paths.runtime_script("modal_client.py"),
            ["start", "--function", function],
            log,
            verbose,
            env=env,
        )
        try:
            outcome = _get_gpu(client, region, setting, ask)
            if outcome == "any":
                _stop_call(client)
                region, setting = "", "any"
                _deploy(region, log, verbose, gpu_type=gpu_type)  # the region is fixed at deploy
                continue
            if outcome == "stop":
                if _stop_call(client):
                    ui.done("Stopped. No GPU ran, so nothing was billed.")
                else:
                    ui.error("Stopped locally, and could not confirm Modal stopped.",
                             "Check https://modal.com/apps or run: haru-kit stop")
                ui.console.print(f"    Try again: {retry_cmd}")
                if region:
                    ui.console.print(f"    Or in any region: {retry_cmd} --region any")
                ui.console.print()
                return None
            where = outcome.removeprefix("gpu:")
            url = _load(client)
        except BaseException:
            # Ctrl+C or a failed start: do not leave a GPU running (and billing) behind.
            if not _stop_call(client):
                ui.error("Could not confirm that the Modal GPU stopped.",
                         "Your internet connection may have dropped.",
                         "Run `haru-kit stop` or check https://modal.com/apps.")
            raise
        client.stop()
        return url, key, where, region


def run(facts: Facts, settings: Settings, verbose: bool, ask: bool) -> None:
    """ask: False with --yes; then (as without a terminal) wait for a GPU up to WAIT_LIMIT and ask nothing."""
    started = start_tunnel(facts, settings, verbose, ask, function="haru")
    if started is None:
        return
    url, key, where, region = started
    open_by = time.strftime("%H:%M", time.localtime(time.time() + FIRST_IDLE_MINUTES * 60))
    ui.ready_modal(f"{url}/?key={key}", GPU, price(region), where, open_by, IDLE_MINUTES)


def stopped_text(last_stop: dict) -> str:
    """The last start ended by itself (the container's idle stop): when and why, in local time."""
    at = time.strftime("%H:%M", time.localtime(last_stop["at"]))
    why = ("no conversation" if last_stop.get("reason") == "no_first_conversation" else
           f"{IDLE_MINUTES} minutes with no conversation")
    return f"Haru stopped by itself at {at} ({why}). Start again: haru-kit start --on modal"


def _client_json(command: str) -> tuple[subprocess.CompletedProcess | None, dict]:
    try:
        result = subprocess.run(_client(command), capture_output=True, text=True, env=child_env(), timeout=30)
    except subprocess.TimeoutExpired:
        return None, {}
    try:
        return result, json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return result, {}


def stop() -> None:
    if not paths.env_python("modal").is_file():
        ui.console.print("  Haru is not set up on Modal. Nothing to stop.")
        return
    result, stopped = _client_json("stop")
    if result is None or result.returncode != 0:
        why = "Modal timed out." if result is None else (result.stderr or result.stdout).strip()[-400:]
        raise Failure("Haru on Modal did not stop.", why,
                      "Stop it on https://modal.com/apps, or try again: haru-kit stop")
    if stopped.get("last_stop"):
        ui.console.print(f"  {stopped_text(stopped['last_stop'])}")
        return
    ui.done("Haru on Modal is stopped. The GPU costs nothing now.")


def uninstall(ask: bool) -> None:
    """Stop Haru; ask before deleting the model copy and the saved kernels in the user's Modal account."""
    try:
        stop()
    except Failure as error:
        ui.error(error.what, error.why, error.fix)
    subprocess.run(_modal(["app", "stop", "--yes", APP_NAME]), capture_output=True, env=child_env(), timeout=30)
    subprocess.run(_modal(["dict", "delete", "--yes", LINKS]), capture_output=True, env=child_env(), timeout=30)
    if ask and ui.confirm(f"Also delete the model copy and the saved kernels in your Modal account (Volume {VOLUME})?",
                          yes="Delete it", no="Keep it", default=False):
        result = subprocess.run(_modal(["volume", "delete", "--yes", VOLUME]), capture_output=True, text=True,
                                env=child_env())
        if result.returncode == 0:
            ui.done("Removed", f"Modal Volume {VOLUME}")
        else:
            ui.error("The Modal Volume was not deleted.", result.stderr.strip()[-300:],
                     f"Delete it on https://modal.com/storage, or run: modal volume delete {VOLUME}")


def running_text() -> str:
    _result, status = _client_json("status")
    if not status:
        return "state unknown"
    if status.get("running"):
        return f"running in {status.get('region') or 'a region'} (stop it: haru-kit stop)"
    if status.get("last_stop"):
        return stopped_text(status["last_stop"])
    return "not running"

