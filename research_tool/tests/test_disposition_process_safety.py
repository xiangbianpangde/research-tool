"""A12: actual competing processes and SIGKILL around HEAD, not a mocked green path."""

from __future__ import annotations
import subprocess
import sys
import time

import pytest
from research_tool.infrastructure.disposition import Registry, DispositionError


CHILD = r"""
import json,os,sys,time
from pathlib import Path
from research_tool.infrastructure.disposition import Registry
reg=Registry(sys.argv[1])
expected=sys.argv[2]
ready=Path(sys.argv[3]); release=Path(sys.argv[4]); point=sys.argv[5]
def fault(where):
 if where==point:
  ready.write_text(where)
  while not release.exists():time.sleep(.005)
reg._fault=fault
if point=='race':
 ready.write_text('ready')
 while not release.exists():time.sleep(.005)
try:
 receipt=reg._commit('checkpoint',{'reason':'transaction-only verification'},expected_head=expected,idempotency_key=sys.argv[6],actor='process-test')
 print(json.dumps({'status':'success','receipt':receipt}),flush=True)
except Exception as exc:
 print(json.dumps({'status':'failed','error':type(exc).__name__,'message':str(exc)}),flush=True)
 raise SystemExit(1)
"""


def wait_file(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert path.exists()


def test_two_processes_same_expected_head_have_one_winner(tmp_path):
    reg = Registry.create(tmp_path / "registry", registry_id="race", roots={"data": str(tmp_path)})
    head = reg.head()
    release = tmp_path / "release"
    procs = []
    try:
        for i in range(2):
            ready = tmp_path / f"ready{i}"
            p = subprocess.Popen(  # noqa: S603 - fixed Python/fixture source, no shell
                [
                    sys.executable,
                    "-B",
                    "-c",
                    CHILD,
                    str(reg.root),
                    head,
                    str(ready),
                    str(release),
                    "race",
                    f"event{i}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(p)
            wait_file(ready)
        release.write_text("go")
        results = [p.communicate(timeout=8) for p in procs]
        assert sorted(p.returncode for p in procs) == [0, 1], results
        assert len(reg.view()["history"]) == 1
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()


@pytest.mark.parametrize("point", ["before_head", "after_head"])
def test_sigkill_does_not_promote_orphan_and_replay_is_idempotent(tmp_path, point):
    reg = Registry.create(tmp_path / "registry", registry_id="kill", roots={"data": str(tmp_path)})
    head = reg.head()
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    p = subprocess.Popen(  # noqa: S603 - fixed Python/fixture source, no shell
        [
            sys.executable,
            "-B",
            "-c",
            CHILD,
            str(reg.root),
            head,
            str(ready),
            str(release),
            point,
            "same-event",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_file(ready)
        p.kill()
        p.communicate(timeout=8)
        assert (reg.head() == head) is (point == "before_head")
        receipt = reg._commit(
            "checkpoint",
            {"reason": "transaction-only verification"},
            expected_head=head,
            idempotency_key="same-event",
            actor="process-test",
        )
        assert len(reg.view()["history"]) == 1
        assert (
            reg._commit(
                "checkpoint",
                {"reason": "transaction-only verification"},
                expected_head=head,
                idempotency_key="same-event",
                actor="process-test",
            )
            == receipt
        )
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_symlink_and_unknown_head_schema_are_rejected(tmp_path):
    reg = Registry.create(tmp_path / "registry", registry_id="safe", roots={"data": str(tmp_path)})
    (reg.root / "HEAD.json").write_text(
        '{"schema":"new-unknown","revision_sha256":"' + "a" * 64 + '"}'
    )
    with pytest.raises(DispositionError):
        reg.view()
