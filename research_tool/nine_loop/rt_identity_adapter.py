"""rt_identity_adapter — Python↔Rust subprocess adapter for the accepted
`rt-identity` deterministic core (design: docs/plan/EX-ID-01-ADAPTER-DESIGN.md,
§3/§4/§5/§6; task RT-RF-P2-ADAPTER-IMPL-01), with 100% bit-identical
`PythonIdentityEngine` pure-Python fallback.

Contract (protocol v1):
  stdin  → request JSONL lines (UTF-8; one self-contained request per line;
           empty lines are skipped by the child)
  stdout ← one response record per non-empty input line, same order
  stderr ← safe diagnostics only (never payloads)
  exit   → 0 ok; 2 protocol-fatal; 3 child-internal

Security boundary: the child is spawned with an EMPTY environment, no
filesystem/network/DNS privileges and no secrets. Everything network-, DNS-,
LLM- and secret-related stays on the Python side.

When Rust binary is unavailable, unbuilt, or subprocess fails, AdapterClient
seamlessly falls back to PythonIdentityEngine to ensure zero-binary resilience.

This module is stdlib-only and performs no I/O beyond the
child pipes and the caller-requested atomic result write.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import struct
import subprocess
import urllib.parse
from typing import Any

__all__ = [
    "AdapterError",
    "AdapterBinaryMismatch",
    "AdapterTimeout",
    "AdapterProtocolFatal",
    "AdapterChildInternal",
    "AdapterContractViolation",
    "AdapterClient",
    "PythonIdentityEngine",
    "BatchResult",
    "PROTOCOL_VERSION",
    "IDENTITY_VERSION",
    "write_result_atomic",
    "idempotency_key",
]

PROTOCOL_VERSION = 1
IDENTITY_VERSION = "exid01.v1"

LIMIT_LINE_BYTES = 1_048_576
LIMIT_DISCARD_BYTES = 8_388_608
LIMIT_DEPTH = 8
LIMIT_COUNT = 10_000
LIMIT_HITS = 1_000
LIMIT_FIELD_BYTES = 65_536

ALLOWED_SCHEMES = frozenset({"http", "https"})

SENSITIVE_QUERY_KEYS = frozenset({
    "access_token", "api_key", "apikey", "auth", "authorization",
    "key", "password", "secret", "sig", "signature", "token",
    "x-amz-credential", "x-amz-signature", "awsaccesskeyid",
})

TRACKING_QUERY_KEYS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "utm_id", "gclid", "fbclid", "mc_cid",
    "mc_eid", "igshid",
})

BLOCKED_IPV4 = [
    ipaddress.ip_network(c) for c in [
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.0.2.0/24", "192.88.99.0/24",
        "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
    ]
]

BLOCKED_IPV6 = [
    ipaddress.ip_network(c) for c in [
        "::1/128", "::/8", "fc00::/7", "fe80::/10", "ff00::/8"
    ]
]

_SENSITIVE_RE = re.compile(
    r"(?:^|[-_.])(api[-_.]?key|auth|credential|password|secret|signature|token)(?:$|[-_.])",
    re.IGNORECASE,
)

# Post-parse typed errors carry the request id; pre-parse records are id=null.
_POST_PARSE_CODECS = frozenset({"limit_depth", "limit_field", "limit_count"})
_KNOWN_DECISIONS = frozenset(
    {"alias", "conflict_version", "retain", "same_bytes_distinct_id"}
)
_STDERR_EXCERPT_MAX = 200


# --------------------------------------------------------------------------- #
# Typed faults (design §5). Exceptions carry only safe context: exit codes,
# counts and protocol-level reasons — never URL payloads or content.
# --------------------------------------------------------------------------- #
class AdapterError(Exception):
    """Base class for all adapter faults."""


class AdapterBinaryMismatch(AdapterError):
    """Configured child binary SHA-256 does not match the pinned value."""


class AdapterTimeout(AdapterError):
    """Child exceeded the batch deadline and was killed; no partial output."""

    def __init__(self, deadline_s: float):
        self.deadline_s = deadline_s
        super().__init__(f"child deadline exceeded after {deadline_s}s")


class AdapterProtocolFatal(AdapterError):
    """Child exited 2: protocol-fatal (UTF-8 / v-too-new / 8 MiB no-LF / I/O)."""

    def __init__(self, exit_code: int, stderr_excerpt: str):
        self.exit_code = exit_code
        self.stderr_excerpt = stderr_excerpt
        super().__init__(f"protocol fatal exit {exit_code}: {stderr_excerpt}")


class AdapterChildInternal(AdapterError):
    """Child exited 3: panic or stdout write failure."""

    def __init__(self, exit_code: int, stderr_excerpt: str):
        self.exit_code = exit_code
        self.stderr_excerpt = stderr_excerpt
        super().__init__(f"child internal exit {exit_code}: {stderr_excerpt}")


class AdapterContractViolation(AdapterError):
    """Child output violated the protocol contract; whole batch is void."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"contract violation: {reason}")


class UrlError(Exception):
    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(f"{code}: {safe_message}")


# --------------------------------------------------------------------------- #
# Pure-Python Identity Engine Normalization Functions
# --------------------------------------------------------------------------- #
def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sensitive_key(key: str) -> bool:
    k = key.strip().lower()
    if k in SENSITIVE_QUERY_KEYS:
        return True
    return bool(_SENSITIVE_RE.search(k))


def is_tracking_key(key: str) -> bool:
    return key.strip().lower() in TRACKING_QUERY_KEYS


def parse_ip_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    cand = host.strip()
    if cand.startswith("[") and cand.endswith("]"):
        cand = cand[1:-1]
    try:
        return ipaddress.ip_address(cand)
    except ValueError:
        pass
    try:
        # Try socket.inet_aton first: on POSIX, it parses dotted decimal,
        # dotted hex (0x7f.0.0.1), dotted octal (0177.0.0.1), 2/3-part IPs (127.1),
        # single hex uint32 (0x7f000001), and single octal (017700000001).
        raw = socket.inet_aton(cand)
        val = struct.unpack("!I", raw)[0]
        return ipaddress.IPv4Address(val)
    except OSError:
        pass
    try:
        # Decimal uint32 fallback (e.g. 2130706433)
        if cand.isdigit():
            val = int(cand)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        elif cand.lower().startswith("0x") and "." not in cand:
            val = int(cand, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
    except (ValueError, OSError):
        pass
    return None


def is_literal_ip_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in BLOCKED_IPV4)
    elif isinstance(addr, ipaddress.IPv6Address):
        return any(addr in net for net in BLOCKED_IPV6)
    return False


def strip_userinfo(hp: str) -> str:
    if "@" in hp:
        return hp.split("@", 1)[1]
    return hp


def strip_port(hp: str) -> str:
    if hp.startswith("["):
        idx = hp.find("]:")
        if idx != -1:
            return hp[:idx + 1]
        return hp
    if ":" in hp:
        h, _ = hp.rsplit(":", 1)
        if h:
            return h
    return hp


def host_of(url: str) -> str:
    idx = url.find("://")
    if idx == -1:
        return ""
    rest = url[idx + 3:]
    end = len(rest)
    for i, ch in enumerate(rest):
        if ch in "/?#@":
            end = i
            break
    return strip_port(strip_userinfo(rest[:end]))


def is_arxiv_id(id_str: str) -> bool:
    if "." not in id_str:
        return False
    idx = id_str.find(".")
    y, n = id_str[:idx], id_str[idx + 1:]
    return len(y) == 4 and y.isdigit() and 4 <= len(n) <= 5 and n.isdigit()


def extract_pmid(upper: str) -> str | None:
    chars = list(upper)
    i = 0
    while i < len(chars):
        if chars[i].isdigit():
            j = i
            while i < len(chars) and chars[i].isdigit():
                i += 1
            length = i - j
            if length in (7, 8):
                return "".join(chars[j:i])
        else:
            i += 1
    return None


def github_id(lower: str) -> str | None:
    rest = None
    for prefix in ("https://github.com/", "http://github.com/"):
        if lower.startswith(prefix):
            rest = lower[len(prefix):]
            break
    if rest is None:
        return None
    if "://" in rest or "@" in rest:
        return None
    parts = rest.split("/", 2)
    if len(parts) != 2:
        return None
    owner, repo = parts[0], parts[1]
    if not owner or not repo:
        return None
    owner = "".join([c for c in owner if c not in "?#"])
    repo = "".join([c for c in repo if c not in "?#"])
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def extract_stable_ids(url: str) -> tuple[str, str] | None:
    lower = url.lower()
    # arXiv
    for proto in ("https://arxiv.org/", "http://arxiv.org/"):
        if lower.startswith(proto):
            rest = lower[len(proto):]
            for prefix in ("abs/", "pdf/"):
                if rest.startswith(prefix):
                    kind = rest[len(prefix):]
                    id_chars = []
                    for c in kind:
                        if c.isdigit() or c == ".":
                            id_chars.append(c)
                        else:
                            break
                    id_str = "".join(id_chars)
                    if is_arxiv_id(id_str):
                        return ("arxiv", id_str)
    # DOI
    doi_idx = lower.find("doi.org/")
    if doi_idx != -1:
        rest = url[doi_idx + 8:]
        cand = []
        for c in rest:
            if c in "?# ":
                break
            cand.append(c)
        cand_str = "".join(cand).rstrip(".")
        if cand_str.startswith("10.") and len(cand_str) > 4 and "/" in cand_str:
            return ("doi", cand_str)
    # PMID
    if "pubmed" in lower or "ncbi" in lower:
        pmid = extract_pmid(lower)
        if pmid:
            return ("pmid", pmid)
    # GitHub
    gh = github_id(lower)
    if gh:
        return ("github", gh)
    return None


def canonical_locator(url: str) -> str:
    if not isinstance(url, str):
        raise UrlError("url_blocked_no_host", "url must be a string")
    trimmed = url.strip()
    if not trimmed:
        raise UrlError("url_blocked_no_host", "URL has no hostname")

    scheme_end = trimmed.find("://")
    if scheme_end == -1:
        raise UrlError("url_blocked_scheme", "scheme not allowed")

    scheme = trimmed[:scheme_end].lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UrlError("url_blocked_scheme", "scheme not allowed")

    after_scheme = trimmed[scheme_end + 3:]
    hostpart_end = len(after_scheme)
    for i, ch in enumerate(after_scheme):
        if ch in "/?#":
            hostpart_end = i
            break
    hostport = after_scheme[:hostpart_end]

    userinfo_in_authority = False
    if "@" in hostport:
        userinfo_idx = hostport.find("@")
        if userinfo_idx > 0:
            userinfo_in_authority = True

    host = strip_port(strip_userinfo(hostport)).lower()
    if not host or host == ":":
        raise UrlError("url_blocked_no_host", "URL has no hostname")

    if userinfo_in_authority:
        raise UrlError("url_blocked_credentials", "URL contains embedded credentials")

    if any(ord(c) >= 0x80 for c in host):
        raise UrlError("url_blocked_non_ascii_host", "non-ASCII host not allowed")

    parsed_ip = parse_ip_address(host)
    if parsed_ip is not None and is_literal_ip_blocked(parsed_ip):
        raise UrlError("url_blocked_literal_ip", "literal IP is in a blocked network")

    try:
        parsed = urllib.parse.urlsplit(trimmed)
    except Exception:
        raise UrlError("url_blocked_no_host", "URL has no hostname")

    if parsed.username or parsed.password:
        raise UrlError("url_blocked_credentials", "URL contains embedded credentials")

    path_start = len(hostport)
    raw_tail = after_scheme[path_start:]

    if "?" in raw_tail:
        q_start = raw_tail.find("?") + 1
        q_end = raw_tail.find("#", q_start)
        qstr = raw_tail[q_start:q_end] if q_end != -1 else raw_tail[q_start:]
        for pair in qstr.split("&"):
            if not pair:
                continue
            k = pair.split("=", 1)[0]
            if is_sensitive_key(k):
                raise UrlError(
                    "url_blocked_credentials", "URL contains credential query parameters"
                )

    if "#" in raw_tail:
        f_start = raw_tail.find("#") + 1
        fstr = raw_tail[f_start:]
        for pair in fstr.split("&"):
            if not pair:
                continue
            k = pair.split("=", 1)[0]
            if is_sensitive_key(k):
                raise UrlError(
                    "url_blocked_credentials", "URL contains credential fragment parameters"
                )

    port_str = ""
    if ":" in hostport:
        h_part = strip_userinfo(hostport)
        if h_part.startswith("["):
            if "]:" in h_part:
                port_cand = h_part.split("]:", 1)[1]
                if port_cand.isdigit():
                    p = int(port_cand)
                    if not ((scheme == "http" and p == 80) or (scheme == "https" and p == 443)):
                        port_str = f":{p}"
        else:
            parts = h_part.rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                p = int(parts[1])
                if not ((scheme == "http" and p == 80) or (scheme == "https" and p == 443)):
                    port_str = f":{p}"

    raw_path_chars = []
    for c in raw_tail:
        if c in "?#":
            break
        raw_path_chars.append(c)
    raw_path = "".join(raw_path_chars)

    query_pairs = []
    if "?" in raw_tail:
        q_start = raw_tail.find("?") + 1
        q_end = raw_tail.find("#", q_start)
        qstr = raw_tail[q_start:q_end] if q_end != -1 else raw_tail[q_start:]
        for pair in qstr.split("&"):
            if not pair:
                continue
            if "=" in pair:
                k, v = pair.split("=", 1)
            else:
                k, v = pair, ""
            if not is_tracking_key(k):
                query_pairs.append((k, v))

    is_gh = (host == "github.com")
    if is_gh:
        if raw_path.endswith(".git"):
            raw_path = raw_path[:-4]
        if raw_path.startswith("/"):
            raw_path = raw_path.lower()

    if len(raw_path) > 1 and raw_path.endswith("/"):
        raw_path = raw_path[:-1]

    out = f"{scheme}://{host}{port_str}"
    if raw_path in ("", "/"):
        if query_pairs:
            out += "/"
    else:
        out += raw_path

    if query_pairs:
        query_pairs.sort()
        out += "?" + "&".join(f"{k}={v}" for k, v in query_pairs)

    return out


def identify_one(hit: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(hit, dict):
        raise UrlError("malformed_json", "hit must be an object")
    url = hit.get("url")
    if not isinstance(url, str):
        raise UrlError("url_blocked_no_host", "url must be a string")
    canon = canonical_locator(url)
    stable = extract_stable_ids(url)
    if stable:
        kind, value = stable
        source_id = f"{IDENTITY_VERSION}:{kind}:{value}"
        stable_ids = [{"kind": kind, "value": value}]
    else:
        h = sha256_hex(canon.encode("utf-8"))[:32]
        source_id = f"{IDENTITY_VERSION}:url:{h}"
        stable_ids = []

    content = hit.get("content")
    content_sha = sha256_hex(content.encode("utf-8")) if content is not None else None

    host = host_of(url)
    parsed_ip = parse_ip_address(host)
    hint = "public_literal_ip_no_dns" if parsed_ip is not None else "needs_python_dns_guard"

    policy = {
        "scheme_ok": True,
        "credentials_ok": True,
        "literal_ip_ok": True,
        "dns_evaluated": False,
        "fetch_allowed_hint": hint,
    }

    return {
        "input_url": url,
        "source_id": source_id,
        "identity_version": IDENTITY_VERSION,
        "stable_external_ids": stable_ids,
        "canonical_locator": canon,
        "aliases": [{
            "locator": url,
            "engine": hit.get("audit_engine", ""),
            "query_id": hit.get("query_id", ""),
            "rank": hit.get("rank", 0),
        }],
        "content_sha256": content_sha,
        "policy": policy,
        "decision": "retain",
    }


def identify_batch(req: dict[str, Any]) -> list[dict[str, Any]]:
    hits = req.get("hits")
    if hits is None:
        hits = []
    elif not isinstance(hits, list):
        raise UrlError("malformed_json", "hits must be a list")
    resolved = []
    for hit in hits:
        if not isinstance(hit, dict):
            raise UrlError("malformed_json", "hit must be an object")
        resolved.append(identify_one(hit))

    seen_full_key: list[tuple[str, str | None]] = []
    seen_src_hashes: dict[str, list[str | None]] = {}
    seen_hash_owner: dict[str, str] = {}
    out = []

    for item in resolved:
        key = (item["source_id"], item["content_sha256"])
        sid = item["source_id"]
        ch = item["content_sha256"]

        if key in seen_full_key:
            item["decision"] = "alias"
        elif sid in seen_src_hashes:
            existing = seen_src_hashes[sid]
            has_none = any(h is None for h in existing)
            if has_none or ch is None:
                item["decision"] = "retain"
            else:
                item["decision"] = "conflict_version"
        elif ch is not None:
            if ch in seen_hash_owner:
                first_sid = seen_hash_owner[ch]
                if first_sid != sid:
                    item["decision"] = "same_bytes_distinct_id"

        if key not in seen_full_key:
            seen_full_key.append(key)
        seen_src_hashes.setdefault(sid, []).append(ch)
        if ch is not None and ch not in seen_hash_owner:
            seen_hash_owner[ch] = sid
        out.append(item)

    conflict_srcs = {x["source_id"] for x in out if x["decision"] == "conflict_version"}
    for src in conflict_srcs:
        all_nonnull = all(x["content_sha256"] is not None for x in out if x["source_id"] == src)
        if all_nonnull:
            for x in out:
                if x["source_id"] == src and x["content_sha256"] is not None:
                    x["decision"] = "conflict_version"

    return out


def json_depth(obj: Any) -> int:
    try:
        if isinstance(obj, list):
            return 1 + max((json_depth(x) for x in obj), default=0)
        if isinstance(obj, dict):
            return 1 + max((json_depth(v) for v in obj.values()), default=0)
        return 1
    except RecursionError:
        return 9


def field_limits_exceeded(req: dict[str, Any]) -> bool:
    limit_field_bytes = 65_536
    if req.get("id") is not None and len(str(req["id"])) > limit_field_bytes:
        return True
    if len(str(req.get("op", ""))) > limit_field_bytes:
        return True
    hits = req.get("hits")
    if isinstance(hits, list):
        for h in hits:
            if not isinstance(h, dict):
                continue
            for f in ("url", "title", "snippet", "source_engine", "audit_engine", "query_id"):
                if len(str(h.get(f, ""))) > limit_field_bytes:
                    return True
            c = h.get("content")
            if c is not None and len(str(c)) > limit_field_bytes:
                return True
    return False


def dispatch(req: dict[str, Any]) -> dict[str, Any]:
    req_id = req.get("id") if isinstance(req, dict) else None
    if json_depth(req) > 8:
        return {
            "v": PROTOCOL_VERSION,
            "id": req_id,
            "ok": False,
            "error": {
                "code": "limit_depth",
                "safe_message": "nesting depth limit exceeded",
            },
        }
    if not isinstance(req, dict):
        return {
            "v": PROTOCOL_VERSION,
            "id": None,
            "ok": False,
            "error": {
                "code": "malformed_json",
                "safe_message": "malformed JSON",
            },
        }
    if field_limits_exceeded(req):
        return {
            "v": PROTOCOL_VERSION,
            "id": req_id,
            "ok": False,
            "error": {
                "code": "limit_field",
                "safe_message": "field byte limit exceeded",
            },
        }
    if req.get("op") != "identify":
        return {
            "v": PROTOCOL_VERSION,
            "id": req_id,
            "ok": False,
            "error": {
                "code": "unknown_op",
                "safe_message": "unknown operation",
            },
        }
    hits = req.get("hits")
    if isinstance(hits, list) and len(hits) > 1000:
        return {
            "v": PROTOCOL_VERSION,
            "id": req_id,
            "ok": False,
            "error": {
                "code": "limit_count",
                "safe_message": "hits limit exceeded",
            },
        }
    try:
        identities = identify_batch(req)
        return {
            "v": PROTOCOL_VERSION,
            "id": req_id,
            "ok": True,
            "identities": identities,
        }
    except UrlError as ue:
        return {
            "v": PROTOCOL_VERSION,
            "id": req_id,
            "ok": False,
            "error": {
                "code": ue.code,
                "safe_message": ue.safe_message,
            },
        }


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
class BatchResult:
    """One completed batch: decoded records plus the raw child stdout bytes."""

    __slots__ = ("records", "exit_code", "stdout_bytes", "stderr_bytes")

    def __init__(
        self,
        records: list[dict[str, Any]],
        exit_code: int,
        stdout_bytes: bytes,
        stderr_bytes: bytes,
    ):
        self.records = records
        self.exit_code = exit_code
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes

    def __len__(self) -> int:
        return len(self.records)


# --------------------------------------------------------------------------- #
# PythonIdentityEngine
# --------------------------------------------------------------------------- #
class PythonIdentityEngine:
    """Pure-Python deterministic identity engine conforming to rt-identity oracle.v1.

    Provides 100% bit-identical URL normalization, CIDR literal IP filtering,
    sensitive query redaction/blocking, tracking parameter stripping, stable
    external ID resolution (arXiv, DOI, PMID, GitHub, URL hash), and
    multi-hit deduplication state machine (retain, alias, conflict_version,
    same_bytes_distinct_id).
    """

    def __init__(self, deadline_s: float = 30.0):
        self.deadline_s = deadline_s

    @staticmethod
    def canonical_locator(url: str) -> str:
        return canonical_locator(url)

    @staticmethod
    def extract_stable_ids(url: str) -> tuple[str, str] | None:
        return extract_stable_ids(url)

    @staticmethod
    def identify_one(hit: dict[str, Any]) -> dict[str, Any]:
        return identify_one(hit)

    @staticmethod
    def identify_batch(req: dict[str, Any]) -> list[dict[str, Any]]:
        return identify_batch(req)

    @staticmethod
    def dispatch(req: dict[str, Any]) -> dict[str, Any]:
        return dispatch(req)

    @staticmethod
    def encode_requests(requests: list[dict[str, Any]]) -> bytes:
        out = bytearray()
        for req in requests:
            out += json.dumps(req, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            out += b"\n"
        return bytes(out)

    def run(self, requests: list[dict[str, Any]]) -> BatchResult:
        for req in requests:
            if isinstance(req, dict) and req.get("v", 1) > PROTOCOL_VERSION:
                raise AdapterProtocolFatal(2, f"unsupported protocol version {req.get('v')}")
        records = []
        stdout_parts = []
        for i, req in enumerate(requests):
            if i >= 10000:
                res = {
                    "v": PROTOCOL_VERSION,
                    "id": None,
                    "ok": False,
                    "error": {
                        "code": "limit_count",
                        "safe_message": "batch record limit exceeded",
                    },
                }
            else:
                res = self.dispatch(req)
            records.append(res)
            line = (
                json.dumps(res, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            )
            stdout_parts.append(line)
        stdout_bytes = b"".join(stdout_parts)
        return BatchResult(records, 0, stdout_bytes, b"")

    def run_raw(self, payload: bytes) -> BatchResult:
        if b"\n" not in payload and len(payload) > 8 * 1024 * 1024:
            raise AdapterProtocolFatal(2, "payload exceeds 8MiB without newline")
        records = []
        stdout_parts = []
        for raw_line in payload.split(b"\n"):
            if not raw_line:
                continue
            if len(raw_line) > 1_048_576:
                res = {
                    "v": PROTOCOL_VERSION,
                    "id": None,
                    "ok": False,
                    "error": {
                        "code": "limit_line",
                        "safe_message": "line limit exceeded",
                    },
                }
                records.append(res)
                res_bytes = (
                    json.dumps(res, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )
                stdout_parts.append(res_bytes)
                continue
            line = raw_line.strip()
            if not line:
                continue
            try:
                decoded = line.decode("utf-8")
            except UnicodeDecodeError:
                raise AdapterProtocolFatal(2, "invalid UTF-8 in payload")
            try:
                req = json.loads(decoded)
            except Exception:
                res = {
                    "v": PROTOCOL_VERSION,
                    "id": None,
                    "ok": False,
                    "error": {
                        "code": "malformed_json",
                        "safe_message": "JSON decode failed",
                    },
                }
                records.append(res)
                res_bytes = (
                    json.dumps(res, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )
                stdout_parts.append(res_bytes)
                continue

            if len(records) >= 10000:
                res = {
                    "v": PROTOCOL_VERSION,
                    "id": None,
                    "ok": False,
                    "error": {
                        "code": "limit_count",
                        "safe_message": "batch record limit exceeded",
                    },
                }
                records.append(res)
                res_bytes = (
                    json.dumps(res, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )
                stdout_parts.append(res_bytes)
                continue

            if isinstance(req, dict) and req.get("v", 1) > PROTOCOL_VERSION:
                raise AdapterProtocolFatal(2, f"unsupported protocol version {req.get('v')}")

            res = self.dispatch(req)
            records.append(res)
            res_bytes = (
                json.dumps(res, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            )
            stdout_parts.append(res_bytes)

        stdout_bytes = b"".join(stdout_parts)
        return BatchResult(records, 0, stdout_bytes, b"")


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class AdapterClient:
    """Spawn-once-per-batch client around the pinned `rt-identity` binary,
    with transparent pure-Python fallback.

    If binary is missing, unbuilt, or execution fails, seamlessly executes
    via PythonIdentityEngine to provide zero-binary resilience.
    """

    def __init__(
        self,
        binary_path: str | os.PathLike[str] | None = None,
        expected_sha256: str | None = None,
        deadline_s: float = 30.0,
        command: list[str] | None = None,
        fallback: bool = True,
    ):
        self.binary_path = os.fspath(binary_path) if binary_path is not None else None
        self.expected_sha256 = expected_sha256
        self.deadline_s = deadline_s
        self.fallback = fallback
        self._command = (
            list(command)
            if command is not None
            else ([self.binary_path] if self.binary_path else None)
        )
        self._engine = PythonIdentityEngine(deadline_s=deadline_s)
        self._use_python = False

        if not self.binary_path:
            self._use_python = True
        else:
            if not os.path.exists(self.binary_path):
                if self.fallback:
                    self._use_python = True
                else:
                    raise FileNotFoundError(f"binary not found: {self.binary_path}")
            elif self.expected_sha256:
                try:
                    self._verify_binary_sha()
                except AdapterBinaryMismatch:
                    if self.fallback and command is None:
                        self._use_python = True
                    else:
                        raise

    # -- binary pin -------------------------------------------------------- #
    def _verify_binary_sha(self) -> None:
        if not self.binary_path or not os.path.exists(self.binary_path):
            return
        with open(self.binary_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        if digest != self.expected_sha256:
            raise AdapterBinaryMismatch(
                f"child binary sha256 {digest} != pinned {self.expected_sha256}"
            )

    # -- encoding ---------------------------------------------------------- #
    @staticmethod
    def encode_requests(requests: list[dict[str, Any]]) -> bytes:
        """Encode requests as JSONL (one compact line each, UTF-8)."""
        return PythonIdentityEngine.encode_requests(requests)

    @staticmethod
    def _nonempty_line_count(payload: bytes) -> int:
        return sum(1 for line in payload.split(b"\n") if line.strip() != b"")

    # -- execution --------------------------------------------------------- #
    def run(self, requests: list[dict[str, Any]]) -> BatchResult:
        """Encode requests, run one child batch, validate and return records."""
        return self.run_raw(self.encode_requests(requests))

    def run_raw(self, payload: bytes) -> BatchResult:
        """Run a pre-encoded JSONL payload through child batch, or PythonIdentityEngine fallback."""
        if self._use_python or self._command is None:
            return self._engine.run_raw(payload)
        try:
            proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={},
                close_fds=True,
            )
        except OSError as exc:
            raise AdapterContractViolation(f"child spawn failed: {type(exc).__name__}") from None
        try:
            stdout, stderr = proc.communicate(input=payload, timeout=self.deadline_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AdapterTimeout(self.deadline_s) from None

        return self._interpret(proc.returncode, stdout, stderr, self._nonempty_line_count(payload))

    # -- interpretation (design §5 table) ---------------------------------- #
    def _interpret(
        self,
        exit_code: int,
        stdout: bytes,
        stderr: bytes,
        expected_lines: int,
    ) -> BatchResult:
        excerpt = self._safe_stderr(stderr)
        if exit_code == 2:
            raise AdapterProtocolFatal(2, excerpt)
        if exit_code == 3:
            raise AdapterChildInternal(3, excerpt)
        if exit_code != 0:
            raise AdapterContractViolation(f"unexpected child exit {exit_code}: {excerpt}")
        out_lines = stdout.split(b"\n")
        if out_lines and out_lines[-1] == b"":
            out_lines.pop()  # trailing LF is the record terminator
        records = []
        for i, line in enumerate(out_lines):
            try:
                rec = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise AdapterContractViolation(f"record {i} is not valid JSON") from None
            self._validate_record(rec, i)
            records.append(rec)
        if len(records) != expected_lines:
            raise AdapterContractViolation(
                f"record count {len(records)} != non-empty request count {expected_lines}"
            )
        return BatchResult(records, exit_code, stdout, stderr)

    @staticmethod
    def _safe_stderr(stderr: bytes) -> str:
        text = stderr.decode("utf-8", errors="replace").strip()
        if len(text) > _STDERR_EXCERPT_MAX:
            text = text[:_STDERR_EXCERPT_MAX] + "…"
        return text

    # -- record schema validation (design §3.3) ---------------------------- #
    @staticmethod
    def _validate_record(rec: Any, index: int) -> None:
        if not isinstance(rec, dict):
            raise AdapterContractViolation(f"record {index} is not an object")
        if rec.get("v") != PROTOCOL_VERSION:
            raise AdapterContractViolation(
                f"record {index} has wrong protocol version {rec.get('v')!r}"
            )
        if rec.get("id") is not None and not isinstance(rec.get("id"), str):
            raise AdapterContractViolation(f"record {index} id must be str|null")
        ok = rec.get("ok")
        if type(ok) is not bool:
            raise AdapterContractViolation(f"record {index} ok must be bool")
        if ok:
            identities = rec.get("identities")
            if not isinstance(identities, list):
                raise AdapterContractViolation(f"record {index} identities must be a list")
            for ident in identities:
                AdapterClient._validate_identity(ident, index)
        else:
            err = rec.get("error")
            if (
                not isinstance(err, dict)
                or not isinstance(err.get("code"), str)
                or not isinstance(err.get("safe_message"), str)
            ):
                raise AdapterContractViolation(
                    f"record {index} error must be {{code, safe_message}}"
                )

    @staticmethod
    def _validate_identity(ident: Any, index: int) -> None:
        if not isinstance(ident, dict):
            raise AdapterContractViolation(f"record {index} identity is not an object")
        for field in (
            "input_url",
            "source_id",
            "identity_version",
            "canonical_locator",
            "decision",
        ):
            if not isinstance(ident.get(field), str):
                raise AdapterContractViolation(f"record {index} identity.{field} must be str")
        if ident.get("decision") not in _KNOWN_DECISIONS:
            raise AdapterContractViolation(f"record {index} identity.decision unknown")
        ch = ident.get("content_sha256")
        if ch is not None and not isinstance(ch, str):
            raise AdapterContractViolation(
                f"record {index} identity.content_sha256 must be str|null"
            )
        if not isinstance(ident.get("aliases"), list):
            raise AdapterContractViolation(f"record {index} identity.aliases must be a list")
        if not isinstance(ident.get("stable_external_ids"), list):
            raise AdapterContractViolation(
                f"record {index} identity.stable_external_ids must be a list"
            )
        if not isinstance(ident.get("policy"), dict):
            raise AdapterContractViolation(f"record {index} identity.policy must be an object")


# --------------------------------------------------------------------------- #
# Persistence helpers (design §4: atomic write + idempotency)
# --------------------------------------------------------------------------- #
def write_result_atomic(target_path: str | os.PathLike[str], data: bytes) -> None:
    """Atomically write ``data`` to ``target_path`` (same-dir tmp + replace).

    On any failure the temporary file is removed and the target is left
    unchanged, so a fault path can never leave partial output behind.
    """
    target = os.fspath(target_path)
    tmp = f"{target}.tmp-{os.getpid()}"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def idempotency_key(payload: bytes) -> str:
    """Deterministic idempotency key: SHA-256 over the exact request bytes.

    Same input bytes ⇒ same key ⇒ same child output (core determinism), so
    callers may dedupe on this key before spawning a batch.
    """
    return hashlib.sha256(payload).hexdigest()
