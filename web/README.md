# sql-harness web frontend

React + Vite SPA that gets built into `src/sql_harness/_web_assets/` and shipped inside the Python wheel.

All web tooling lives here. **Do not run npm at the repo root.**

```
web/
├── package.json           ← run npm here
├── tsconfig.json
├── vite.config.ts
├── src/                   ← TS source
└── README.md              ← this file
```

After `npm run build`, the on-disk layout becomes:

```
src/sql_harness/_web_assets/    (created by Vite, served by webapp.py)
├── index.html
└── assets/
    ├── index-<hash>.js
    └── style-<hash>.css
```

## Common commands

```bash
cd web
npm ci                          # install deps
npm run dev                     # Vite dev server on :5173, proxies /api to :8765
npm run build                   # tsc --noEmit + vite build (writes to ../src/sql_harness/_web_assets/)
```

Before `npm run dev`, start the backend in another terminal:

```bash
uv run sql-harness web --port 8765 --no-open
```

## Where things live

| Thing | Path |
| --- | --- |
| React entrypoint | `src/main.tsx` |
| App shell + HashRouter | `src/App.tsx` |
| Components | `src/components/*.tsx` |
| API wrapper + types | `src/lib/api.ts`, `src/lib/types.ts` |
| State (useReducer + Context) | `src/lib/state.tsx` |
| Global CSS | `src/styles/globals.css` |

## How the backend picks up the built SPA

`src/sql_harness/webapp.py::_serve_asset` (whitelist: `index.html` + `assets/*`)
resolves assets via `importlib.resources.files("sql_harness").joinpath("_web_assets", name)`.
`pyproject.toml`'s `[tool.hatch.build.targets.wheel.force-include]` is responsible
for putting the built files into the wheel — see `references/A02-扩展方法.md` for the SOP.