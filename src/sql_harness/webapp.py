"""Local docs browser + editor: `sql-harness web`.

Serves the disjoint doc trees this tool accumulates — the shipped docs
(`SKILL.md`, `interaction-skills/{shared/sql, postgres, mysql, ssh, redis}`,
the preset meta skills), the runtime workspace
(`$BH_SQL_HOME/agent-workspace/zones/<conn>/`), the scripts saved into those
zones, and the connections.toml file — as one documentation site with a
sidebar and an in-page editor.

Two things make this module more careful than its size suggests:

1. It writes files on HTTP request. `resolve_doc()` / `resolve_create()` /
   `resolve_rename()` are the only things standing between a request body
   and an arbitrary write, so every path goes through one of them and the
   resolved path is what gets opened — never the joined one.
2. Any page the user visits can reach `http://127.0.0.1:<port>`. The handler
   therefore checks `Host`, requires `PUT` + `application/json` for writes,
   and checks `Origin`. See `_check_host` and `_check_write_request`.

Mirrors `browser-harness`'s daemon-free stance: this is a foreground server
you Ctrl-C, not a background service.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .config import (
    ConnectionConfig,
    ConnectionsConfig,
    PoolConfig,
    _mask_password,
)
from .paths import config_file, ensure_private_dir, workspace_dir, zone_skills_dir

# Bodies larger than this are refused (413): these are markdown docs, not data.
MAX_BODY = 2 * 1024 * 1024

# Only these may be written. `.md` is the doc corpus; `.py` is saved scripts
# and per-zone helpers. `.toml` is the connection file (added 2026-09).
EDITABLE_SUFFIXES = (".md", ".py", ".toml")

# Allowed kinds for `POST /api/create`. The name part is `.md` / `.py` / `.toml`
# (or just `name` — extension is filled in if missing for `md`/`py`).
CREATE_KINDS = {"md", "py", "toml"}

# Whitelist for static assets served under /static/.
#
# Layout on disk (sql_harness/_web_assets/):
#   index.html
#   index-<hash>.js
#   index-<hash>.css
#   Editor-<hash>.js             ← lazy-loaded editor chunk
#
# Rules:
#   - `index.html` is matched exactly (never nested).
#   - Anything else must be a hashed chunk (`<name>-<hash>.<ext>` where name
#     is alphanumeric and hash is alphanumeric). The hash is the canonical
#     integrity check; the name encodes the entry point. This is the same
#     shape Vite emits, so we don't have to enumerate builds.
#   - Anything else returns 404.
#
# Hashed chunks carry `Cache-Control: max-age=31536000, immutable` because
# their name encodes their content; the SPA shell stays `no-store` so a new
# build busts the cache automatically.
ASSETS_INDEX = "index.html"
# Vite emits `name-<hash>.<ext>`; accept that exact shape and reject
# arbitrary nested paths. The hash pattern is Vite's default (alphanumerics
# + dash, 8+ chars).
_HASSHED_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*-[A-Za-z0-9_-]{8,}\.[a-z]+$")

# Names accepted in the Host / Origin headers. The whole security story assumes
# loopback; `--host` widens this set (see make_server).
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


# --- Errors ---------------------------------------------------------------- #

class WebError(Exception):
    """Base for the HTTP-mappable errors below."""

    status = HTTPStatus.BAD_REQUEST
    code = "bad_request"


class Forbidden(WebError):
    status = HTTPStatus.FORBIDDEN
    code = "forbidden"


class NotFound(WebError):
    status = HTTPStatus.NOT_FOUND
    code = "not_found"


class Conflict(WebError):
    status = HTTPStatus.CONFLICT
    code = "conflict"

    def __init__(self, message: str, *, sha256: str, raw: str):
        super().__init__(message)
        self.sha256 = sha256
        self.raw = raw


class TooLarge(WebError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    code = "too_large"


class Unsupported(WebError):
    status = HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_media_type"


# --- Doc roots ------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class DocRoot:
    """One browsable tree. `base` is a directory, or a single file for
    singleton roots like `SKILL.md`."""

    id: str
    label: str
    kind: str  # "source" | "runtime" | "config"
    base: Path
    editable: bool = True
    connection: str | None = None
    exclude: tuple[str, ...] = field(default=())

    @property
    def singleton(self) -> bool:
        """True when `base` is a file rather than a directory (SKILL.md, config).

        Derived from `base` rather than stored, so a `DocRoot` built by hand
        (tests, callers) behaves like the ones `build_roots()` returns without
        having to remember the flag.
        """
        return self.base.is_file()


def _repo_root() -> Path:
    """The `lab/sql_harness/` checkout root, 2 levels up from this file.

    Mirrors `cli._package_root_dir()`. Meaningless in a wheel install, which is
    why every caller pairs it with a `_bundled()` fallback.
    """
    return Path(__file__).resolve().parents[2]


def _bundled(*parts: str) -> Path | None:
    """A path inside the wheel's `sql_harness/_skills/` bundle, or None.

    Mirrors `cli._packaged_skill_body()`'s two-hop resolution, but returns a
    path instead of a body.
    """
    from importlib import resources as importlib_resources

    try:
        ref = importlib_resources.files("sql_harness").joinpath("_skills", *parts)
        if ref.is_file() or ref.is_dir():
            return Path(str(ref))
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return None


def _source_root(*repo_parts: str, bundled: tuple[str, ...]) -> tuple[Path, bool] | None:
    """Resolve a shipped doc location; prefer the checkout, fall back to the wheel.

    Returns `(path, editable)`. The repo checkout is editable; the wheel bundle
    sits in site-packages, so it is not.
    """
    repo = _repo_root()
    if (repo / "SKILL.md").is_file():
        candidate = repo.joinpath(*repo_parts)
        if candidate.exists():
            return candidate, True
    if (found := _bundled(*bundled)) is not None:
        return found, False
    return None


def build_roots() -> list[DocRoot]:
    """Enumerate every browsable root.

    Deliberately uncached: the server is long-lived, and a zone created by
    `sql-harness init` (or another agent) should appear on refresh without a
    restart. Cost is a few `exists()` calls and one `iterdir()`.
    """
    roots: list[DocRoot] = []

    shipped = (
        ("skill", "SKILL.md", ("SKILL.md",), ("SKILL.md",)),
        ("interaction", "interaction-skills/", ("interaction-skills",), ("interaction-skills",)),
    )
    for root_id, label, repo_parts, bundled_parts in shipped:
        resolved = _source_root(*repo_parts, bundled=bundled_parts)
        if resolved is None:
            continue
        base, editable = resolved
        roots.append(
            DocRoot(
                id=root_id,
                label=label,
                kind="source",
                base=base,
                editable=editable,
            )
        )

    # `meta-skills` is the cross-DSN fallback zone: always point at the
    # runtime dir so CRUD lands in $BH_SQL_HOME/agent-workspace, not the
    # repo checkout (which `git status` would otherwise flag). If the
    # runtime dir doesn't exist yet, fall back to the repo path so the
    # tree still lists the bundled preset skills.
    runtime_meta = zone_skills_dir("meta")
    if runtime_meta.is_dir():
        roots.append(
            DocRoot(
                id="meta-skills",
                label="meta preset skills/",
                kind="runtime",
                base=runtime_meta,
                connection="meta",
            )
        )
    else:
        resolved = _source_root(
            "agent-workspace", "zones", "meta", "skills",
            bundled=("zones-meta-skills",),
        )
        if resolved is not None:
            base, _editable = resolved
            roots.append(
                DocRoot(
                    id="meta-skills",
                    label="meta preset skills/",
                    kind="source",
                    base=base,
                    editable=False,  # source fallback is preset, not writable
                )
            )

    # Runtime: the workspace minus zones (zones get their own roots, so listing
    # them here too would show every file twice).
    ws = workspace_dir()
    if ws.is_dir():
        roots.append(
            DocRoot(
                id="runtime",
                label="agent-workspace/",
                kind="runtime",
                base=ws,
                exclude=("zones",),
            )
        )

    # One root per zone directory on disk. Two sources of zone directories:
    #
    #   1. $BH_SQL_HOME/agent-workspace/zones/        (runtime, writable)
    #   2. <repo>/agent-workspace/zones/             (shipped preset,
    #                                                  appears in source
    #                                                  checkouts only)
    #
    # Runtime wins: when both exist for the same name, the runtime zone is
    # what CRUD touches and the repo one is masked. (Otherwise the user
    # creates a `meta` skill and wonders why it's not in the tree — it's
    # landing under the repo path, which `git status` will then complain
    # about, and the in-tree file_count never reflects it.)
    seen_zone_ids: set[str] = set()
    candidate_zone_dirs: list[tuple[str, Path, bool]] = []  # (id, base, writable)
    ws = workspace_dir()
    runtime_zones = ws / "zones"
    if runtime_zones.is_dir():
        for child in sorted(runtime_zones.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            candidate_zone_dirs.append((f"zone:{child.name}", child, True))
            seen_zone_ids.add(f"zone:{child.name}")
    repo_root = _repo_root()
    repo_zones = repo_root / "agent-workspace" / "zones"
    if repo_zones.is_dir():
        for child in sorted(repo_zones.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            zid = f"zone:{child.name}"
            if zid in seen_zone_ids:
                continue  # runtime takes precedence
            # Repo zones are preset material; not writable through the web
            # because edits would land in git.
            candidate_zone_dirs.append((zid, child, False))
            seen_zone_ids.add(zid)
    for zid, base, writable in candidate_zone_dirs:
        roots.append(
            DocRoot(
                id=zid,
                label=f"zones/{base.name}/",
                kind="runtime",
                base=base,
                connection=base.name,
                editable=writable,
            )
        )

    # connections.toml — the config root. Singleton file root; like SKILL.md,
    # it's editable in a checkout; in a wheel install (no repo root) the
    # runtime config file is what we point at (always editable for the user
    # who owns it — they have to be able to add a DSN, otherwise the whole
    # tool is dead-on-arrival).
    cfg_path = config_file()
    if cfg_path.exists() or _repo_root().joinpath("SKILL.md").is_file():
        # Source checkout / wheel: prefer the config file at runtime, mark
        # editable so the user can actually save credentials after `init`.
        roots.append(
            DocRoot(
                id="config",
                label="connections.toml",
                kind="config",
                base=cfg_path,
            )
        )

    return roots


def find_root(roots: list[DocRoot], root_id: str) -> DocRoot:
    for root in roots:
        if root.id == root_id:
            return root
    raise NotFound(f"unknown root: {root_id!r}")


# --- Path safety ----------------------------------------------------------- #

def _check_rel(rel: str, *, allow_empty: bool = False) -> list[str]:
    """Validate the per-segment shape of a root-relative path.

    Returns the validated parts list. Rejects NUL bytes, dotfiles, dot-dirs,
    `.`/`..`, and drive specs. Centralised so create / rename / doc all use
    the same rule.
    """
    if "\x00" in rel:
        raise Forbidden("path contains a NUL byte")
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if not parts:
        if allow_empty:
            return []
        raise Forbidden("empty path")
    bad = [p for p in parts if p in ("", ".", "..") or p.startswith(".") or ":" in p]
    if bad:
        raise Forbidden(f"illegal path component in {rel!r}: {bad}")
    return list(parts)


def resolve_doc(root: DocRoot, rel: str) -> Path:
    """Map a root-relative path to an absolute file, or raise.

    This is the security boundary for both reads and writes. `Path.resolve()`
    normalises `..` *and* follows symlinks/junctions, so the single
    `is_relative_to` containment test also kills symlink escapes. Callers must
    open the returned path, never `root.base / rel`.
    """
    base = root.base.resolve()

    if root.singleton:
        # Singleton file root: only the file itself, addressed as "" or its name.
        if rel not in ("", root.base.name):
            raise Forbidden(
                f"{root.id} is a single file; only {root.base.name!r} is addressable"
            )
        if not base.is_file():
            raise NotFound(root.base.name)
        return base

    parts = _check_rel(rel)
    if not parts:
        raise Forbidden("empty path")

    candidate = (base / Path(*parts)).resolve()
    if not candidate.is_relative_to(base):
        raise Forbidden(f"path escapes {root.id}")
    if not candidate.is_file():
        raise NotFound(rel)
    return candidate


def resolve_create_target(root: DocRoot, rel: str) -> Path:
    """Resolve the target for a `create` request. The path must NOT yet exist.

    Same containment rules as `resolve_doc`, but allows files that don't
    exist yet — and refuses anything that does (caller probably wanted rename).
    """
    base = root.base.resolve()
    if root.singleton:
        raise Forbidden(f"{root.id} is a single file; cannot create inside it")
    parts = _check_rel(rel)
    if not parts:
        raise Forbidden("empty path")
    candidate = (base / Path(*parts)).resolve()
    if not candidate.is_relative_to(base):
        raise Forbidden(f"path escapes {root.id}")
    if candidate.exists():
        raise Conflict(
            f"file already exists: {rel}",
            sha256=_sha256(candidate.read_bytes()),
            raw="",
        )
    return candidate


def resolve_rename_target(root: DocRoot, src: str, new_rel: str) -> tuple[Path, Path]:
    """Resolve `(src_abs, dst_abs)` for a rename request. `dst` must not exist.

    Both halves are containment-checked. The src must exist (file) so we can
    refuse "rename something that isn't there"; the dst must not, so we don't
    silently overwrite.
    """
    base = root.base.resolve()
    if root.singleton:
        raise Forbidden(f"{root.id} is a single file; cannot rename inside it")

    src_parts = _check_rel(src)
    if not src_parts:
        raise Forbidden("empty src path")
    src_abs = (base / Path(*src_parts)).resolve()
    if not src_abs.is_relative_to(base):
        raise Forbidden(f"src path escapes {root.id}")
    if not src_abs.is_file():
        raise NotFound(src)

    dst_parts = _check_rel(new_rel)
    if not dst_parts:
        raise Forbidden("empty new path")
    dst_abs = (base / Path(*dst_parts)).resolve()
    if not dst_abs.is_relative_to(base):
        raise Forbidden(f"new path escapes {root.id}")
    if dst_abs.exists():
        raise Conflict(
            f"destination already exists: {new_rel}",
            sha256=_sha256(dst_abs.read_bytes()),
            raw="",
        )
    return src_abs, dst_abs


# --- Frontmatter parsing -------------------------------------------------- #
#
# Markdown *rendering* moved to the client (react-markdown + remark-gfm in
# the React SPA) — the legacy markdown-it-py dependency was removed.
# Frontmatter parsing stays server-side so the doc header can show name /
# description without the client re-parsing it.

def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading `---` block off the body.

    Only `SKILL.md` uses frontmatter, and its values are single-line, so this
    is a hand-rolled parser rather than a YAML dependency. Anything that does
    not look like frontmatter is left in the body verbatim — this never
    raises.
    """
    stripped = text.lstrip("﻿")
    if not stripped.startswith("---"):
        return {}, text

    lines = stripped.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}, text  # unterminated — treat the whole thing as body

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            meta.setdefault("_raw", "")
            meta["_raw"] = (meta["_raw"] + "\n" + line).strip()
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key.strip()] = value
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_preserving_newlines(target: Path, content: str, existing: bytes) -> bytes:
    """Write `content`, keeping the file's existing line-ending convention.

    The browser hands us LF (textarea values are LF by spec), but `write_text`
    would translate that to CRLF on Windows — reformatting every LF file in the
    repo on save. So match what's already there and write with `newline=""` to
    switch the translation off.

    Returns the bytes actually written, so the caller can report a sha that
    matches the file on disk.
    """
    if b"\r\n" in existing:
        content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    data = content.encode("utf-8")
    target.write_text(content, encoding="utf-8", newline="")
    return data


# --- Config parsing (read-side) ------------------------------------------- #

def _redact_url(url: str) -> str:
    """Best-effort password redaction that the UI can show safely."""
    return _mask_password(url)


def _connections_summary(cfg: ConnectionsConfig) -> dict[str, Any]:
    """Turn a parsed ConnectionsConfig into a UI-friendly dict (passwords masked)."""
    pd = cfg.pool_defaults
    pool_defaults = {
        "size": pd.size,
        "recycle": pd.recycle,
        "pre_ping": pd.pre_ping,
        "echo": pd.echo,
    }
    conns = []
    for c in cfg.connections:
        conns.append(
            {
                "name": c.name,
                "driver": c.driver,
                "url": _redact_url(c.url),
                "description": c.description,
                "password_set": bool(c.password),
                "read_only": c.read_only,
                "pool": {
                    "size": c.pool.size,
                    "recycle": c.pool.recycle,
                    "pre_ping": c.pool.pre_ping,
                    "echo": c.pool.echo,
                },
                "application_name": c.application_name,
            }
        )
    return {
        "default_workspace": cfg.default_workspace,
        "pool_defaults": pool_defaults,
        "connections": conns,
    }


def _parse_connections(raw: bytes) -> dict[str, Any]:
    """Parse the connections.toml file and return `{raw, ok, summary|error}`.

    `raw` is always returned (the UI shows the text regardless of whether it
    parsed, so a syntax error still has a raw view). `ok=True` ⇒ `summary`
    is set; `ok=False` ⇒ `error` carries the line/column message from toml.
    """
    text = raw.decode("utf-8", errors="replace")
    out: dict[str, Any] = {"raw": text, "size": len(raw)}
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        out["ok"] = False
        out["error"] = str(e)
        return out
    cfg = ConnectionsConfig()
    pd_raw = parsed.get("pool_defaults") or {}
    cfg.pool_defaults = PoolConfig(
        size=int(pd_raw.get("size", 5)),
        recycle=int(pd_raw.get("recycle", 3600)),
        pre_ping=bool(pd_raw.get("pre_ping", True)),
        echo=bool(pd_raw.get("echo", False)),
    )
    cfg.default_workspace = str(parsed.get("default_workspace", ""))
    for entry in parsed.get("connections", []) or []:
        p_raw = entry.get("pool") or {}
        pool = PoolConfig(
            size=int(p_raw.get("size", cfg.pool_defaults.size)),
            recycle=int(p_raw.get("recycle", cfg.pool_defaults.recycle)),
            pre_ping=bool(p_raw.get("pre_ping", cfg.pool_defaults.pre_ping)),
            echo=bool(p_raw.get("echo", cfg.pool_defaults.echo)),
        )
        for k, attr in (("pool_size", "size"), ("pool_recycle", "recycle")):
            if k in entry:
                setattr(pool, attr, int(entry[k]))
        for k, attr in (("pre_ping", "pre_ping"), ("echo", "echo")):
            if k in entry:
                setattr(pool, attr, bool(entry[k]))
        cfg.connections.append(
            ConnectionConfig(
                name=str(entry["name"]),
                driver=str(entry["driver"]),
                url=str(entry["url"]),
                description=str(entry.get("description", "")),
                password=str(entry.get("password", "")),
                read_only=bool(entry.get("read_only", False)),
                pool=pool,
                application_name=str(entry.get("application_name", "sql-harness")),
            )
        )
    out["ok"] = True
    out["summary"] = _connections_summary(cfg)
    return out


# --- Directory tree -------------------------------------------------------- #

_DEPTH_CAP = 12


def _file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in EDITABLE_SUFFIXES:
        return suffix.lstrip(".")
    return "other"


def build_tree(base: Path, *, exclude: tuple[str, ...] = (), _depth: int = 0) -> list[dict]:
    """Recursively describe a directory for the sidebar.

    Symlinked directories are reported but not descended into, so a link
    pointing outside the root cannot smuggle foreign content into the tree.
    """
    if _depth >= _DEPTH_CAP or not base.is_dir():
        return []

    dirs: list[dict] = []
    files: list[dict] = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    for entry in entries:
        if entry.name.startswith(".") or entry.name == "__pycache__" or entry.name in exclude:
            continue
        try:
            if entry.is_symlink() and entry.is_dir():
                dirs.append(
                    {"name": entry.name, "path": entry.name, "type": "link", "children": []}
                )
                continue
            if entry.is_dir():
                dirs.append(
                    {
                        "name": entry.name,
                        "path": entry.name,
                        "type": "dir",
                        "children": build_tree(entry, _depth=_depth + 1),
                    }
                )
            elif entry.is_file():
                kind = _file_kind(entry)
                stat = entry.stat()
                files.append(
                    {
                        "name": entry.name,
                        "path": entry.name,
                        "type": "file",
                        "kind": kind,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "editable": kind != "other",
                    }
                )
        except OSError:
            continue

    # Directories first, then files — each already name-sorted.
    return dirs + files


def _tree_payload(roots: list[DocRoot]) -> dict[str, Any]:
    out = []
    for root in roots:
        if root.singleton:
            stat = root.base.stat() if root.base.is_file() else None
            kind = _file_kind(root.base) if root.base.is_file() else "other"
            tree: list[dict] = []
            if stat:
                tree = [
                    {
                        "name": root.base.name,
                        "path": root.base.name,
                        "type": "file",
                        "kind": kind,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "editable": root.editable and kind != "other",
                    }
                ]
        else:
            tree = build_tree(root.base, exclude=root.exclude)
        out.append(
            {
                "id": root.id,
                "label": root.label,
                "kind": root.kind,
                "connection": root.connection,
                "editable": root.editable,
                "base": str(root.base),
                "singleton": root.singleton,
                "file_count": _count_files(tree),
                "tree": tree,
            }
        )
    return {"version": __version__, "roots": out}


def _count_files(tree: list[dict]) -> int:
    total = 0
    for node in tree:
        if node["type"] == "file":
            total += 1
        else:
            total += _count_files(node.get("children") or [])
    return total


# --- HTTP ------------------------------------------------------------------ #

class DocHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"sql-harness/{__version__}"

    # -- plumbing ---------------------------------------------------------- #

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("BH_SQL_WEB_VERBOSE") == "1":
            super().log_message(fmt, *args)

    def _roots(self) -> list[DocRoot]:
        return build_roots()

    def _send(
        self,
        status: int,
        body: bytes,
        ctype: str,
        extra: dict | None = None,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _fail(self, err: WebError) -> None:
        payload: dict[str, Any] = {"error": str(err), "code": err.code}
        if isinstance(err, Conflict):
            payload["sha256"] = err.sha256
            payload["raw"] = err.raw
        self._json(err.status, payload)

    # -- security ---------------------------------------------------------- #

    def _check_host(self) -> None:
        """Reject requests whose Host is not us.

        This is the DNS-rebinding defence: without it a page on an attacker's
        domain, rebound to 127.0.0.1, is same-origin with this server and can
        read the tree and write files.
        """
        header = self.headers.get("Host", "")
        if header.startswith("["):  # bracketed IPv6 literal
            name, _, rest = header.partition("]")
            name, port = name[1:], rest.lstrip(":")
        else:
            name, _, port = header.rpartition(":")
            if not port.isdigit():  # no port in the header
                name, port = header, ""
        if (
            name.lower() not in self.server.allowed_names
            or port not in ("", str(self.server.server_port))
        ):
            raise Forbidden(f"bad Host header: {header!r}")

    def _check_write_request(self) -> None:
        """Gate every state-changing request.

        `PUT` + `application/json` is the load-bearing part: a cross-origin
        `fetch` with PUT always triggers a preflight, which browsers block
        because we send no CORS headers; and an HTML `<form>` can only produce
        urlencoded/multipart/plain, all refused here. The two collection
        routes (`/api/create`, `/api/rename`) take POST so the verb matches
        "make a new thing"; `_dispatch` already rejects POST against any
        other path so the loose gate below is safe.

        `DELETE` carries no body in our schema, so the Content-Type check
        only fires when there's actually a body to inspect.
        """
        if self.server.read_only:
            raise Forbidden("server is running in read-only mode")

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 0:
            ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise Unsupported(
                    f"writes must be application/json, got {ctype or 'nothing'!r}"
                )

        origin = self.headers.get("Origin")
        if origin and origin not in self.server.allowed_origins:
            raise Forbidden(f"bad Origin: {origin!r}")

    # -- routes ------------------------------------------------------------ #

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        # POST is *only* for the two collection routes below (create / rename,
        # which name their target in the body). Existing docs are overwritten
        # with PUT, so `POST /api/doc` stays a 403 — see _check_write_request
        # and the do_POST branch in _dispatch.
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        try:
            self._check_host()
            route = urlsplit(self.path)
            path, query = route.path, parse_qs(route.query)

            if self.command in ("GET", "HEAD"):
                if path == "/":
                    return self._serve_asset("index.html", "text/html; charset=utf-8")
                if path.startswith("/static/"):
                    return self._serve_asset(path[len("/static/") :], None)
                if path == "/api/health":
                    return self._json(
                        200,
                        {
                            "ok": True,
                            "version": __version__,
                            "read_only": self.server.read_only,
                        },
                    )
                if path == "/api/tree":
                    return self._json(200, _tree_payload(self._roots()))
                if path == "/api/doc":
                    return self._get_doc(query)
                if path == "/api/config":
                    return self._get_config(query)
                if path == "/api/search":
                    return self._search(query)
                raise NotFound(path)

            if self.command == "PUT":
                if path == "/api/doc":
                    return self._put_doc()
                raise NotFound(path)
            if self.command == "DELETE":
                if path == "/api/doc":
                    return self._delete_doc(query)
                raise NotFound(path)
            if self.command == "POST":
                # The two collection routes take their target from the body;
                # `POST /api/doc` is the wrong verb for an overwrite, so it
                # joins everything else in the 403 below.
                if not self.server.read_only and path == "/api/create":
                    return self._create_doc()
                if not self.server.read_only and path == "/api/rename":
                    return self._rename_doc()
                raise Forbidden(
                    "writes must use PUT (or POST /api/create|rename)"
                )
            raise NotFound(path)

        except WebError as err:
            self._fail(err)
        except Exception as exc:  # never leak a traceback to the client
            self._json(500, {"error": f"{type(exc).__name__}: {exc}", "code": "internal"})

    def _serve_asset(self, name: str, ctype: str | None) -> None:
        # Whitelist: index.html exactly, or any hashed chunk (`name-hash.ext`).
        # Reject traversal (no `..`, no leading `/`) before resolving.
        if not name or ".." in name.split("/") or name.startswith("/"):
            raise NotFound(name)
        if name == ASSETS_INDEX:
            cache = "no-store"
            target = name
        elif "/" not in name and _HASSHED_RE.match(name):
            cache = "max-age=31536000, immutable"
            target = name
        else:
            raise NotFound(name)

        from importlib import resources as importlib_resources

        try:
            body = (
                importlib_resources.files("sql_harness")
                .joinpath("_web_assets", target)
                .read_bytes()
            )
        except (ModuleNotFoundError, FileNotFoundError, OSError):
            raise NotFound(f"asset {name} is not bundled")
        if ctype is None:
            ctype = {
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".html": "text/html; charset=utf-8",
            }.get(Path(name).suffix, "application/octet-stream")
        extra = {"Content-Security-Policy": CSP} if ctype.startswith("text/html") else None
        self._send(200, body, ctype, extra, cache_control=cache)

    # -- /api/doc (read) -------------------------------------------------- #

    def _get_doc(self, query: dict[str, list[str]]) -> None:
        root_id = (query.get("root") or [""])[0]
        rel = (query.get("path") or [""])[0]
        root = find_root(self._roots(), root_id)
        target = resolve_doc(root, rel)

        raw_bytes = target.read_bytes()
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise Unsupported(f"{target.name} is not valid UTF-8")

        kind = _file_kind(target)
        stat = target.stat()
        payload: dict[str, Any] = {
            "root": root.id,
            "path": rel or target.name,
            "abs": str(target),
            "raw": raw,
            # `html` removed (PR2 of the React rewrite): the SPA renders
            # markdown client-side via react-markdown. Kept here for one
            # release as a no-op so any older wheel-built UI keeps working.
            "html": None,
            "frontmatter": split_frontmatter(raw)[0] if kind == "md" else {},
            "kind": kind,
            "editable": root.editable and kind != "other",
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "sha256": _sha256(raw_bytes),
        }
        # Side-channel: connections.toml carries a parsed view so the UI can
        # render a structured tab without re-parsing in the browser.
        if root.id == "config":
            payload["parsed"] = _parse_connections(raw_bytes)
        self._json(200, payload)

    # -- /api/doc (write) ------------------------------------------------- #

    def _put_doc(self) -> None:
        self._check_write_request()
        payload = self._read_json()

        root = find_root(self._roots(), str(payload.get("root", "")))
        if not root.editable:
            raise Forbidden(f"{root.id} is read-only (shipped docs in a wheel install)")
        rel = str(payload.get("path", ""))
        target = resolve_doc(root, rel)

        if target.suffix.lower() not in EDITABLE_SUFFIXES:
            raise Forbidden(f"{target.name} is not an editable file type")

        content = payload.get("content")
        if not isinstance(content, str):
            raise Forbidden("content must be a string")
        if "\x00" in content:
            raise Forbidden("content contains a NUL byte")
        if len(content.encode("utf-8")) > MAX_BODY:
            raise TooLarge(f"content exceeds {MAX_BODY} bytes")

        current = target.read_bytes()
        try:
            current_text = current.decode("utf-8")
        except UnicodeDecodeError:
            raise Unsupported(f"{target.name} is not valid UTF-8; refusing to overwrite")

        base_sha = payload.get("base_sha256")
        current_sha = _sha256(current)
        # Optimistic concurrency: agents write these files too (`save`,
        # `apply_skill`), so "open in browser while an agent rewrites it" is a
        # real collision, not a theoretical one.
        if base_sha and base_sha != current_sha:
            raise Conflict(
                "file changed on disk since you loaded it",
                sha256=current_sha,
                raw=current_text,
            )

        # Refuse to save a half-written connections.toml: parse it first so a
        # missing `]]` doesn't kill every downstream `cmd_doctor` call.
        if root.id == "config":
            try:
                tomllib.loads(content)
            except tomllib.TOMLDecodeError as e:
                raise Unsupported(f"connections.toml does not parse: {e}")

        ensure_private_dir(target.parent)
        written = write_preserving_newlines(target, content, current)
        stat = target.stat()
        self._json(
            200,
            {
                "saved": True,
                "root": root.id,
                "path": rel or target.name,
                "sha256": _sha256(written),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            },
        )

    # -- /api/create ------------------------------------------------------ #

    def _create_doc(self) -> None:
        self._check_write_request()
        payload = self._read_json()

        root = find_root(self._roots(), str(payload.get("root", "")))
        if not root.editable:
            raise Forbidden(f"{root.id} is read-only")
        rel = str(payload.get("path", "")).strip()
        kind = str(payload.get("kind", "md")).lower().strip()
        if kind not in CREATE_KINDS:
            raise Forbidden(f"kind must be one of {sorted(CREATE_KINDS)}, got {kind!r}")

        # If the user didn't supply an extension, append the default for the kind.
        suffix = "." + kind
        if not rel.endswith(suffix) and not Path(rel).suffix:
            rel = rel + suffix

        target = resolve_create_target(root, rel)
        if target.suffix.lower() not in EDITABLE_SUFFIXES:
            raise Forbidden(f"{target.suffix!r} is not an editable file type")

        body = payload.get("content")
        if body is None:
            # Friendly starter bodies per kind. The user can always overwrite.
            if kind == "md":
                body = (
                    f"# {target.stem}\n\n"
                    f"_Created from `sql-harness web` on {Path(target).parent.as_posix()}/._\n\n"
                )
            elif kind == "py":
                body = (
                    '"""Saved from `sql-harness web`. Helpers are pre-imported '
                    'when run via `sql-harness run`."""\n\n'
                )
            elif kind == "toml":
                # Connections files are best edited via the structured tab;
                # refuse to scaffold a blank one.
                raise Forbidden(
                    "connections.toml must be created with `sql-harness init` "
                    "so the loader knows it's the canonical config"
                )
        if not isinstance(body, str):
            raise Forbidden("content must be a string")
        if "\x00" in body:
            raise Forbidden("content contains a NUL byte")
        if len(body.encode("utf-8")) > MAX_BODY:
            raise TooLarge(f"content exceeds {MAX_BODY} bytes")

        ensure_private_dir(target.parent)
        target.write_bytes(b"")  # so _sha256(empty) is meaningful
        written = write_preserving_newlines(target, body, b"")
        stat = target.stat()
        self._json(
            200,
            {
                "created": True,
                "root": root.id,
                "path": str(Path(rel)),
                "sha256": _sha256(written),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            },
        )

    # -- /api/rename ------------------------------------------------------ #

    def _rename_doc(self) -> None:
        self._check_write_request()
        payload = self._read_json()
        root = find_root(self._roots(), str(payload.get("root", "")))
        if not root.editable:
            raise Forbidden(f"{root.id} is read-only")
        if root.singleton:
            raise Forbidden(f"{root.id} is a single file; cannot rename")
        src = str(payload.get("src", "")).strip()
        new_rel = str(payload.get("path", "")).strip()
        if not src or not new_rel:
            raise Forbidden("src and path are required")

        # Auto-fill an extension when the user just typed a stem.
        if not Path(new_rel).suffix:
            new_rel = new_rel + Path(src).suffix

        src_abs, dst_abs = resolve_rename_target(root, src, new_rel)
        if dst_abs.suffix.lower() not in EDITABLE_SUFFIXES:
            raise Forbidden(f"{dst_abs.suffix!r} is not an editable file type")

        ensure_private_dir(dst_abs.parent)
        src_abs.rename(dst_abs)
        stat = dst_abs.stat()
        self._json(
            200,
            {
                "renamed": True,
                "root": root.id,
                "src": src,
                "path": str(Path(new_rel)),
                "sha256": _sha256(dst_abs.read_bytes()),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            },
        )

    # -- /api/doc DELETE -------------------------------------------------- #

    def _delete_doc(self, query: dict[str, list[str]]) -> None:
        self._check_write_request()
        root_id = (query.get("root") or [""])[0]
        rel = (query.get("path") or [""])[0]
        root = find_root(self._roots(), root_id)
        if not root.editable:
            raise Forbidden(f"{root.id} is read-only")
        if root.singleton:
            raise Forbidden(f"{root.id} is a single file; cannot delete")
        target = resolve_doc(root, rel)

        # Refuse to delete the agent-workspace root by accident — the root
        # itself is fine; only an empty-dir delete (rmdir on a populated dir)
        # would be problematic, and resolve_doc only matches files.
        try:
            target.unlink()
        except FileNotFoundError:
            raise NotFound(rel)
        self._json(200, {"deleted": True, "root": root.id, "path": rel})

    # -- /api/config ------------------------------------------------------ #

    def _get_config(self, query: dict[str, list[str]]) -> None:
        """Return the parsed view of connections.toml (read-only side channel)."""
        cfg = config_file()
        if not cfg.is_file():
            raise NotFound(str(cfg))
        out = _parse_connections(cfg.read_bytes())
        out["path"] = str(cfg)
        self._json(200, out)

    # -- /api/search ------------------------------------------------------ #

    def _search(self, query: dict[str, list[str]]) -> None:
        """Search filenames and (optionally) file contents across all roots.

        `?q=<text>&content=0|1`  — content=0 is default; pass 1 to also grep
        inside editable files. Bodies > 256 KiB are skipped to keep responses
        bounded. Hard cap on matches is 200 to avoid an accidental flood.
        """
        q = (query.get("q") or [""])[0].strip()
        if not q:
            return self._json(200, {"q": "", "matches": []})
        content_search = (query.get("content") or ["0"])[0] == "1"

        ql = q.lower()
        matches: list[dict[str, Any]] = []
        cap = 256 * 1024
        for root in self._roots():
            if root.singleton:
                continue
            for dirpath, dirnames, filenames in os.walk(root.base):
                # Mirror build_tree's exclusion rules so we don't report
                # files the sidebar wouldn't show.
                dirnames[:] = [
                    d for d in sorted(dirnames)
                    if not d.startswith(".") and d != "__pycache__" and d not in root.exclude
                ]
                for name in filenames:
                    if name.startswith("."):
                        continue
                    full = Path(dirpath) / name
                    rel = full.relative_to(root.base).as_posix()
                    kind = _file_kind(full)
                    hit = None
                    if ql in name.lower():
                        hit = {"field": "filename", "snippet": name}
                    elif content_search and kind in ("md", "py", "toml"):
                        try:
                            if full.stat().st_size > cap:
                                continue
                            raw = full.read_text(encoding="utf-8", errors="ignore")
                        except OSError:
                            continue
                        idx = raw.lower().find(ql)
                        if idx >= 0:
                            lo = max(0, idx - 30)
                            hi = min(len(raw), idx + len(q) + 30)
                            snippet = raw[lo:hi].replace("\n", " ")
                            hit = {"field": "content", "snippet": f"…{snippet}…"}
                    if hit:
                        matches.append({
                            "root": root.id,
                            "root_label": root.label,
                            "path": rel,
                            "name": name,
                            "kind": kind,
                            **hit,
                        })
                        if len(matches) >= 200:
                            return self._json(200, {"q": q, "matches": matches, "truncated": True})
        self._json(200, {"q": q, "matches": matches, "truncated": False})

    # -- helpers ---------------------------------------------------------- #

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Unsupported("bad Content-Length")
        if length > MAX_BODY:
            raise TooLarge(f"body exceeds {MAX_BODY} bytes")
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Unsupported(f"body is not valid JSON: {exc}")
        if not isinstance(payload, dict):
            raise Forbidden("body must be a JSON object")
        return payload


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, addr, handler, *, read_only: bool, allowed_names: frozenset[str]):
        super().__init__(addr, handler)
        self.read_only = read_only
        self.allowed_names = allowed_names
        self.allowed_origins = {
            f"http://{name}:{self.server_port}" for name in allowed_names
        } | {f"http://{name}" for name in allowed_names}


def make_server(host: str = "127.0.0.1", port: int = 8765, *, read_only: bool = False) -> WebServer:
    """Bind a loopback doc server. `port=0` picks an ephemeral port."""
    allowed = LOOPBACK_NAMES | {host.lower()}
    return WebServer((host, port), DocHandler, read_only=read_only, allowed_names=frozenset(allowed))