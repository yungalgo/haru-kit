"""The same card files give the same rendered card text in TypeScript (web app) and Swift (Mac app).

Expected texts in tests/data/card_cases.json come from upstream ``haru.prompt.render_card`` (haru-speech 0.1.1).
The TypeScript check needs Node 22.6 or newer; the Swift check needs swiftc (macOS). Missing tools skip.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"
CASES = json.loads((DATA / "card_cases.json").read_text(encoding="utf-8"))


def expected() -> list[str]:
    return [case["expected"] for case in CASES]


def test_python_renderer_matches():
    from haru_kit.card import render_card

    actual = [render_card(case["card"], case["language"]) for case in CASES]
    assert actual == expected()


def _node_strips_types() -> bool:
    if not shutil.which("node"):
        return False
    version = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    major, minor = (int(x) for x in version.split(".")[:2])
    return (major, minor) >= (22, 6)


@pytest.mark.skipif(not _node_strips_types(), reason="needs Node 22.6 or newer")
def test_web_renderer_matches(tmp_path):
    script = tmp_path / "render.mjs"
    card_ts = ROOT / "haru_kit" / "apps" / "web" / "src" / "pages" / "Queue" / "card.ts"
    script.write_text(
        f"import {{ renderCard }} from {json.dumps(str(card_ts))};\n"
        f"import {{ readFileSync }} from 'node:fs';\n"
        f"const cases = JSON.parse(readFileSync({json.dumps(str(DATA / 'card_cases.json'))}, 'utf8'));\n"
        f"const t = JSON.parse(readFileSync({json.dumps(str(DATA / 'card_templates.json'))}, 'utf8')).templates;\n"
        "console.log(JSON.stringify(cases.map(c => renderCard(c.card, t[c.language]))));\n"
    )
    result = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", str(script)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected()


@pytest.mark.skipif(not shutil.which("swiftc"), reason="swiftc not found")
def test_mac_renderer_matches(tmp_path):
    main = tmp_path / "main.swift"
    main.write_text(
        "import Foundation\n"
        f"let cases = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: "
        f"{json.dumps(str(DATA / 'card_cases.json'))}))) as! [[String: Any]]\n"
        f"let all = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: "
        f"{json.dumps(str(DATA / 'card_templates.json'))}))) as! [String: Any]\n"
        "let templates = all[\"templates\"] as! [String: [String: Any]]\n"
        "let out = cases.map { c in renderCard(c[\"card\"] as! [String: String], "
        "templates[c[\"language\"] as! String]!) }\n"
        "print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)\n"
    )
    binary = tmp_path / "render"
    card = ROOT / "haru_kit" / "apps" / "mac" / "App" / "Card.swift"
    build = subprocess.run(["swiftc", str(card), str(main), "-o", str(binary)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert json.loads(result.stdout) == expected()
