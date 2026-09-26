"""End-to-end smoke test for `sql-harness web` (dev harness, not a unit test).

Boots the real HTTP server on an ephemeral port via `cmd_web`, then drives
every new/changed route the way the browser does: read → create → rename →
edit → delete, plus the connections.toml structured view and search.

Server runs in-process (no subprocess pipe-deadlock risk); the loop just
calls `serve_forever` in a thread and tears down on exit.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# Make `sql_harness` importable without `pip install -e .`
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from sql_harness import webapp  # noqa: E402
from sql_harness.paths import workspace_dir  # noqa: E402

HOME = Path(os.environ["BH_SQL_HOME"])


def req(url, method="GET", payload=None, headers=None):
    # `Connection: close` forces urllib to open a fresh socket per call —
    # this dodges Windows' "send a second request on the keep-alive socket
    # before the server replies" jitter that shows up as ConnectionAborted.
    head = {"Connection": "close", **(headers or {})}
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        head.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as res:
            raw = res.read()
            try:
                return res.status, json.loads(raw)
            except ValueError:
                return res.status, raw.decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")[:200]


def main():
    # Make sure runtime zone dirs exist (and are clean of test leftovers).
    ws = workspace_dir()
    for d in ("zones/meta/skills", "zones/example_pg/skills"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    for leftover in ws.glob("zones/*/skills/zz_*.md"):
        leftover.unlink()

    httpd = webapp.make_server("127.0.0.1", 0, read_only=False)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    fails = []

    def check(label, cond, extra=""):
        print(("  ok  " if cond else "  FAIL") + f"  {label}" + (f"   {extra}" if extra else ""))
        if not cond:
            fails.append(label)

    print(f"server {base}\n")

    # --- health / tree -----------------------------------------------------
    print("[health + tree]")
    st, h = req(base + "/api/health")
    check("GET /api/health", st == 200 and h["ok"] is True)
    st, tree = req(base + "/api/tree")
    ids = {r["id"]: r for r in tree["roots"]}
    check("GET /api/tree", st == 200 and len(ids) > 3)
    check("config root present", "config" in ids, f"roots={sorted(ids)}")
    check("config root is singleton", ids.get("config", {}).get("singleton") is True)
    check("config root has 1 file", ids.get("config", {}).get("file_count") == 1)
    check("tree marks editable", ids.get("interaction", {}).get("editable") is True)

    # --- connections.toml --------------------------------------------------
    print("\n[connections.toml]")
    st, doc = req(base + "/api/doc?root=config&path=connections.toml")
    check("GET config doc", st == 200, f"status={st}")
    check("config kind is toml", doc.get("kind") == "toml")
    check("config editable", doc.get("editable") is True)
    check("parsed present", isinstance(doc.get("parsed"), dict))
    summ = (doc.get("parsed") or {}).get("summary") or {}
    check("parsed 2 connections", len(summ.get("connections", [])) == 2,
          f"got {len(summ.get('connections', []))}")
    check("password redacted in url",
          "***" in summ.get("connections", [{}])[0].get("url", ""),
          summ.get("connections", [{}])[0].get("url", ""))
    check("default_workspace", summ.get("default_workspace") == "local_pg")

    st, h = req(base + "/api/config")
    check("GET /api/config", st == 200 and h.get("ok") is True)

    # --- create / rename / edit / delete -----------------------------------
    print("\n[CRUD]")
    st, r = req(base + "/api/create", "POST",
                {"root": "meta-skills", "path": "zz_smoke_test", "kind": "md"})
    check("POST /api/create (auto .md)",
          st == 200 and r.get("path") == "zz_smoke_test.md",
          f"status={st} {r}")
    created = workspace_dir() / "zones" / "meta" / "skills" / "zz_smoke_test.md"
    check("file on disk", created.is_file())

    st, r = req(base + "/api/create", "POST",
                {"root": "meta-skills", "path": "zz_smoke_test", "kind": "md"})
    check("duplicate create → 409", st == 409, f"status={st} code={r.get('code')}")

    st, r = req(base + "/api/create", "POST",
                {"root": "meta-skills", "path": "../escape.md", "kind": "md"})
    check("traversal create → 403", st == 403, f"status={st}")

    st, r = req(base + "/api/doc?root=meta-skills&path=zz_smoke_test.md", "DELETE")
    check("DELETE", st == 200 and r.get("deleted") is True, f"status={st} {r}")
    check("file gone", not created.exists())

    st, r = req(base + "/api/create", "POST",
                {"root": "meta-skills", "path": "zz_rename_me.md", "kind": "md",
                 "content": "# original\n"})
    check("create for rename", st == 200, f"status={st}")
    st, r = req(base + "/api/rename", "POST",
                {"root": "meta-skills", "src": "zz_rename_me.md", "path": "zz_renamed.md"})
    check("POST /api/rename",
          st == 200 and r.get("path") == "zz_renamed.md",
          f"{st} {r}")
    check("old gone",
          not (workspace_dir() / "zones" / "meta" / "skills" / "zz_rename_me.md").exists())
    renamed = workspace_dir() / "zones" / "meta" / "skills" / "zz_renamed.md"
    check("new exists", renamed.is_file())

    # After the rename above, `zz_renamed.md` exists; rename it again to
    # something that doesn't exist (must 404). Using `zz_renamed.md` as the
    # new src means we test the "source not found" path, not "dest collides".
    st, r = req(base + "/api/rename", "POST",
                {"root": "meta-skills", "src": "zz_renamed.md",
                 "path": "zz_renamed_twice.md"})
    check("rename ok second time",
          st == 200 and r.get("path") == "zz_renamed_twice.md",
          f"{st} {r}")

    st, r = req(base + "/api/rename", "POST",
                {"root": "meta-skills", "src": "zz_does_not_exist.md",
                 "path": "zz_x.md"})
    check("rename missing src → 404", st == 404, f"status={st}")

    st, r = req(base + "/api/rename", "POST",
                {"root": "meta-skills", "src": "../../SKILL.md", "path": "x.md"})
    check("rename traversal → 403", st == 403, f"status={st}")

    st, r = req(base + "/api/doc?root=meta-skills&path=zz_renamed_twice.md", "DELETE")
    check("cleanup delete", st == 200, f"status={st}")

    # --- write guard: bad origin / host / wrong verb ----------------------
    print("\n[guards]")
    st, r = req(base + "/api/create", "POST",
                {"root": "meta-skills", "path": "zz_x.md", "kind": "md"},
                headers={"Origin": "http://evil.example.com"})
    check("foreign Origin → 403", st == 403, f"status={st}")

    st, r = req(base + "/api/create", "POST",
                {"root": "meta-skills", "path": "zz_x.md", "kind": "md"},
                headers={"Host": "evil.example.com"})
    check("foreign Host → 403", st == 403, f"status={st}")

    st, r = req(base + "/api/doc", "POST", {"root": "meta-skills"})
    check("POST /api/doc → 403", st == 403, f"status={st}")

    # --- config write validation -----------------------------------------
    print("\n[config write]")
    st, doc = req(base + "/api/doc?root=config&path=connections.toml")
    st, r = req(base + "/api/doc", "PUT",
                {"root": "config", "path": "connections.toml",
                 "content": "this is [ not toml =", "base_sha256": doc["sha256"]})
    check("invalid TOML refused", st == 415, f"status={st}")

    st, r = req(base + "/api/doc", "PUT",
                {"root": "config", "path": "connections.toml",
                 "content": doc["raw"], "base_sha256": "0" * 64})
    check("stale sha → 409", st == 409, f"status={st}")

    st, r = req(base + "/api/doc", "PUT",
                {"root": "config", "path": "connections.toml",
                 "content": doc["raw"], "base_sha256": doc["sha256"]})
    check("valid TOML re-save",
          st == 200 and r.get("saved") is True,
          f"status={st} {r}")

    # --- search -----------------------------------------------------------
    print("\n[search]")
    st, r = req(base + "/api/search?q=zzz_no_such_thing")
    check("empty search", st == 200 and r["matches"] == [])

    # Create a file with a unique stem so the filename search is unambiguous
    # (the meta-skills dir otherwise has only shipped preset files).
    req(
        base + "/api/create", "POST",
        {"root": "meta-skills", "path": "zz_search_target.md", "kind": "md",
         "content": "# SearchTarget\nbody uses sentinel_use_workspace token\n"},
    )
    st, r = req(base + "/api/search?q=zz_search_target&content=0")
    check("filename search hits",
          st == 200 and any(m["path"].endswith("zz_search_target.md") for m in r["matches"]),
          f"{len(r.get('matches',[]))} matches")

    st, r = req(base + "/api/search?q=sentinel_use_workspace&content=1")
    check("content search hits",
          st == 200 and any(m["field"] == "content" for m in r["matches"]),
          f"{len(r.get('matches',[]))} matches")
    req(base + "/api/doc?root=meta-skills&path=zz_search_target.md", "DELETE")

    # --- read-only server --------------------------------------------------
    print("\n[read-only mode]")
    httpd.shutdown()
    httpd2 = webapp.make_server("127.0.0.1", 0, read_only=True)
    threading.Thread(target=httpd2.serve_forever, daemon=True).start()
    b2 = f"http://127.0.0.1:{httpd2.server_port}"
    st, r = req(b2 + "/api/create", "POST",
                {"root": "meta-skills", "path": "zz_ro.md", "kind": "md"})
    check("create refused in read-only", st == 403, f"status={st}")
    st, r = req(b2 + "/api/doc?root=meta-skills&path=zz_ro.md", "DELETE")
    check("delete refused in read-only", st == 403, f"status={st}")
    st, r = req(b2 + "/api/tree")
    check("tree still readable", st == 200)
    httpd2.shutdown()

    print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())