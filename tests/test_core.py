import subprocess
import sys
from pathlib import Path

import pytest

from haru_kit import detect, envs, paths, procs, ui, ways
from haru_kit.detect import Facts, Gpu
from haru_kit.settings import Settings


def mac(memory=36.0, version="15.4", arch="arm64"):
    return Facts(os="mac", arch=arch, chip="Apple M3 Pro", memory_gb=memory, os_version=version)


def pc(os_name="windows", gpu_gb=24.0, cuda=(12, 8), gpu=True):
    gpus = [Gpu("NVIDIA GeForce RTX 3090", gpu_gb, "571.96", cuda)] if gpu else []
    return Facts(os=os_name, arch="amd64", memory_gb=64, gpus=gpus, nvidia_smi=gpu)


def table(facts):
    return {w.key: (w.possible, w.recommended, w.reason) for w in ways.evaluate(facts)}


# Every row of the hardware and OS detection rules.
@pytest.mark.parametrize("facts, expected", [
    (mac(), {"mac": (True, True, ""), "nvidia": (False, False, "needs an NVIDIA GPU"), "modal": (True, False, "")}),
    (mac(memory=8), {"mac": (False, False, "needs 16 GB memory; this Mac has 8 GB"), "modal": (True, True, "")}),
    (mac(version="12.7"), {"mac": (False, False, "needs macOS 13 or later"), "modal": (True, True, "")}),
    (mac(arch="x86_64"), {"mac": (False, False, "needs Apple silicon"), "modal": (True, True, "")}),
    (pc(), {"mac": (False, False, "needs a Mac"), "nvidia": (True, True, ""), "modal": (True, False, "")}),
    (pc("linux"), {"nvidia": (True, True, ""), "modal": (True, False, "")}),
    (pc(gpu_gb=8), {"nvidia": (False, False, "needs 12 GB GPU memory; this GPU has 8 GB"), "modal": (True, True, "")}),
    (pc(cuda=(11, 8)), {"nvidia": (False, False, f"update the NVIDIA driver (CUDA 12 or later): {ways.DRIVER_URL}"),
                        "modal": (True, True, "")}),
    (pc(gpu=False), {"nvidia": (False, False, "no NVIDIA GPU found"), "modal": (True, True, "")}),
    (Facts(os="other", arch="riscv64"), {"mac": (False, False, "needs a Mac"), "modal": (True, True, "")}),
])
def test_way_rules(facts, expected):
    result = table(facts)
    for key, value in expected.items():
        assert result[key] == value
    assert sum(r[1] for r in result.values()) == 1


def test_space_counts_only_missing_items_and_margin(tmp_path):
    items = [ways.Item("env", 2.0, tmp_path, done=True), ways.Item("model", 10.5, tmp_path),
             ways.Item("cloud", 0, None)]
    [space] = ways.space(items, free_gb=lambda _p: 11.0)
    assert space.need_gb == pytest.approx(12.5)
    assert not space.ok
    assert ways.space([ways.Item("model", 10.5, tmp_path, done=True)], free_gb=lambda _p: 1) == []


def test_plan_items_per_way():
    assert [i.text for i in ways.plan("mac", "app", {})][-1] == "Build and install the Mac app"
    assert len(ways.plan("mac", "browser", {"env": True})) == 2
    assert ways.plan("mac", "browser", {"env": True})[0].done
    assert ways.plan("modal", None, {})[1].size_gb == 0


def test_settings_round_trip(tmp_path):
    path = tmp_path / "s.json"
    assert Settings.load(path) == Settings()
    Settings(way="mac", front="app", port=9000).save(path)
    assert Settings.load(path).front == "app" and Settings.load(path).port == 9000
    path.write_text("not json")
    assert Settings.load(path) == Settings()


def test_cli_imports_no_engine():
    code = ("import sys, haru_kit.cli, haru_kit.mac, haru_kit.nvidia, haru_kit.modal;"
            "bad=[m for m in ('torch','mlx','haru','haru_mlx','vllm','modal','numpy') if m in sys.modules];"
            "print(bad); sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_parsers():
    assert detect.parse_memsize("38654705664") == 36
    assert detect.parse_meminfo("MemTotal:       16303428 kB\nMemFree: 1 kB") == pytest.approx(15.55, 0.01)
    gpus = detect.parse_nvidia_smi("NVIDIA GeForce RTX 3090, 24576, 571.96\nNVIDIA T400, 2048, 571.96\n")
    assert [g.memory_gb for g in gpus] == [24, 2]
    header = "| NVIDIA-SMI 571.96   Driver Version: 571.96   CUDA Version: 12.8 |"
    assert detect.parse_cuda_version(header) == (12, 8)
    assert detect.parse_cuda_version("garbage") is None
    new = "| NVIDIA-SMI 610.57.04   KMD Version: 610.57.04   CUDA UMD Version: 13.3 |"
    assert detect.parse_cuda_version(new) == (13, 3)
    assert detect.cuda_from_driver("571.96") == (12, 8)
    assert detect.cuda_from_driver("470.1") == (11, 0)


def test_ssh_and_display():
    assert detect.in_ssh({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"})
    assert not detect.has_display("windows", {"SSH_CLIENT": "x"})
    assert detect.has_display("windows", {})
    assert not detect.has_display("linux", {})
    assert detect.has_display("linux", {"WAYLAND_DISPLAY": "wayland-0"})


def test_modal_login(tmp_path):
    assert detect.modal_logged_in({"MODAL_TOKEN_ID": "a", "MODAL_TOKEN_SECRET": "b"}, tmp_path)
    assert not detect.modal_logged_in({}, tmp_path)
    (tmp_path / ".modal.toml").write_text('[default]\ntoken_id = "x"\n')
    assert detect.modal_logged_in({}, tmp_path)


# Windows logic without hardware.
def test_windows_folders(monkeypatch):
    home = paths.home_dir("windows", {"LOCALAPPDATA": r"C:\Users\a\AppData\Local"})
    assert home == Path(r"C:\Users\a\AppData\Local") / "haru-kit"
    assert paths.env_python("nvidia", "windows").parts[-2:] == ("Scripts", "python.exe")
    assert paths.home_dir("linux", {"XDG_DATA_HOME": "/x"}) == Path("/x/haru-kit")
    assert paths.hf_home({"HF_HOME": "/h"}) == Path("/h")


def test_uv_commands_windows_and_cuda():
    facts = pc()
    cmds = envs.commands("nvidia", facts, "uv.exe")
    assert cmds[0][:4] == ["uv.exe", "venv", "--python", "3.12"]
    assert cmds[1][-2:] == ["--torch-backend", "cu128"]
    assert envs.torch_backend(pc(cuda=(12, 4))) == "cu126"
    assert "install.ps1" in envs.uv_install_line("windows")
    assert all(isinstance(a, str) for c in cmds for a in c)


def test_stop_uses_ctrl_break_on_windows(monkeypatch):
    sent = []

    class Proc:
        pid = 1
        def poll(self): return None
        def send_signal(self, sig): sent.append(sig)
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(procs, "WINDOWS", True)
    monkeypatch.setattr(procs.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(procs.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    procs.stop(Proc())
    assert sent == [1]
    assert "creationflags" in procs.popen_flags()


def test_stop_ends_a_real_child():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **procs.popen_flags())
    procs.stop(proc, timeout=5)
    assert proc.poll() is not None


def test_screens_render(capsys):
    ui.header()
    ui.facts(mac())
    from unittest import mock

    with mock.patch.object(ui.Prompt, "ask", return_value="1"):
        chosen = ui.choose_way(ways.evaluate(mac(memory=8)))  # not a terminal: the numbered question
    assert chosen.key == "modal"
    ok = ui.plan(ways.plan("mac", "app", {}), [ways.Space(Path("/"), 13, 40)], "about 15 minutes")
    bad = ui.plan(ways.plan("mac", "app", {}), [ways.Space(Path("/"), 13, 9)], None)
    ui.ready_browser("http://localhost:8998", True)
    ui.ready_ssh(8998, "me", "gpu-box")
    ui.ready_app()
    ui.ready_modal("https://x.modal.run/?key=k", "A10", 1.27, "Asia-Pacific", "11:01", 5)
    out = capsys.readouterr().out
    assert "11:01" in out
    out += capsys.readouterr().out
    assert ok and not bad
    assert "not possible: needs 16 GB memory" in out
    assert "Not enough free space" in out and "export HF_HOME" in out
    assert "ssh -L 8998:localhost:8998 me@gpu-box" in out


@pytest.mark.parametrize("zone, offset, region", [
    ("Asia/Seoul", 9, "ap"), ("Australia/Sydney", 10, "ap"), ("Europe/Berlin", 1, "eu"), ("Africa/Lagos", 1, "eu"),
    ("America/Los_Angeles", -8, "us"), ("America/Sao_Paulo", -3, "us"), (None, 9, "ap"), (None, 2, "eu"),
    (None, -5, "us"), (None, 5.5, "ap"),
])
def test_modal_region_from_time_zone(zone, offset, region):
    from haru_kit import modal

    assert modal.region_from_time_zone(zone, offset) == region


def test_modal_region_choice_and_price(monkeypatch):
    from haru_kit import modal

    monkeypatch.setattr(modal, "local_time_zone", lambda: "Asia/Seoul")
    assert modal.choose_region("auto") == "ap"
    assert modal.choose_region("any") == "" and modal.choose_region("eu") == "eu"
    assert modal.price("") == pytest.approx(1.10) and modal.price("ap") == pytest.approx(1.27)
    assert modal.price("ap-northeast") == pytest.approx(1.93)


def test_rich_markup_in_errors_does_not_crash():
    with ui.console.capture() as captured:
        ui.error("Build failed [test]", "clang: error: [/Users/dev/path] unmatched [/tag]", "Fix: [run] command")
    text = captured.get()
    assert "[/Users/dev/path]" in text and "[/tag]" in text


def test_uninstall_yes_preserves_user_library(tmp_path, monkeypatch):
    from haru_kit import cli

    home = tmp_path / "haru-kit"
    library = home / "library" / "characters"
    library.mkdir(parents=True)
    char_file = library / "u-123456.character.json"
    char_file.write_text('{"id": "u-123456"}', encoding="utf-8")
    (home / "settings.json").write_text('{"way": "mac"}', encoding="utf-8")
    (home / "envs" / "mac").mkdir(parents=True)
    app = tmp_path / "Haru.app"
    app.mkdir()
    monkeypatch.setattr(paths, "home_dir", lambda *a, **kw: home)
    monkeypatch.setattr(paths, "app_path", lambda: app)
    monkeypatch.setattr(paths, "model_dir", lambda *a, **kw: tmp_path / "no-model")

    assert cli.main(["uninstall", "--yes"]) == 0
    assert not app.exists()
    assert not (home / "settings.json").exists()
    assert not (home / "envs").exists()
    assert char_file.is_file()


def test_invalid_port_does_not_corrupt_settings(tmp_path, monkeypatch):
    from haru_kit import cli

    s_file = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_file", lambda: s_file)
    assert cli.main(["start", "--port", "99999", "--yes"]) == 1
    assert not s_file.exists()


def test_app_source_hash_ignores_build_and_tests(tmp_path, monkeypatch):
    from haru_kit import mac as mac_mod

    pkg = tmp_path / "pkg"
    app_src = pkg / "apps" / "mac"
    (app_src / "App").mkdir(parents=True)
    (app_src / "App" / "HaruApp.swift").write_text("// app", encoding="utf-8")
    monkeypatch.setattr(paths, "package_dir", lambda: pkg)
    baseline = mac_mod.app_source_hash()

    (app_src / "build" / "Haru.app").mkdir(parents=True)
    (app_src / "build" / "Haru.app" / "Info.plist").write_text("secret", encoding="utf-8")
    (app_src / "Tests").mkdir()
    (app_src / "Tests" / "Extra.swift").write_text("// test", encoding="utf-8")
    assert mac_mod.app_source_hash() == baseline
