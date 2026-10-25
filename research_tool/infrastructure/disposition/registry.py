"""One content-addressed decision history, CAS publication and evidence-pinned review."""

from __future__ import annotations

import copy
import fcntl
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .records import (
    DispositionError,
    audit_observations,
    byte_hash,
    committed_files,
    digest,
    encoded,
    require,
    safe_bytes,
    strict_json,
    validate_reference,
)
from .state import apply_event, context_digest, counts, validate_disposition
from .schema import validate_event


def _directory(path):
    path = Path(os.path.abspath(path))
    for parent in [*reversed(path.parents), path]:
        require(not parent.is_symlink(), "registry symlinks are forbidden")
    path.mkdir(parents=True, exist_ok=True)
    require(path.is_dir(), "registry must be a directory")
    return path


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Registry:
    """Public local decision API. The event history is authority, never its derived view."""

    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        self._fault = None  # deterministic transaction failpoint used by subprocess verification
        self._verified_replay = None  # process-local acceleration; all chain bytes still rehashed

    @classmethod
    def create(cls, root, *, registry_id, roots):
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", registry_id), "invalid registry ID")
        require(
            roots and all(re.fullmatch(r"[A-Za-z0-9_-]+", k) for k in roots), "invalid root IDs"
        )
        normalized = {}
        for key, path in roots.items():
            p = Path(os.path.abspath(path))
            require(p.is_dir() and not p.is_symlink(), "evidence root unavailable or unsafe")
            normalized[key] = str(p)
        _directory(root)
        result = cls(root)
        require(not (result.root / "HEAD.json").exists(), "registry already initialized")
        result._commit(
            "initialize",
            {"registry_id": registry_id, "roots": normalized},
            expected_head=None,
            idempotency_key="initialize",
            actor="local-configuration",
        )
        return result

    @contextmanager
    def _lock(self):
        _directory(self.root)
        fd = os.open(self.root / ".writer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def head(self):
        path = self.root / "HEAD.json"
        if not path.exists():
            return None
        value = strict_json(safe_bytes(self.root, "HEAD.json"))
        require(
            set(value) == {"schema", "revision_sha256"} and value["schema"] == "issue-head/v1",
            "unknown registry HEAD schema",
        )
        require(
            re.fullmatch(r"[0-9a-f]{64}", value["revision_sha256"]), "invalid HEAD revision hash"
        )
        return value["revision_sha256"]

    def _read(self):
        head = self.head()
        require(head is not None, "registry has not been initialized")
        chain, seen, pin = [], set(), head
        cached = self._verified_replay
        while pin is not None:
            require(pin not in seen and re.fullmatch(r"[0-9a-f]{64}", pin), "broken revision chain")
            seen.add(pin)
            data = safe_bytes(self.root, f"revisions/{pin}.json")
            value = strict_json(data)
            require(
                byte_hash(data) == pin
                and isinstance(value, dict)
                and set(value) == {"schema", "previous", "event", "state_sha256"}
                and value.get("schema") == "issue-revision/v1",
                "revision hash or schema mismatch",
            )
            chain.append((pin, value))
            pin = value["previous"]
        if cached is not None and cached["head"] in seen:
            prefix_end = next(i for i, (pin, _) in enumerate(chain) if pin == cached["head"])
            state = cached["state"]
            events = list(cached["events"])
            pending = list(reversed(chain[:prefix_end]))
        else:
            state, events = None, []
            pending = list(reversed(chain))
        seen_event_ids = {e["event_id"] for e in events}
        seen_keys = {e["idempotency_key"] for e in events}
        for _, value in pending:
            event = value["event"]
            validate_event(event)
            require(
                state is not None or event["type"] == "initialize",
                "missing registry initialization",
            )
            require(event["event_id"] not in seen_event_ids, "duplicate event ID")
            require(event["idempotency_key"] not in seen_keys, "duplicate idempotency key")
            seen_event_ids.add(event["event_id"])
            seen_keys.add(event["idempotency_key"])
            state = apply_event(state, event)
            require(digest(state) == value["state_sha256"], "replayed state digest mismatch")
            events.append(event)
        require(self.head() == head, "registry changed during read")
        self._verified_replay = {"head": head, "state": state, "events": tuple(events)}
        # Internal callers sometimes inspect the returned state. Do not let them
        # mutate the verified cache used by a later CAS/retry.
        return head, copy.deepcopy(state), copy.deepcopy(events)

    def _validate_origins(self, state):
        cache, stale = {}, []
        references = [o["origin"] for o in state["observations"].values()]
        references += [
            ref
            for p in state["proposals"].values()
            for ref in p["payload"].get("evidence_refs", [])
        ]
        references += [
            ref
            for p in state["network_proposals"].values()
            for ref in p["network"]["evidence_refs"]
        ]
        for ref in references:
            try:
                validate_reference(state["roots"], ref, cache=cache)
            except (DispositionError, OSError, KeyError, TypeError):
                stale.append(digest(ref))
        return sorted(set(stale))

    def view(self):
        head, state, _ = self._read()
        stale = self._validate_origins(state)
        result = copy.deepcopy(state)
        for issue in result["issues"].values():
            last = issue.get("last_decision")
            changed = bool(
                last
                and (
                    issue.get("decision_scope_sha256") != digest(issue["scope"])
                    or issue.get("decision_network_sha256") != digest(state["network"])
                    or issue.get("decision_policy_sha256") != digest(state["policy"])
                )
            )
            issue["needs_revalidation"] = changed or bool(stale)
            issue["effective_lifecycle"] = (
                "open" if issue["needs_revalidation"] else issue["lifecycle"]
            )
        result.update(
            head_sha256=head,
            counts=counts(result),
            freshness="stale" if stale else "fresh",
            stale_references=stale,
        )
        require(self.head() == head, "registry changed during view validation")
        return result

    def export(self, *, expected_head):
        """Portable, pinned decision history; consumers need no second writable registry."""
        head, state, events = self._read()
        require(head == expected_head, "registry HEAD conflict")
        revisions, pin = {}, head
        while pin is not None:
            require(pin not in revisions, "cyclic revision during export")
            data = safe_bytes(self.root, f"revisions/{pin}.json")
            require(byte_hash(data) == pin, "revision changed during export")
            revision = strict_json(data)
            revisions[pin] = revision
            pin = revision["previous"]
        artifacts = {}
        for event in events:
            if event["type"] != "import":
                continue
            request = event["request"]
            path = f"runs/{request['run_id']}/final/evidence-audit.json"
            data = safe_bytes(Path(state["roots"][request["root_id"]]), path)
            require(byte_hash(data) == request["audit_sha256"], "export imported audit changed")
            artifacts[request["audit_sha256"]] = data.decode("utf-8")
        refs = [o["origin"] for o in state["observations"].values()]
        refs += [
            ref
            for p in state["proposals"].values()
            for ref in p["payload"].get("evidence_refs", [])
        ]
        refs += [
            ref
            for p in state["network_proposals"].values()
            for ref in p["network"]["evidence_refs"]
        ]
        for ref in refs:
            pin = ref["artifact_sha256"]
            if pin not in artifacts:
                data = safe_bytes(Path(state["roots"][ref["root_id"]]), ref["path"])
                require(byte_hash(data) == pin, "export evidence changed")
                artifacts[pin] = data.decode("utf-8")
        require(self.head() == head, "registry changed during export")
        return {
            "schema": "issue-ledger-snapshot/v1",
            "head_sha256": head,
            "registry_id": state["registry_id"],
            "state_sha256": digest(state),
            "state": state,
            "evidence_artifacts": artifacts,
            "revisions": revisions,
        }

    def snapshot(self, *, expected_head, require_fresh=True):
        result = self.view()
        require(result["head_sha256"] == expected_head, "registry HEAD conflict")
        require(
            not require_fresh or result["freshness"] == "fresh", "registry source evidence is stale"
        )
        return result

    def _publish(self, data, head):
        directory = _directory(self.root / "revisions")
        fd, name = tempfile.mkstemp(prefix=".revision-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            destination = directory / (head + ".json")
            try:
                os.link(name, destination)
            except FileExistsError:
                require(
                    safe_bytes(self.root, f"revisions/{head}.json") == data,
                    "content-address collision",
                )
            _fsync_dir(directory)
            if self._fault:
                self._fault("before_head")
            fd, temp_head = tempfile.mkstemp(prefix=".head-", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded({"schema": "issue-head/v1", "revision_sha256": head}))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_head, self.root / "HEAD.json")
                _fsync_dir(self.root)
            finally:
                if os.path.exists(temp_head):
                    os.unlink(temp_head)
            if self._fault:
                self._fault("after_head")
        finally:
            os.unlink(name)

    def _commit(
        self,
        kind,
        payload,
        *,
        expected_head,
        idempotency_key,
        actor,
        prepare=None,
        allow_stale=False,
    ):
        require(
            isinstance(idempotency_key, str)
            and idempotency_key.strip()
            and isinstance(actor, str)
            and actor.strip(),
            "event needs key and actor label",
        )
        request_pin = digest({"type": kind, "payload": payload, "actor": actor})
        with self._lock():
            head = self.head()
            if head is None:
                state, events = None, []
            else:
                _, state, events = self._read()
            for event in events:
                if event["idempotency_key"] == idempotency_key:
                    require(
                        event["request_sha256"] == request_pin,
                        "idempotency key reused with new data",
                    )
                    return event["receipt"]
            require(head == expected_head, "registry HEAD CAS conflict")
            if state is not None and not allow_stale:
                require(not self._validate_origins(state), "registry evidence is stale")
            actual_payload = prepare(state) if prepare else payload
            event = {
                "event_id": uuid4().hex,
                "type": kind,
                "payload": actual_payload,
                "request": payload,
                "idempotency_key": idempotency_key,
                "request_sha256": request_pin,
                "actor_label": actor,
                "actor_authority": "self_asserted_agent",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
            receipt = {
                "event_id": event["event_id"],
                "event_type": kind,
                "payload_sha256": digest(actual_payload),
            }
            if kind in {"propose", "network_propose"}:
                proposal = actual_payload["proposal"]
                receipt.update(
                    proposal_id=proposal["proposal_id"], proposal_sha256=digest(proposal)
                )
            event["receipt"] = receipt
            validate_event(event)
            next_state = apply_event(state, event)
            if not allow_stale:
                require(
                    not self._validate_origins(next_state), "evidence changed before publication"
                )
            revision = {
                "schema": "issue-revision/v1",
                "previous": head,
                "event": event,
                "state_sha256": digest(next_state),
            }
            data = encoded(revision)
            self._publish(data, byte_hash(data))
            return receipt

    def import_audit(
        self, *, root_id, run_id, expected_audit_sha256, expected_head, idempotency_key, actor
    ):
        request = {"root_id": root_id, "run_id": run_id, "audit_sha256": expected_audit_sha256}

        def prepare(state):
            return {
                "observations": audit_observations(
                    state["roots"], root_id, run_id, expected_audit_sha256
                )
            }

        return self._commit(
            "import",
            request,
            expected_head=expected_head,
            idempotency_key=idempotency_key,
            actor=actor,
            prepare=prepare,
        )

    def claim_reference(self, root_id, run_id, claim_id):
        state = self.view()
        require(
            state["freshness"] == "fresh" and root_id in state["roots"], "unavailable source root"
        )
        path = f"runs/{run_id}/final/tree/knowledge.json"
        _, record_pin, files = committed_files(Path(state["roots"][root_id]), run_id)
        require(path in files, "no committed final evidence graph")
        ref = {
            "root_id": root_id,
            "run_id": run_id,
            "path": path,
            "artifact_sha256": files[path],
            "run_record_sha256": record_pin,
            "claim_id": claim_id,
        }
        validate_reference(state["roots"], ref)
        return ref

    def propose_network(self, network, policy, *, expected_head, idempotency_key, actor):
        from .network import validate_network

        request = {"network": network, "policy": policy}

        def prepare(state):
            valid = validate_network(network, policy, state["roots"])
            return {
                "proposal": {
                    "proposal_id": "NET-" + uuid4().hex,
                    "network": valid,
                    "policy": policy,
                    "previous_network_sha256": digest(state["network"]),
                    "previous_policy_sha256": digest(state["policy"]),
                }
            }

        return self._commit(
            "network_propose",
            request,
            expected_head=expected_head,
            idempotency_key=idempotency_key,
            actor=actor,
            prepare=prepare,
        )

    def review_network(
        self,
        proposal_id,
        *,
        expected_proposal_sha256,
        approve,
        reason,
        expected_head,
        idempotency_key,
        actor,
        authority="agent_source_review",
    ):
        from .network import validate_network

        require(authority == "agent_source_review", "unsupported network reviewer authority")
        require(
            type(approve) is bool and isinstance(reason, str) and reason.strip(),
            "invalid network review",
        )
        request = {
            "proposal_id": proposal_id,
            "proposal_sha256": expected_proposal_sha256,
            "approve": approve,
            "reason": reason,
            "reviewer_label": actor,
            "authority": authority,
        }

        def prepare(state):
            require(proposal_id in state["network_proposals"], "unknown network proposal")
            proposal = state["network_proposals"][proposal_id]
            require(digest(proposal) == expected_proposal_sha256, "network proposal hash mismatch")
            if approve:
                validate_network(proposal["network"], proposal["policy"], state["roots"])
            return request

        return self._commit(
            "network_review",
            request,
            expected_head=expected_head,
            idempotency_key=idempotency_key,
            actor=actor,
            prepare=prepare,
            allow_stale=not approve,
        )

    @staticmethod
    def _context(state, issue):
        return context_digest(state, issue)

    def propose(
        self,
        issue_id,
        *,
        operation,
        expected_issue_revision,
        payload,
        expected_head,
        idempotency_key,
        actor,
    ):
        request = {
            "issue_id": issue_id,
            "operation": operation,
            "revision": expected_issue_revision,
            "payload": payload,
        }

        def prepare(state):
            require(issue_id in state["issues"], "unknown issue")
            issue = state["issues"][issue_id]
            require(issue["revision"] == expected_issue_revision, "issue revision conflict")
            validate_disposition(state, issue, operation, payload)
            for ref in payload.get("evidence_refs", []):
                validate_reference(state["roots"], ref)
            if payload.get("new_scientific_evidence_added"):
                self._validate_new_evidence(state, issue, payload)
            return {
                "proposal": {
                    "proposal_id": "PROP-" + uuid4().hex,
                    "issue_id": issue_id,
                    "issue_revision": expected_issue_revision,
                    "operation": operation,
                    "payload": payload,
                    "context_sha256": self._context(state, issue),
                }
            }

        return self._commit(
            "propose",
            request,
            expected_head=expected_head,
            idempotency_key=idempotency_key,
            actor=actor,
            prepare=prepare,
            allow_stale=operation in {"reopen", "assess"} and not payload.get("evidence_refs"),
        )

    @staticmethod
    def _validate_new_evidence(state, issue, payload):
        old_bodies, old_texts, old_runs = set(), [], set()
        for oid in issue["origin_ids"]:
            origin = state["observations"][oid]["origin"]
            key = (origin["root_id"], origin["run_id"])
            if key in old_runs:
                continue
            old_runs.add(key)
            root = Path(state["roots"][key[0]])
            _, record_pin, files = committed_files(root, key[1])
            require(
                record_pin == origin["run_record_sha256"], "novelty baseline run identity changed"
            )
            path = f"runs/{key[1]}/final/tree/knowledge.json"
            require(path in files, "novelty baseline lacks committed graph")
            data = safe_bytes(root, path)
            require(byte_hash(data) == files[path], "novelty baseline graph changed")
            graph = strict_json(data)
            old_bodies.update(s["content_sha256"] for s in graph["sources"])
            old_texts.extend(" ".join(s["text"].split()) for s in graph["sources"])
        accepted = [row["evidence_ref"] for row in payload["acceptance_evidence"]]
        novel = False
        for ref in accepted:
            if "claim_id" not in ref or (ref["root_id"], ref["run_id"]) in old_runs:
                continue
            fact = validate_reference(state["roots"], ref)
            novel |= any(
                c["content_sha256"] not in old_bodies
                and not any(" ".join(c["text"].split()) in text for text in old_texts)
                for c in fact["citations"]
            )
        require(novel, "new-science flag needs accepted new source content, not rerun/old context")

    def review(
        self,
        proposal_id,
        *,
        expected_proposal_sha256,
        approve,
        reason,
        expected_head,
        idempotency_key,
        actor,
        authority="agent_source_review",
    ):
        require(
            authority == "agent_source_review", "human clinical authority cannot be self-declared"
        )
        require(
            type(approve) is bool and isinstance(reason, str) and reason.strip(), "invalid review"
        )
        request = {
            "proposal_id": proposal_id,
            "proposal_sha256": expected_proposal_sha256,
            "approve": approve,
            "reason": reason,
            "authority": authority,
        }

        def prepare(state):
            require(proposal_id in state["proposals"], "unknown proposal")
            proposal = state["proposals"][proposal_id]
            require(digest(proposal) == expected_proposal_sha256, "review proposal hash mismatch")
            issue = state["issues"][proposal["issue_id"]]
            context = self._context(state, issue)
            require(not approve or context == proposal["context_sha256"], "review context is stale")
            if approve:
                for ref in proposal["payload"].get("evidence_refs", []):
                    validate_reference(state["roots"], ref)
            return {
                **request,
                "context_sha256": proposal["context_sha256"],
                "reviewer_label": actor,
            }

        return self._commit(
            "review",
            request,
            expected_head=expected_head,
            idempotency_key=idempotency_key,
            actor=actor,
            prepare=prepare,
            allow_stale=not approve or self._metadata_only_proposal(proposal_id),
        )

    def _metadata_only_proposal(self, proposal_id):
        _, state, _ = self._read()
        proposal = state["proposals"].get(proposal_id, {})
        return proposal.get("operation") in {"reopen", "assess"} and not proposal.get(
            "payload", {}
        ).get("evidence_refs")
