# Zone-skill auto-surface

> The "domain skills" pattern, adapted for SQL. browser-harness auto-surfaces `domain-skills/<host>/` on `goto_url(...)`; sql-harness auto-surfaces `zones/<connection>/skills/` on `use_workspace(...)`. Same idea, different context key (DSN instead of hostname).

## Detection

- You just called `use_workspace("something")` and you're about to query/modify tables
- You want to know if a per-DSN skill doc already explains this schema/connection
- Setting `$BH_SQL_ZONE_SKILLS=1` makes it automatic

Without the env var you only get the bare `Workspace` object — skill list stays hidden. With the env var, `use_workspace(name)` returns a dict that also includes `zone_skills` and `zone_scripts` (capped at 10 each, sorted).

## Approach

### The two ways to use it

**1. Env-var auto-surface (the browser-harness way):**

```bash
BH_SQL_ZONE_SKILLS=1 uv run sql-harness <<'PY'
info = use_workspace("aliyun_mysql")
# info is a dict now, not the bare Workspace
print("zone_skills:  ", info["zone_skills"])
print("zone_scripts:", info["zone_scripts"])
# workspace is reachable via info["workspace"]
ws = info["workspace"]
print("driver:", ws.driver.name)
PY
```

`use_workspace(name)` returns a dict:
```python
{
    "workspace": <Workspace object>,
    "connection": "aliyun_mysql",
    "driver": "mysql",
    "zone_skills":  ["aliyun_mysql-setup.md"],          # up to 10
    "zone_scripts": ["schema_init", "seed_products"],   # up to 10
    "hint": "Set $BH_SQL_ZONE_SKILLS=1 to auto-surface these. ..."
}
```

**2. Force-show (always, regardless of env):**

```python
use_workspace("aliyun_mysql")
info = use_workspace_info("aliyun_mysql")
# always returns a dict, no env required
print(info["skills"], info["scripts"])
```

Use `use_workspace_info` when you want the surface unconditionally (e.g. inside a script that needs to list the zone).

### What the agent should do with the surfaced list

```python
info = use_workspace("aliyun_mysql")
for skill in info["zone_skills"]:
    body = apply_skill(skill[:-3])        # strip .md
    # ... read the body before touching tables ...
```

The skill is a markdown file with non-obvious schema knowledge ("the `orders` table has `sku` not `product_id`", "stock was added in v2", etc.) that the agent wouldn't infer from `describe()` alone — equivalent of a `README.md` printed when you `cd` into a new directory. Low cost (10 filenames, no full reads), high payoff (the agent now knows skills exist and can read the relevant ones).

## Gotchas

- **Env var must be set BEFORE launching sql-harness** (same shell that runs `uv run sql-harness`). Check happens at `use_workspace` call time, not at process start.
- **Default off** — if you `print(use_workspace("x"))` and see a `Workspace` object (not a dict), the flag is off. The `hint` field in the surfaced dict tells you how to turn it on.
- **10 skill filenames + 10 script filenames per zone** — beyond that the list gets noisy and the agent's context fills with names it'll never read. Capped at 10 to keep `use_workspace` returns short; if you need priority, rename (sort is alphabetical).
- **No full reads.** Auto-surface is a hint, not a preload — the agent still has to `apply_skill` the ones it wants.
- **Skills are markdown only** — `.md`. Python helpers in the zone (`helpers.py` is auto-loaded) are not listed; that's `apply_skill` of code, not knowledge.
- **Returns a dict, not the Workspace** when the flag is on. Access via `info["workspace"]`. Code that did `ws = use_workspace(name); ws.engine.connect()` will break — change to `ws = use_workspace(name)["workspace"]` (or use the env-var-default branch).
- **The flag affects ALL `use_workspace` calls** in the process, including ones inside CLI subcommands (`save`, `run`, `ssh`). For pure utility scripts that don't want the surface noise, leave the env off.
- **Sublayer recursion: top-level only — change to `rglob` when needed.** Current implementation uses `d.glob("*.md")` (top-level). If an agent starts organizing a zone into subdirs (e.g. `zones/prod_pg/skills/postgres/plan-reading.md`), the PG-depth file will **not** surface — only top-level filenames do. **Trigger to fix**: when the first person writes a `.md` under a subdir of a zone's `skills/`, change `_list_zone_skills` in `src/sql_harness/helpers.py` from `d.glob("*.md")` to `d.rglob("*.md")` and return paths relative to the zone base (e.g. `postgres/plan-reading.md`). Until then, the top-level discipline is enough. This mirrors browser-harness's `rglob` behavior on `domain-skills/<host>/` but defers the change to when zone dir structure actually needs it.

## Cross-DB / per-zone considerations

Auto-surface is **per-zone** — each `use_workspace(name)` shows that name's zone. Five zones → five different surfaces. There's no cross-zone "global" surface; for that, use `list_skills()` (global) or `use_workspace_info` (current zone only).

## One-liner recipe

```python
# Default: silent, returns Workspace
use_workspace("name")

# Surface on: returns dict with zone_skills + zone_scripts
os.environ["BH_SQL_ZONE_SKILLS"] = "1"   # or set in shell before sql-harness
info = use_workspace("name")
```
