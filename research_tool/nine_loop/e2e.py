"""E2E chain runner — P3 mid-closeout (task RT-RF-P3-E2E-MID-01).

Composes the five accepted stages in order:
    ① collect → ④ knowledge-network → ⑤ inspect → ⑧ quality-gate → ⑨ report
Every stage-boundary artifact is persisted ATOMICALLY (same-dir tmp +
os.replace) under the work directory together with a state manifest; the
``--resume`` mode (and the equivalent in-process API) replays only the stages
that were not yet durably committed, driven by the idempotency key + the
persisted stage outputs. A resumed chain produces byte-identical artifacts
and executes each stage at most once. Re-running the same input against the
same work directory is a no-op delta (zero new executions, unchanged
artifacts).

Determinism: identical input bytes ⇒ byte-identical final report envelope.
Offline and stdlib-only; all stage-boundary/crash state lives under the
caller-provided work directory (task scratch in tests).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from typing import Any, Callable


from . import collect_stage  # noqa: E402
from . import inspect_min  # noqa: E402
from . import knowledge_min  # noqa: E402
from . import qgate_min  # noqa: E402
from . import report_min  # noqa: E402
from .chain_state import (  # noqa: E402
    STATE_VERSION,
    E_IDEMPOTENCY_CONFLICT,
    E_STATE,
    E_CAS_CONFLICT,
    ChainStateFault,
    E2EFault,
    sha256_bytes,
    sha256_file,
    write_atomic,
    ChainState,
    canonical_stage_name,
)

STAGES = ("collect", "network", "inspect", "gate", "report")


# --------------------------------------------------------------------------- #
# Chain execution
# --------------------------------------------------------------------------- #
class E2EChain:
    """Five-stage composition with durable per-stage artifacts + resume."""

    def __init__(self, client, work_dir,
                 fault_hook: Callable[[str], None] | None = None,
                 execution_log: list[str] | None = None):
        self.client = client
        self.work_dir = pathlib.Path(work_dir)
        self.state = ChainState(self.work_dir)
        self.fault_hook = fault_hook
        self.execution_log = execution_log if execution_log is not None else []

    def run(self, collect_request: dict[str, Any]) -> dict[str, Any]:
        return self._execute(collect_request, resume=False)

    def run_resume(self, collect_request: dict[str, Any]) -> dict[str, Any]:
        return self._execute(collect_request, resume=True)

    def _execute(self, collect_request: dict[str, Any],
                 resume: bool) -> dict[str, Any]:
        input_key = str(collect_request.get("idempotency_key"))
        state = self.state.load(input_key)
        if not resume:
            if state["done"]:
                raise E2EFault(
                    E_IDEMPOTENCY_CONFLICT,
                    "work dir already contains a chain for this input; "
                    "use resume mode")
        done = set(state["done"])

        # ① collect (needs the real pinned child via the adapter client)
        if "collect" in done:
            collect_env = self.state.read_stage(state, "collect")
        else:
            outcome = collect_stage.run_collect_outcome(collect_request,
                                                        self.client)
            collect_env = outcome.envelope
            if collect_env.get("error") is not None:
                raise E2EFault(
                    f"collect.{collect_env['error']['code']}",
                    str(collect_env["error"]["safe_message"]))
            self.execution_log.append("collect")
            state = self.state.commit_stage(state, "collect", collect_env)
            self._fault("collect")

        # ④ knowledge-network
        if "network" in done:
            net_env = self.state.read_stage(state, "network")
        else:
            net_env = knowledge_min.run_network(
                knowledge_min.request_from_collect(collect_env))
            if net_env.get("error") is not None:
                raise E2EFault(f"net.{net_env['error']['code']}",
                               str(net_env["error"]["safe_message"]))
            self.execution_log.append("network")
            state = self.state.commit_stage(state, "network", net_env)
            self._fault("network")

        # ⑤ inspect
        if "inspect" in done:
            insp_env = self.state.read_stage(state, "inspect")
        else:
            insp_env = inspect_min.run_inspect(
                inspect_min.request_from_network(net_env))
            if insp_env.get("error") is not None:
                raise E2EFault(f"inspect.{insp_env['error']['code']}",
                               str(insp_env["error"]["safe_message"]))
            self.execution_log.append("inspect")
            state = self.state.commit_stage(state, "inspect", insp_env)
            self._fault("inspect")

        # ⑧ quality-gate
        if "gate" in done:
            gate_env = self.state.read_stage(state, "gate")
        else:
            gate_env = qgate_min.run_gate(
                qgate_min.request_from_inspect(insp_env))
            if gate_env.get("error") is not None:
                raise E2EFault(f"gate.{gate_env['error']['code']}",
                               str(gate_env["error"]["safe_message"]))
            self.execution_log.append("gate")
            state = self.state.commit_stage(state, "gate", gate_env)
            self._fault("gate")

        # ⑨ verified report (final)
        if "report" in done:
            report_env = self.state.read_stage(state, "report")
        else:
            report_env = report_min.run_report(
                report_min.request_from_chain(gate_env, [net_env]))
            if report_env.get("error") is not None:
                raise E2EFault(f"report.{report_env['error']['code']}",
                               str(report_env["error"]["safe_message"]))
            self.execution_log.append("report")
            state = self.state.commit_stage(state, "report", report_env)
            self._fault("report")

        return report_env

    def _fault(self, stage: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(stage)


def _load_input(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """CLI: run the five-stage chain; --resume replays uncommitted stages."""
    from .rt_identity_adapter import AdapterClient  # noqa: E402

    ap = argparse.ArgumentParser(prog="e2e.py")
    ap.add_argument("--input", required=True,
                    help="path to the frozen collect request JSON")
    ap.add_argument("--work", required=True,
                    help="work directory for stage artifacts/state")
    ap.add_argument("--resume", action="store_true",
                    help="resume from persisted stage outputs")
    ap.add_argument("--binary", required=True,
                    help="path to the pinned rt-identity release binary")
    ap.add_argument("--binary-sha", required=True,
                    help="pinned SHA-256 of the release binary")
    args = ap.parse_args(argv)
    try:
        client = AdapterClient(args.binary, args.binary_sha, deadline_s=30.0)
        request = _load_input(pathlib.Path(args.input))
        chain = E2EChain(client, pathlib.Path(args.work))
        env = chain.run_resume(request) if args.resume else chain.run(request)
    except E2EFault as e:
        print(f"E2E:{e.code}: {e.safe_message}", file=sys.stderr)
        return 2
    sys.stdout.write(
        json.dumps(env, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")) + "\n")
    return 0
