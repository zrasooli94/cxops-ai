"""Reproducible measurement of the API process's import/start-up cost (1T).

Each sample is a fresh interpreter importing ``app.main`` (exactly what
``uvicorn app.main:app`` does before it can bind its port) under
``-X importtime``. It reports wall time, the share spent in each top-level
package, and whether the OpenAI SDK was loaded at import.

    .venv/bin/python -m scripts.benchmark_startup --runs 5

To emulate a CPU-limited instance, run the same command inside the production
image: ``docker run --rm --cpus 0.1 <image> python -m scripts.benchmark_startup``
(Render Free is 0.1 CPU; Starter 0.5). It never connects to a database and
reads no secret; it runs with a throwaway ``DATABASE_URL``.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_LINE = re.compile(r"import time:\s+(\d+) \|\s+(\d+) \|\s*(\S+)")
_PROBE = (
    "import sys, time\n"
    "t = time.perf_counter()\n"
    "import app.main\n"
    "elapsed = time.perf_counter() - t\n"
    "print('RESULT', elapsed, 'openai' in sys.modules, 'langchain_openai' in "
    "sys.modules, 'langgraph' in sys.modules)\n"
)


def sample() -> dict:
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://bench:bench@127.0.0.1:1/bench",
        "OPENAI_API_KEY": "benchmark-not-a-key",
        "PYTHONPATH": str(REPO_ROOT),
    }
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        check=False,
    )
    wall = time.perf_counter() - started
    result = next(
        (line for line in proc.stdout.splitlines() if line.startswith("RESULT")), None
    )
    if result is None:
        raise SystemExit(f"import failed:\n{proc.stderr[-800:]}")
    _tag, elapsed, openai, lc_openai, langgraph = result.split()
    packages: collections.Counter[str] = collections.Counter()
    for line in proc.stderr.splitlines():
        match = _LINE.match(line)
        if match:
            packages[match.group(3).split(".")[0]] += int(match.group(1))
    return {
        "import_seconds": float(elapsed),
        "process_wall_seconds": round(wall, 3),
        "openai_loaded": openai == "True",
        "langchain_openai_loaded": lc_openai == "True",
        "langgraph_loaded": langgraph == "True",
        "top_self_ms": {k: v // 1000 for k, v in packages.most_common(8)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    samples = [sample() for _ in range(max(1, args.runs))]
    seconds = [s["import_seconds"] for s in samples]
    report = {
        "runs": len(samples),
        "import_seconds": {
            "min": round(min(seconds), 3),
            "median": round(statistics.median(seconds), 3),
            "max": round(max(seconds), 3),
        },
        "openai_loaded_at_import": any(s["openai_loaded"] for s in samples),
        "langgraph_loaded_at_import": any(s["langgraph_loaded"] for s in samples),
        "top_self_ms_last_run": samples[-1]["top_self_ms"],
    }
    print(json.dumps(report, indent=None if args.json else 2))


if __name__ == "__main__":
    main()
