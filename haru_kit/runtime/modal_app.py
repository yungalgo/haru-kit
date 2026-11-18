"""The Modal app: one GPU container with the upstream vLLM server and the upstream web server, reached through
a direct tunnel (modal.forward) in the region closest to the user.

Deployed and started by haru-kit with the Modal tool in the "modal" env:

    HARU_KIT_GPU=A10 HARU_KIT_REGION=ap modal deploy modal_app.py
    then: modal.Function.from_name("haru-kit", "haru").spawn(key=..., link=...)

Why a tunnel: Modal's web URLs route every request through its servers in Virginia (us-east); a tunnel connects
the browser straight to the container, so a user in Asia or Europe talks to a GPU near them without the trip
through the USA.

Settings from the environment at deploy time:
    HARU_KIT_GPU     GPU type (default: GPU_DEFAULT)
    HARU_KIT_REGION  Modal container region ("us", "eu", "ap", or a narrow one such as "ap-northeast");
                     empty: any region
"""

from __future__ import annotations

import base64
import collections
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

import modal

APP_NAME = "haru-kit"
VOLUME_NAME = "haru-kit-model"
LINKS_NAME = "haru-kit-links"  # modal.Dict: link id -> {"region", "started", "stage", "url", "error"} for haru-kit
LAST_STOP = "last_stop"  # links[LAST_STOP] = {"at": time, "reason": "no_first_conversation" | "idle"}
GPU_DEFAULT = "A10"
IDLE_MINUTES = 5  # after a conversation: stop after 5 minutes with no conversation
FIRST_IDLE_MINUTES = 15  # before the first conversation: the user may open the link later
LOAD_TIMEOUT_MINUTES = 25  # container-side safety cap if vLLM hangs during startup
PORT = 8998
HERE = Path(__file__).resolve().parent

# Kernels that vLLM, FlashInfer, Triton and torch.compile build at the first start on a GPU worker (about 2 min).
# They are built in CACHE_DIR, then kept as one archive in the Volume (CACHE_SAVED/<GPU and versions>.tar) and
# unpacked at the next start. One archive for each GPU type and version set: a cache never meets another GPU.
CACHE_DIR = Path("/root/haru-cache")
CACHE_SAVED = Path("/model/cache")
CACHE_ENV = {
    "VLLM_CACHE_ROOT": str(CACHE_DIR / "vllm"),
    "FLASHINFER_WORKSPACE_BASE": str(CACHE_DIR),  # FlashInfer adds .cache/flashinfer
    "TRITON_CACHE_DIR": str(CACHE_DIR / "triton"),
    "TORCHINDUCTOR_CACHE_DIR": str(CACHE_DIR / "inductor"),
}
CACHE_PACKAGES = ("vllm", "vllm-omni", "torch", "triton", "flashinfer-python", "haru-speech-vllm")

# Loading stages of the container, in order; haru-kit shows them. Ready: the web server answers /api/ready.
STAGES = ("engine", "load", "compile", "warmup")
_STAGE_LINES = (  # the last stage first: a line moves the stage forward only
    ("warmup", re.compile(r"Application startup complete|Starting vLLM API server \d+ on")),
    ("compile", re.compile(r"torch\.compile|Compiling a graph|Dynamo bytecode transform|Capturing CUDA graph", re.I)),
    ("load", re.compile(r"Starting to load model|Loading weights|Loading safetensors|Model loading took", re.I)),
)


def vllm_stage(line: str, current: str) -> str:
    """The loading stage after one vLLM output line; it never goes back (vLLM can load more than one model)."""
    for stage, pattern in _STAGE_LINES:
        if pattern.search(line):
            return stage if STAGES.index(stage) > STAGES.index(current) else current
    return current


app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
links = modal.Dict.from_name(LINKS_NAME, create_if_missing=True)

base_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .apt_install("ffmpeg")
    .uv_pip_install("vllm==0.32.0", "vllm-omni==0.32.0", "haru-speech==0.1.1", "haru-speech-vllm==0.1.1")
    .env({"HF_HOME": "/model/hf", "HF_XET_HIGH_PERFORMANCE": "1"})
)

image = (
    base_image
    .add_local_file(HERE / "web_server.py", "/haru_kit/runtime/web_server.py")
    .add_local_dir(HERE.parent / "static", "/haru_kit/static")
)

gateway_image = (
    base_image
    .uv_pip_install(
        "pipecat-ai[websocket,twilio,telnyx]==1.16.0",
        "fastapi>=0.115",
        "uvicorn[standard]>=0.32",
        "rich>=13",
    )
    .add_local_dir(
        HERE.parent,
        "/haru_kit_pkg/haru_kit",
        ignore=["apps", "static", "__pycache__", "*.pyc"],
    )
)

region = os.environ.get("HARU_KIT_REGION") or None


def _deploy_config_path(max_sessions: int = 2) -> str:
    deploy = subprocess.run([sys.executable, "-m", "haru_vllm.deploy"], capture_output=True, text=True,
                            check=True).stdout.strip()
    if max_sessions == 2:
        return deploy
    raw = Path(deploy).read_text(encoding="utf-8")
    raw = re.sub(r"(?m)^(\s*max_sessions:\s*)\d+", rf"\g<1>{max_sessions}", raw)
    raw = re.sub(r"(?m)^(\s*max_num_seqs:\s*)\d+", rf"\g<1>{max_sessions}", raw)
    custom = Path("/tmp/haru_deploy.yaml")
    custom.write_text(raw, encoding="utf-8")
    return str(custom)


def _start_vllm(max_sessions: int = 2) -> subprocess.Popen:
    deploy = _deploy_config_path(max_sessions)
    # A new GPU worker builds its kernels first (measured on A10: 5 min or more before the engine is ready), so wait
    # longer than vLLM-Omni's default of 300 s; haru-kit stops waiting after 20 minutes.
    return subprocess.Popen(["vllm", "serve", "higurashi-ai/Haru-4B", "--omni", "--deploy-config", deploy,
                             "--host", "127.0.0.1", "--port", "8000",
                             "--stage-init-timeout", "1200", "--init-timeout", "1200"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                            env={**os.environ, **CACHE_ENV, "PYTHONUNBUFFERED": "1"})


def _start_web(key: str) -> subprocess.Popen:
    return subprocess.Popen([
        sys.executable, "/haru_kit/runtime/web_server.py", "--engine", "vllm", "--vllm-url", "http://127.0.0.1:8000",
        "--host", "0.0.0.0", "--port", str(PORT),
        "--network-profile", "internet", "--max-sessions", "2", "--access-key", key,
        "--idle-minutes", str(IDLE_MINUTES), "--first-idle-minutes", str(FIRST_IDLE_MINUTES),
        "--load-timeout-minutes", str(LOAD_TIMEOUT_MINUTES),
    ], stdout=subprocess.PIPE, text=True, errors="replace")


def cache_name(gpu: str, versions: dict[str, str]) -> str:
    """Archive name for one GPU type and one set of package versions, e.g.
    "nvidia-a10_vllm-0.32.0_vllm-omni-0.32.0_torch-2.13.0_triton-3.7.1_flashinfer-python-0.7.0.post1.tar"."""
    slug = re.sub(r"[^a-z0-9.]+", "-", gpu.lower()).strip("-")
    return "_".join([slug, *(f"{package}-{version}" for package, version in versions.items())]) + ".tar"


def _cache_archive() -> Path:
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                         text=True, check=True).stdout.splitlines()[0]
    versions = {package: importlib.metadata.version(package) for package in CACHE_PACKAGES}
    return CACHE_SAVED / cache_name(gpu, versions)


def _restore_cache(archive: Path) -> bool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        return False
    try:
        with tarfile.open(archive) as tar:
            tar.extractall(CACHE_DIR, filter="data")
        return True
    except (OSError, tarfile.TarError) as error:  # a broken archive: wipe partial files, build again
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"haru-kit: saved kernels not used: {error}", flush=True)
        return False


def _save_cache_when_ready(archive: Path, stopping: threading.Event) -> None:
    """After the first ready on this GPU type and versions: keep the built kernels in the Volume."""
    while not stopping.is_set():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ready", timeout=10) as response:
                if json.load(response).get("ready"):
                    break
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(5)
    if stopping.is_set():
        return
    started = time.monotonic()
    partial = archive.with_suffix(".partial")
    try:
        with tarfile.open(partial, "w") as tar:
            tar.add(CACHE_DIR, arcname=".")
        partial.replace(archive)
    except (OSError, tarfile.TarError) as error:  # the next start builds the kernels again and tries again
        partial.unlink(missing_ok=True)
        print(f"haru-kit: kernels not saved: {error}", flush=True)
        return
    # Archives of older versions for this GPU type, and parts of archives that a stop cut off.
    slug = archive.name.split("_", 1)[0]
    for old in [*CACHE_SAVED.glob(f"{slug}_*.tar"), *CACHE_SAVED.glob("*.partial")]:
        if old != archive:
            old.unlink(missing_ok=True)
    volume.commit()
    print(f"haru-kit: kernels saved ({archive.stat().st_size / 1e6:.0f} MB, "
          f"{time.monotonic() - started:.0f} s): {archive.name}", flush=True)


@app.function(
    image=image,
    gpu=os.environ.get("HARU_KIT_GPU") or GPU_DEFAULT,
    region=region,
    volumes={"/model": volume},
    timeout=2 * 3600,  # covers load + the 60-minute conversation limit; the idle stop ends the container early
    max_containers=1,  # one Haru container at a time
    scaledown_window=2,  # each start is one call: do not keep (and bill) the GPU after it ends
)
def haru(key: str, link: str) -> None:
    """Run Haru until the web server stops (no conversation: see IDLE_MINUTES and FIRST_IDLE_MINUTES), or until
    haru-kit stops the app."""
    state = {"region": os.environ.get("MODAL_REGION", ""), "started": time.time(), "stage": STAGES[0]}
    lock = threading.Lock()
    stopping = threading.Event()

    def publish(**changes) -> None:
        with lock:
            if not stopping.is_set():
                state.update(changes)
                links[link] = dict(state)

    vllm = web = idle_stop = None
    try:
        publish()  # haru-kit: a GPU is found
        CACHE_SAVED.mkdir(parents=True, exist_ok=True)
        archive = _cache_archive()
        cached = _restore_cache(archive)
        publish(cached=cached)
        vllm = _start_vllm()
        web = _start_web(key)
        if not cached:
            threading.Thread(target=_save_cache_when_ready, args=(archive, stopping), daemon=True).start()

        def watch_vllm() -> None:
            # Pass vLLM's output on to the container log, and report the loading stage.
            tail: collections.deque[str] = collections.deque(maxlen=3)
            last_error = ""
            assert vllm.stdout is not None
            for line in vllm.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                tail.append(line.rstrip())
                if re.search(r"\w(Error|Exception)\b", line):
                    last_error = line.strip()
                stage = vllm_stage(line, state["stage"])
                if stage != state["stage"]:
                    publish(stage=stage)
            # The web server waits for vLLM; if vLLM ends first, end the container instead of billing for it.
            code = vllm.wait()
            if not stopping.is_set():
                print(f"haru-kit: the vLLM server stopped (exit code {code}); stopping", flush=True)
                publish(error=f"The vLLM server stopped (exit code {code}).", tail=last_error[-600:] or "\n".join(tail))
                web.terminate()

        threading.Thread(target=watch_vllm, daemon=True).start()
        with modal.forward(PORT) as tunnel:
            publish(url=tunnel.url)
            assert web.stdout is not None
            for line in web.stdout:  # ends at the idle stop
                sys.stdout.write(line)
                sys.stdout.flush()
                if '"idle_stop"' in line:
                    idle_stop = json.loads(line)
            web.wait()
    finally:
        stopping.set()
        for proc in (web, vllm):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if idle_stop:  # haru-kit status tells the user why and when Haru stopped
            links[LAST_STOP] = {"at": time.time(), "reason": idle_stop.get("reason", "idle")}
        if not state.get("error"):  # haru-kit reads the error; its next start or stop removes it
            links.pop(link, None)
        volume.commit()


def _start_gateway(key: str, options: dict) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "/haru_kit_pkg/haru_kit/runtime/modal_gateway.py",
        "--vllm-url", "http://127.0.0.1:8000",
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--max-sessions", str(int(options.get("max_sessions") or 2)),
        "--character", str(options.get("character") or "haru"),
        "--language", str(options.get("language") or "en"),
        "--idle-minutes", str(float(options.get("idle_minutes", IDLE_MINUTES))),
        "--first-idle-minutes", str(float(options.get("first_idle_minutes", FIRST_IDLE_MINUTES))),
        "--load-timeout-minutes", str(LOAD_TIMEOUT_MINUTES),
    ]
    if options.get("speak_first"):
        cmd.append("--speak-first")
    if options.get("card_text"):
        card_path = Path("/tmp/haru_custom_card.txt")
        card_path.write_text(str(options["card_text"]), encoding="utf-8")
        cmd += ["--card-file", str(card_path)]
    if options.get("voice_b64"):
        ext = str(options.get("voice_ext") or ".flac")
        if not ext.startswith("."):
            ext = f".{ext}"
        voice_path = Path(f"/tmp/haru_custom_voice{ext}")
        voice_path.write_bytes(base64.b64decode(options["voice_b64"]))
        cmd += ["--voice-file", str(voice_path)]
    if options.get("preroll_b64"):
        preroll_path = Path("/tmp/haru_preroll.flac")
        preroll_path.write_bytes(base64.b64decode(options["preroll_b64"]))
        cmd += ["--preroll-file", str(preroll_path)]

    child_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "HARU_API_KEY": key,
        "HARU_BRIDGE_TOKEN": str(options.get("bridge_token") or key),
    }
    for env_key, opt_key in (
        ("TWILIO_ACCOUNT_SID", "twilio_sid"),
        ("TWILIO_AUTH_TOKEN", "twilio_token"),
        ("TELNYX_API_KEY", "telnyx_key"),
    ):
        if options.get(opt_key):
            child_env[env_key] = str(options[opt_key])
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace", env=child_env)


@app.function(
    image=gateway_image,
    gpu=os.environ.get("HARU_KIT_GPU") or GPU_DEFAULT,
    region=region,
    volumes={"/model": volume},
    timeout=2 * 3600,
    max_containers=1,
    scaledown_window=2,
)
def serve_gateway(key: str, link: str, options: dict | None = None) -> None:
    """Run ``vllm serve`` and ``modal_gateway.py`` (realtime API + optional Twilio/Telnyx phone bridge)."""
    opts = options or {}
    max_sessions = max(1, int(opts.get("max_sessions") or 2))
    state = {
        "region": os.environ.get("MODAL_REGION", ""),
        "started": time.time(),
        "stage": STAGES[0],
        "mode": str(opts.get("mode") or "serve"),
    }
    lock = threading.Lock()
    stopping = threading.Event()

    def publish(**changes) -> None:
        with lock:
            if not stopping.is_set():
                state.update(changes)
                links[link] = dict(state)

    vllm = gateway = idle_stop = None
    try:
        publish()
        CACHE_SAVED.mkdir(parents=True, exist_ok=True)
        archive = _cache_archive()
        cached = _restore_cache(archive)
        publish(cached=cached)
        vllm = _start_vllm(max_sessions=max_sessions)
        gateway = _start_gateway(key, opts)
        if not cached:
            threading.Thread(target=_save_cache_when_ready, args=(archive, stopping), daemon=True).start()

        def watch_vllm() -> None:
            tail: collections.deque[str] = collections.deque(maxlen=3)
            last_error = ""
            assert vllm.stdout is not None
            for line in vllm.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                tail.append(line.rstrip())
                if re.search(r"\w(Error|Exception)\b", line):
                    last_error = line.strip()
                stage = vllm_stage(line, state["stage"])
                if stage != state["stage"]:
                    publish(stage=stage)
            code = vllm.wait()
            if not stopping.is_set():
                print(f"haru-kit: the vLLM server stopped (exit code {code}); stopping", flush=True)
                publish(error=f"The vLLM server stopped (exit code {code}).", tail=last_error[-600:] or "\n".join(tail))
                gateway.terminate()

        threading.Thread(target=watch_vllm, daemon=True).start()
        with modal.forward(PORT) as tunnel:
            publish(url=tunnel.url)
            assert gateway.stdout is not None
            for line in gateway.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                if '"idle_stop"' in line:
                    idle_stop = json.loads(line)
            gateway.wait()
    finally:
        stopping.set()
        for proc in (gateway, vllm):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if idle_stop:
            links[LAST_STOP] = {"at": time.time(), "reason": idle_stop.get("reason", "idle")}
        if not state.get("error"):
            links.pop(link, None)
        volume.commit()
