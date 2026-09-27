"""Unit tests for webapp.py — doc roots, path safety, rendering, HTTP.

No DB. The HTTP tests bind a real socket on loopback port 0, so they need no
external network; set `BH_SQL_NO_SOCKET=1` to skip them in a sandbox that
forbids binding.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from sql_harness import webapp as W
from sql_harness.paths import workspace_dir

NO_SOCKET = os.environ.get("BH_SQL_NO_SOCKET") == "1"


# --- fixtures -------------------------------------------------------------- #

@pytest.fixture()
def workspace(isolated_home: Path) -> Path:
    """An isolated agent-workspace with one zone, ready to browse."""
    ws = workspace_dir()
    (ws / "zones" / "alpha" / "skills").mkdir(parents=True)
    (ws / "zones" / "alpha" / "scripts").mkdir(parents=True)
    # write_bytes, not write_text: write_text would translate LF to CRLF on
    # Windows, so the line-ending assertions below would be testing the
    # fixture rather than the server.
    (ws / "zones" / "alpha" / "skills" / "note.md").write_bytes(b"# note\n")
    (ws / "zones" / "alpha" / "scripts" / "q.py").write_bytes(b"print(1)\n")
    (ws / "agent_helpers.py").write_bytes(b"# helpers\n")
    return ws


@pytest.fixture()
def server(workspace: Path):
    if NO_SOCKET:
        pytest.skip("BH_SQL_NO_SOCKET=1")
    httpd = W.make_server("127.0.0.1", 0, read_only=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def ro_server(workspace: Path):
    if NO_SOCKET:
        pytest.skip("BH_SQL_NO_SOCKET=1")
    httpd = W.make_server("127.0.0.1", 0, read_only=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def _get(url: str, **headers):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.headers, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def _put(url: str, payload: dict, **headers):
    body = json.dumps(payload).encode("utf-8")
    head = {"Content-Type": "application/json"}
    head.update(headers)
    req = urllib.request.Request(url, data=body, headers=head, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.headers, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def _json_body(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


# --- roots ----------------------------------------------------------------- #

def test_build_roots_includes_source_roots(workspace: Path) -> None:
    ids = {r.id for r in W.build_roots()}
    # These live in the repo checkout, which is where the tests run from.
    assert {"skill", "interaction"} <= ids


def test_build_roots_includes_runtime_root(workspace: Path) -> None:
    runtime = [r for r in W.build_roots() if r.id == "runtime"]
    assert runtime and runtime[0].kind == "runtime"


def test_build_roots_enumerates_zone_dirs(workspace: Path) -> None:
    zones = [r for r in W.build_roots() if r.id == "zone:alpha"]
    assert len(zones) == 1
    assert zones[0].connection == "alpha"


def test_build_roots_ignores_zone_files(workspace: Path) -> None:
    (workspace / "zones" / "notadir").write_text("x", encoding="utf-8")
    assert "zone:notadir" not in {r.id for r in W.build_roots()}


def test_build_roots_ignores_dot_zone_dirs(workspace: Path) -> None:
    (workspace / "zones" / ".hidden").mkdir()
    assert "zone:.hidden" not in {r.id for r in W.build_roots()}


def test_runtime_root_excludes_zones(workspace: Path) -> None:
    """Zones get their own roots; listing them twice would duplicate the tree."""
    runtime = next(r for r in W.build_roots() if r.id == "runtime")
    names = {node["name"] for node in W.build_tree(runtime.base, exclude=runtime.exclude)}
    assert "zones" not in names
    assert "agent_helpers.py" in names


# --- path safety ----------------------------------------------------------- #

@pytest.fixture()
def dir_root(workspace: Path) -> W.DocRoot:
    return W.DocRoot(id="runtime", label="ws", kind="runtime", base=workspace)


@pytest.fixture()
def file_root(tmp_path: Path) -> W.DocRoot:
    target = tmp_path / "SKILL.md"
    target.write_bytes(b"# x\n")
    return W.DocRoot(id="skill", label="SKILL.md", kind="source", base=target)


def test_resolve_accepts_nested_relative(dir_root: W.DocRoot) -> None:
    got = W.resolve_doc(dir_root, "zones/alpha/skills/note.md")
    assert got.name == "note.md" and got.is_file()


@pytest.mark.parametrize(
    "rel",
    ["../secrets.md", "..\\secrets.md", "zones/../../secrets.md", "..", "./../x.md"],
)
def test_resolve_rejects_parent_traversal(dir_root: W.DocRoot, rel: str) -> None:
    with pytest.raises(W.Forbidden):
        W.resolve_doc(dir_root, rel)


@pytest.mark.parametrize(
    "rel",
    [
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "C:/Windows/win.ini",
        "c:relative.md",
        "\\\\server\\share\\x.md",
    ],
)
def test_resolve_rejects_absolute_paths(dir_root: W.DocRoot, rel: str) -> None:
    with pytest.raises(W.Forbidden):
        W.resolve_doc(dir_root, rel)


@pytest.mark.parametrize("rel", [".git/config", ".env", "zones/.ssh/id_rsa"])
def test_resolve_rejects_dot_components(dir_root: W.DocRoot, rel: str) -> None:
    with pytest.raises(W.Forbidden):
        W.resolve_doc(dir_root, rel)


def test_resolve_rejects_nul_byte(dir_root: W.DocRoot) -> None:
    with pytest.raises(W.Forbidden):
        W.resolve_doc(dir_root, "a\x00b.md")


def test_resolve_rejects_empty_path(dir_root: W.DocRoot) -> None:
    with pytest.raises(W.Forbidden):
        W.resolve_doc(dir_root, "")


def test_resolve_rejects_directory(dir_root: W.DocRoot) -> None:
    with pytest.raises(W.NotFound):
        W.resolve_doc(dir_root, "zones")


def test_resolve_rejects_symlink_escape(dir_root: W.DocRoot, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = dir_root.base / "escape.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable (needs Developer Mode or admin on Windows)")
    with pytest.raises(W.Forbidden):
        W.resolve_doc(dir_root, "escape.md")


def test_singleton_root_accepts_only_its_own_file(file_root: W.DocRoot) -> None:
    assert W.resolve_doc(file_root, "").name == "SKILL.md"
    assert W.resolve_doc(file_root, "SKILL.md").name == "SKILL.md"
    for rel in ["other.md", "../SKILL.md", "a/b.md"]:
        with pytest.raises(W.Forbidden):
            W.resolve_doc(file_root, rel)


def test_find_root_unknown_raises() -> None:
    with pytest.raises(W.NotFound):
        W.find_root([], "nope")


# --- tree ------------------------------------------------------------------ #

def test_tree_sorted_dirs_first(workspace: Path) -> None:
    tree = W.build_tree(workspace)
    types = [node["type"] for node in tree]
    assert types == sorted(types, key=lambda t: t == "file")


def test_tree_skips_pycache_and_dotfiles(workspace: Path) -> None:
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__" / "x.pyc").write_text("", encoding="utf-8")
    (workspace / ".secret").write_text("", encoding="utf-8")
    names = {node["name"] for node in W.build_tree(workspace)}
    assert "__pycache__" not in names and ".secret" not in names


def test_tree_marks_editable_and_kind(workspace: Path) -> None:
    tree = W.build_tree(workspace / "zones" / "alpha" / "scripts")
    node = next(n for n in tree if n["name"] == "q.py")
    assert node["kind"] == "py" and node["editable"] is True


def test_tree_depth_cap(workspace: Path) -> None:
    deep = workspace
    for i in range(15):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "x.md").write_text("", encoding="utf-8")

    def depth(nodes: list[dict]) -> int:
        if not nodes:
            return 0
        return 1 + max((depth(n.get("children") or []) for n in nodes), default=0)

    assert depth(W.build_tree(workspace)) <= W._DEPTH_CAP + 1


# --- frontmatter parsing -------------------------------------------------- #
# (markdown rendering moved to the React client in PR2 of the React rewrite;
# the server still parses frontmatter so the doc header can show name /
# description without the client needing to do it again.)

def test_split_frontmatter_parses_keys() -> None:
    meta, body = W.split_frontmatter('---\nname: x\ndescription: "a b"\n---\n# Body\n')
    assert meta == {"name": "x", "description": "a b"}
    assert body.startswith("# Body")


def test_split_frontmatter_absent() -> None:
    meta, body = W.split_frontmatter("# Hi\ntext\n")
    assert meta == {} and body.startswith("# Hi")


def test_split_frontmatter_unterminated_is_all_body() -> None:
    text = "---\nname: x\n# no closing fence\n"
    meta, body = W.split_frontmatter(text)
    assert meta == {} and body == text


def test_split_frontmatter_strips_bom() -> None:
    meta, _ = W.split_frontmatter("\ufeff---\nname: x\n---\nbody\n")
    assert meta == {"name": "x"}


def test_split_frontmatter_keeps_non_kv_lines() -> None:
    meta, _ = W.split_frontmatter("---\nname: x\njust a stray line\n---\nbody\n")
    assert meta["name"] == "x"
    assert "just a stray line" in meta["_raw"]


# --- HTTP ------------------------------------------------------------------ #

def test_http_index_served(server: str) -> None:
    status, headers, body = _get(server + "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"sql-harness" in body


def test_http_index_sets_csp(server: str) -> None:
    _, headers, _ = _get(server + "/")
    assert "default-src 'none'" in headers["Content-Security-Policy"]


def test_http_static_asset(server: str) -> None:
    # PR2: Vite emits hashed JS/CSS/Editor chunks directly in `_web_assets/`.
    # The exact hash changes every build, so we read the bundled directory to
    # find one instead of hard-coding the name. Whitelist + traversal
    # rejection tests below still cover the security guard.
    from importlib import resources as importlib_resources

    assets_root = importlib_resources.files("sql_harness").joinpath("_web_assets")
    css_files = [
        f.name for f in assets_root.iterdir() if f.name.endswith(".css")
    ] if assets_root.is_dir() else []
    assert css_files, "expected at least one built CSS file"
    asset = css_files[0]
    status, headers, body = _get(f"{server}/static/{asset}")
    assert status == 200 and headers["Content-Type"].startswith("text/css") and body


def test_http_static_whitelist_rejects_other_names(server: str) -> None:
    # Legacy name list (no longer whitelisted) and arbitrary nested paths must 404.
    # `index.html` is on the whitelist so it serves; traversal attacks still 404.
    for name in ["../cli.py", "cli.py", "..%2fcli.py", "app.css", "app.js"]:
        status, _, _ = _get(f"{server}/static/{name}")
        assert status == 404, name
    # Nested paths and traversal segments are blocked even when the rest looks valid.
    for name in ["etc/passwd", "foo/bar.css", "../assets/x.js", "x.js", "index"]:
        status, _, _ = _get(f"{server}/static/{name}")
        assert status == 404, name


def test_http_static_hashed_asset_has_immutable_cache(server: str) -> None:
    """Hashed chunks must be marked Cache-Control: immutable."""
    from importlib import resources as importlib_resources

    assets_root = importlib_resources.files("sql_harness").joinpath("_web_assets")
    if not assets_root.is_dir():
        return  # no build yet — skip
    css_files = [str(f.name) for f in assets_root.iterdir() if f.name.endswith(".css")]
    if not css_files:
        return
    _, headers, _ = _get(f"{server}/static/{css_files[0]}")
    cc = headers.get("Cache-Control", "")
    assert "immutable" in cc, cc


def test_http_health(server: str) -> None:
    status, _, body = _get(server + "/api/health")
    assert status == 200 and _json_body(body)["read_only"] is False


def test_http_tree_lists_zone(server: str) -> None:
    status, _, body = _get(server + "/api/tree")
    assert status == 200
    roots = {r["id"]: r for r in _json_body(body)["roots"]}
    assert "zone:alpha" in roots
    assert roots["zone:alpha"]["file_count"] >= 2


def test_http_doc_returns_raw_and_frontmatter(server: str) -> None:
    # PR2 of the React rewrite moved markdown rendering to the client (react-markdown).
    # The backend still returns `raw` + `frontmatter` + `sha256`; the `html`
    # field is preserved for back-compat but is now always null.
    status, _, body = _get(server + "/api/doc?root=zone:alpha&path=skills/note.md")
    doc = _json_body(body)
    assert status == 200
    assert doc["raw"] == "# note\n"
    assert doc["html"] is None  # client renders markdown now
    assert doc["editable"] is True
    assert len(doc["sha256"]) == 64


def test_http_doc_non_markdown_has_no_html(server: str) -> None:
    _, _, body = _get(server + "/api/doc?root=zone:alpha&path=scripts/q.py")
    assert _json_body(body)["html"] is None


@pytest.mark.parametrize(
    "path",
    ["../../../etc/passwd", "..\\..\\secrets.md", ".git/config", "skills/../../x.md"],
)
def test_http_doc_traversal_forbidden(server: str, path: str) -> None:
    status, _, _ = _get(f"{server}/api/doc?root=zone:alpha&path={urllib.parse.quote(path)}")
    assert status == 403


def test_http_doc_unknown_root_404(server: str) -> None:
    status, _, _ = _get(server + "/api/doc?root=nope&path=x.md")
    assert status == 404


def test_http_put_saves_utf8_and_preserves_lf(server: str) -> None:
    target = workspace_dir() / "zones" / "alpha" / "skills" / "note.md"
    _, _, body = _get(server + "/api/doc?root=zone:alpha&path=skills/note.md")
    sha = _json_body(body)["sha256"]

    content = "# 冒烟\n\n中文 🎯\n"
    status, _, res = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": content, "base_sha256": sha},
    )
    assert status == 200
    assert target.read_text(encoding="utf-8") == content
    # The repo is LF-only; write_text would otherwise turn this into CRLF.
    assert b"\r\n" not in target.read_bytes()
    assert _json_body(res)["sha256"] == W._sha256(target.read_bytes())


def test_http_put_preserves_crlf(server: str) -> None:
    target = workspace_dir() / "zones" / "alpha" / "skills" / "crlf.md"
    target.write_bytes(b"# a\r\nb\r\n")
    status, _, _ = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/crlf.md", "content": "# a\nb\nc\n"},
    )
    assert status == 200
    assert target.read_bytes() == b"# a\r\nb\r\nc\r\n"


def test_http_put_requires_json_content_type(server: str) -> None:
    status, _, _ = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": "x"},
        **{"Content-Type": "text/plain"},
    )
    assert status == 415


def test_http_post_is_rejected(server: str) -> None:
    req = urllib.request.Request(
        server + "/api/doc",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 403


def test_http_put_rejects_foreign_host(server: str) -> None:
    status, _, _ = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": "x"},
        **{"Host": "evil.example.com"},
    )
    assert status == 403


def test_http_get_rejects_foreign_host(server: str) -> None:
    status, _, _ = _get(server + "/api/tree", **{"Host": "evil.example.com"})
    assert status == 403


def test_http_put_rejects_foreign_origin(server: str) -> None:
    status, _, _ = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": "x"},
        **{"Origin": "http://evil.example.com"},
    )
    assert status == 403


def test_http_put_conflict_on_stale_sha(server: str) -> None:
    target = workspace_dir() / "zones" / "alpha" / "skills" / "note.md"
    before = target.read_text(encoding="utf-8")
    status, _, body = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": "clobber\n",
         "base_sha256": "0" * 64},
    )
    assert status == 409
    payload = _json_body(body)
    assert payload["code"] == "conflict" and payload["raw"] == before
    assert target.read_text(encoding="utf-8") == before  # untouched


def test_http_put_force_after_conflict(server: str) -> None:
    target = workspace_dir() / "zones" / "alpha" / "skills" / "note.md"
    _, _, body = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": "forced\n",
         "base_sha256": "0" * 64},
        **{},
    )
    fresh = _json_body(body)["sha256"]
    status, _, _ = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": "forced\n",
         "base_sha256": fresh},
    )
    assert status == 200
    assert target.read_text(encoding="utf-8") == "forced\n"


def test_http_put_traversal_forbidden(server: str) -> None:
    status, _, _ = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "../../../tmp/evil.md", "content": "x"},
    )
    assert status == 403


def test_http_put_rejects_non_editable_suffix(server: str) -> None:
    (workspace_dir() / "zones" / "alpha" / "notes.txt").write_text("x", encoding="utf-8")
    status, _, _ = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "notes.txt", "content": "y"},
    )
    assert status == 403


def test_http_put_rejects_nul(server: str) -> None:
    status, _, _ = _put(
        server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": "a\x00b"},
    )
    assert status == 403


def test_http_put_rejects_bad_json(server: str) -> None:
    req = urllib.request.Request(
        server + "/api/doc",
        data=b"not json",
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 415


def test_http_read_only_refuses_writes(ro_server: str) -> None:
    status, _, body = _put(
        ro_server + "/api/doc",
        {"root": "zone:alpha", "path": "skills/note.md", "content": "x"},
    )
    assert status == 403 and "read-only" in _json_body(body)["error"]
    # Reads still work.
    assert _get(ro_server + "/api/tree")[0] == 200


def test_http_unknown_route_404(server: str) -> None:
    assert _get(server + "/nope")[0] == 404


# --- CLI ------------------------------------------------------------------- #

def test_cli_web_help(isolated_home: Path) -> None:
    import subprocess
    import sys

    env = os.environ.copy()
    env["BH_SQL_HOME"] = str(isolated_home)
    p = subprocess.run(
        [sys.executable, "-m", "sql_harness.run", "web", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    assert p.returncode == 0
    for flag in ["--port", "--host", "--no-open", "--read-only"]:
        assert flag in p.stdout
