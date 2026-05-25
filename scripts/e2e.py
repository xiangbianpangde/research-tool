#!/usr/bin/env python
"""端到端剧本 —— 覆盖升级计划 §6.2 的可执行版本。

P3（bilibili / BiliNote）场景尚未实现，已跳过。

用法：
    python scripts/e2e.py            # 快速：dry-run + 离线场景（不耗 LLM）
    python scripts/e2e.py --live     # 追加真实采集+深挖（联网 + 耗 LLM，数分钟）

退出码 0 = 全部通过；非 0 = 有场景失败。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# GBK 控制台下 ✅/中文 会崩，统一切到 UTF-8（与 cli.py 一致）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

PY = sys.executable
CLI = [PY, "-m", "research_tool.cli"]
ROOT = Path(__file__).resolve().parent.parent  # research-tool/

_results: list[tuple[str, bool, str]] = []


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    env = {**_env(), "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        CLI + args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, cwd=ROOT, env=env,
    )


_ENV_KEYS = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "tavily_api_key": "TAVILY_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}


def _env() -> dict:
    """复制当前环境，并从 .env 补齐密钥（CLI 默认不读 .env，与 webui 一致）。"""
    import os
    env = dict(os.environ)
    for cand in [ROOT / ".env", ROOT.parent / ".env", Path.home() / ".env"]:
        if not cand.exists():
            continue
        for line in cand.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            name = _ENV_KEYS.get(k.strip().lower())
            if name and not env.get(name):
                env[name] = v.strip()
        break
    return env


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    icon = "✅" if ok else "❌"
    print(f"{icon} {name}" + (f" — {detail}" if detail else ""))


# --------------------------------------------------------------------------- #
# 快速场景（dry-run / 离线，不耗 LLM）
# --------------------------------------------------------------------------- #
def scenario_stages_include_deepen() -> None:
    """测试5 反向：默认 run 含 deepen 阶段。"""
    r = _run(["run", "x", "-s", "web", "--dry-run"])
    out = r.stdout + r.stderr
    check("默认管道含 deepen", "deepen" in out, out.strip().splitlines()[-1] if out.strip() else "")


def scenario_skip_deepen() -> None:
    """测试5：--skip deepen 去掉深挖阶段。"""
    r = _run(["run", "x", "-s", "web", "--skip", "deepen", "--dry-run"])
    out = r.stdout + r.stderr
    check("--skip deepen 生效", "deepen" not in out and "collect" in out)


def scenario_multi_source_warnings() -> None:
    """测试1：多源采集 dry-run，搜索失败以 warning 可见（不静默）。"""
    r = _run(
        ["collect", "Transformer 架构", "-s", "web", "-s", "wikipedia",
         "-s", "semantic_scholar", "-s", "github", "--dry-run"],
        timeout=120,
    )
    out = r.stdout + r.stderr
    got_hits = "条结果" in out
    # 有命中即算通过；若某源失败，应以「警告」呈现而非静默
    check("多源 dry-run 出结果", got_hits, _last_line(out))
    if "警告" in out:
        check("失败源以 warning 呈现（可观测）", True,
              [ln for ln in out.splitlines() if "警告" in ln][0][:80])


# --------------------------------------------------------------------------- #
# 真实场景（--live：联网 + 耗 LLM）
# --------------------------------------------------------------------------- #
def scenario_deepen_resume(work: Path) -> None:
    """测试2：深挖跑通，且二次运行 resume 跳过 deepen。"""
    args = ["run", "图神经网络", "-s", "web", "-s", "arxiv",
            "--skip", "clean", "--skip", "extract",
            "--skip", "organize", "--skip", "report",
            "-o", str(work)]
    r1 = _run(args, timeout=600)
    out1 = r1.stdout + r1.stderr
    ran = "deepen" in out1 and "completed" in out1 or "完成" in out1
    marker = next(work.rglob(".deepen_done"), None)
    check("deepen 真实跑通", marker is not None, f"marker={marker}")

    r2 = _run(args, timeout=120)
    out2 = r2.stdout + r2.stderr
    check("二次运行 resume 跳过 deepen", "skipped" in out2 or "跳过" in out2,
          _last_line(out2))


def scenario_kang_bias(work: Path) -> None:
    """测试4：康怡琳偏差复测——深挖后 raw/ 应出现独立于中南民大的教育/学术线索。"""
    args = ["run", "康怡琳", "-s", "web", "-s", "wikipedia", "-s", "semantic_scholar",
            "--skip", "clean", "--skip", "extract",
            "--skip", "organize", "--skip", "report",
            "-o", str(work)]
    r = _run(args, timeout=900)
    raw_files = list(work.rglob("raw/*.md"))
    blob = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in raw_files)
    # 软判据：是否出现"博士/PhD/英文名/独立院校"等超出单一机构锚点的线索
    signals = [s for s in ("博士", "PhD", "Ph.D", "Kang", "南洋", "Nanyang", "本科", "硕士")
               if s in blob]
    check("康怡琳深挖产出资料", len(raw_files) > 0, f"{len(raw_files)} 个 raw 文件")
    check("含独立教育/学术线索（消偏差）", len(signals) >= 1,
          f"命中信号: {signals}")


def _last_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1][:100] if lines else ""


def main() -> int:
    live = "--live" in sys.argv
    print("=== 端到端剧本（升级计划 §6.2）===\n")

    print("[ 快速场景 ]")
    scenario_stages_include_deepen()
    scenario_skip_deepen()
    scenario_multi_source_warnings()

    if live:
        print("\n[ 真实场景（联网 + LLM）]")
        tmp = Path(tempfile.mkdtemp(prefix="rt-e2e-"))
        try:
            scenario_deepen_resume(tmp)
            scenario_kang_bias(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("\n（跳过真实场景；加 --live 启用，需联网 + 消耗 LLM）")

    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n=== {len(_results) - len(failed)}/{len(_results)} 通过 ===")
    if failed:
        print("失败：" + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
