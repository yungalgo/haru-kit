import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRINGS = ROOT / "haru_kit" / "apps" / "strings"


def test_string_tables_are_generated_from_the_shared_files():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_strings.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_language_has_every_key():
    english = set(json.loads((STRINGS / "en.json").read_text(encoding="utf-8")))
    assert not list(STRINGS.glob("pending-*.json")), "translate the pending keys into en.json and all languages"
    for lang in ("ja", "zh", "ko", "es", "fr", "de", "pt", "ru"):
        keys = set(json.loads((STRINGS / f"{lang}.json").read_text(encoding="utf-8")))
        assert keys == english, f"{lang}.json: missing {sorted(english - keys)}"
