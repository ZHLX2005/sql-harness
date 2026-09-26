# The save → run cycle

> Executed code is saved as reusable Python, then re-run whenever you need it again.

## Detection

- You got a heredoc working against a workspace
- The operations will be reused later (rebuild schema, seed data, place order, etc.)
- Resist: writing ONE big script at the end that captures everything

## Approach (the 3-step loop)

```
[block 1]  heredoc → save <name1> → run <name1>       # verify it works
[block 2]  heredoc → save <name2> → run <name2>       # verify
[block 3]  heredoc → save <name3> → run <name3>       # verify
...
```

- `save <name>` writes the heredoc body to `agent-workspace/zones/<connection>/scripts/<name>.py` (auto-prepended with a `"""Saved by sql-harness save ..."""` provenance header).
- `run <name>` re-executes the saved script verbatim in a fresh process with helpers pre-imported — exactly like a heredoc.

### Concrete walkthrough (from the aliyun_mysql setup)

```bash
# Stage 1: schema (DDL only)
cat > /tmp/schema.sh <<'PY'
use_workspace("aliyun_mysql")
execute("CREATE DATABASE IF NOT EXISTS aliyun_demo ...")
execute("CREATE TABLE aliyun_demo.products (...) ENGINE=InnoDB ...")
execute("CREATE TABLE aliyun_demo.orders ...")
execute("CREATE TABLE aliyun_demo.order_items ...")
print("schema:", list_tables(schema="aliyun_demo"))
PY
uv run sql-harness save schema_init -c aliyun_mysql < /tmp/schema.sh
uv run sql-harness run schema_init -c aliyun_mysql     # verify

# Stage 2: seed (idempotent delete+insert)
uv run sql-harness save seed_products -c aliyun_mysql < /tmp/seed.sh
uv run sql-harness run seed_products -c aliyun_mysql   # verify

# Stage 3: place an order (relational insert)
uv run sql-harness save place_order -c aliyun_mysql < /tmp/order.sh
uv run sql-harness run place_order -c aliyun_mysql     # verify
```

After 3 stages the zone has 3 scripts. Tomorrow when you need to rebuild the demo from scratch:

```bash
uv run sql-harness run schema_init -c aliyun_mysql && \
uv run sql-harness run seed_products -c aliyun_mysql && \
uv run sql-harness run place_order -c aliyun_mysql
```

Three commands, zero re-typing, exactly the same behavior as when you originally wrote them.

If a saved script becomes "load-bearing" (you run it weekly), promote the WHY into a zone-skill:
- `scripts/demo_setup.py` — runs
- `skills/demo_schema.md` — explains what the tables mean and how they relate (`apply_skill("demo_schema")` when an agent is ABOUT to touch the tables; run the script when the agent WANTS to rebuild from scratch — different lifecycles).

## Gotchas

- **`save` writes the heredoc body verbatim** — including any `print()` for debugging. Strip prints before saving (or accept them; useful as audit trail).
- **`save` is idempotent on the name** — re-saving overwrites. For version history use git on `agent-workspace/zones/<conn>/scripts/` (source; the runtime copy is `~/.config/sql-harness/.../scripts/`).
- **Fresh process per `run`** — globals set in one heredoc don't persist. Shared state goes in a zone-skill or constants file, not a script. If block 2 needs a value created in block 1: pass via `execute("INSERT INTO _state VALUES ...")` or import from a `setup_<name>.py`.
- **The provenance header is added by `save`** — don't write your own docstring, or you'll get `"""docstring\n""" + """docstring"""`.
- **Run-mode sees the same helper namespace as heredoc** — `use_workspace`, `query`, `execute`, `with_transaction`, `explain`, `list_tables`, `describe`, all of `ssh_*` are pre-imported.

## One-liner recipe

```bash
# Working block N: heredoc → save → run. Repeat. Don't batch.
uv run sql-harness save <name> -c <conn> < heredoc.py
uv run sql-harness run  <name> -c <conn>      # verify it works
# ... next block
```
