"""Runs inside a way's env: download the Haru model into the Hugging Face cache, with JSON progress lines.

    python download.py [--repo higurashi-ai/Haru-4B]

Prints {"event": "progress", "name": "download", "done": N, "total": N}, then {"event": "done", "path": ...}.
An interrupted download continues on the next run (huggingface_hub resumes partial files).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time

FILES = ("config.json", "model.safetensors", "tokenizer.json")
_print_lock = threading.Lock()


def emit(**event) -> None:
    with _print_lock:
        print(json.dumps(event), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="higurashi-ai/Haru-4B")
    args = parser.parse_args()

    try:
        from huggingface_hub import HfApi, snapshot_download
        from tqdm.auto import tqdm
    except ImportError as error:
        emit(event="error", what="The engine is not complete.", why=str(error), fix="Run haru-kit again.")
        return 1

    try:
        info = HfApi().model_info(args.repo, files_metadata=True)
    except Exception as error:  # network or Hugging Face down
        emit(event="error", what="Could not reach Hugging Face.", why=str(error).splitlines()[0][:200],
             fix="Check the internet connection, then run haru-kit again.")
        return 1
    total = sum((s.size or 0) for s in info.siblings or [] if s.rfilename in FILES)
    emit(event="progress", name="download", done=0, total=total)

    lock = threading.Lock()
    state = {"done": 0, "last_emit": 0.0}

    class ProgressBar(tqdm):
        def __init__(self, *bar_args, **kwargs):
            name = kwargs.pop("name", "")
            self._track = kwargs.get("unit") == "B" and not str(name).endswith(".transfer")
            kwargs["file"] = io.StringIO()
            kwargs["disable"] = False
            super().__init__(*bar_args, **kwargs)

        def update(self, n=1):
            super().update(n)
            if not self._track or not total:
                return
            now = time.monotonic()
            with lock:
                done = min(max(state["done"], int(self.n)), total)
                if done > state["done"] and (now - state["last_emit"] >= 0.25 or done >= total):
                    state["done"] = done
                    state["last_emit"] = now
                    emit(event="progress", name="download", done=done, total=total)

    try:
        path = snapshot_download(args.repo, revision=info.sha, allow_patterns=list(FILES), tqdm_class=ProgressBar)
    except Exception as error:
        emit(event="error", what="The model download stopped.", why=str(error).splitlines()[0][:200],
             fix="Run haru-kit again. The download continues where it stopped.")
        return 1
    emit(event="progress", name="download", done=total, total=total)
    emit(event="done", path=str(path))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
