"""Run the local nine-stage acceptance suite without external credentials."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    targets = [
        "test_nine_loop_runner.py",
        "test_nine_loop_runtime_state.py",
        "test_nine_loop_graph.py",
        "test_nine_loop_evidence.py",
        "test_nine_loop_entrypoints.py",
    ]
    result = subprocess.run(  # noqa: S603 - fixed test targets with the active interpreter
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *[str(root / "research_tool" / "tests" / name) for name in targets]],
        cwd=root,
        env={**os.environ, "RESEARCH_DISABLE_DOTENV": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
