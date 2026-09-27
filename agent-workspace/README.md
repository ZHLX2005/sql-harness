# Generated-code examples (mirrors browser-harness/agent-workspace/domain-skills/)

These are **committed examples** of code the harness generated during real
sessions. The live runtime copies live in `$BH_SQL_AGENT_WORKSPACE` (default
`~/.config/sql-harness/agent-workspace/`); these repo copies are the
"historical code" you can read in-tree, the same way browser-harness ships
`agent-workspace/domain-skills/<site>/` examples.

## Contents

- `scripts/sh_demo_crud.py` — full e-commerce CRUD walkthrough against a live
  PostgreSQL. Generated via `sql-harness save sh_demo_crud`. Idempotent (drops
  + recreates the `sh_demo` schema each run). Re-run with
    `sql-harness run sh_demo_crud`.
- `agent_helpers.py` — reusable task-specific helpers auto-imported into
  the heredoc/run namespace. Empty by default (the loader tolerates a
  missing file); extend it as you discover repeated patterns.
- `skills/sh_demo-schema.md` — schema knowledge the agent captured for reuse.

## How this code was produced

```bash
# 1. Save the DSN once
sql-harness add --name test_pg --driver postgres --url 'postgres://...'

# 2. Iterate in heredoc mode until the flow works
sql-harness <<'PY'
use_workspace("test_pg")
...
PY

# 3. Persist the working heredoc as reusable code
sql-harness save sh_demo_crud <<'PY'
... the working code ...
PY

# 4. Re-run anytime (no re-typing)
sql-harness run sh_demo_crud
```

That last step — **executed code is saved and re-runnable** — is the essence
this harness inherits from browser-harness.
