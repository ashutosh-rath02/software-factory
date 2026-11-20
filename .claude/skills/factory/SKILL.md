---
name: factory
description: Use the local software factory (agents in isolated lanes, driven through tickets) for a project. Use when the user says "use the factory", "run this through the factory", "make a ticket for", "start a lane", "factory status", or asks to build/fix something in a repo that has a .factory/factory.json.
---

# Drive the software factory from Claude Code

The factory is a local server (WSL/Ubuntu, `~/software-factory`) that is pointed at ONE repository
at a time. API: http://127.0.0.1:7777 · UI: http://localhost:7779 (open 7779, not 7777).

If the `factory` MCP server is connected (tools named `factory_*`, see `mcp/README.md`), use those
tools for every API call below; otherwise fall back to `curl`.

## 1. Find the target repo and check the factory

- Target repo = the current working directory's git root. It must contain `.factory/factory.json`
  and live on the Linux side (e.g. `/home/<user>/myproject`), not under `/mnt/c`.
- `curl -s http://127.0.0.1:7777/api/health` — the JSON's `repo.root` is the repo the server serves.
  - Unreachable → start it (below). Wrong `repo.root` → stop it and start it on the target repo.
  - `manifest` not `ok` → `curl -s http://127.0.0.1:7777/api/manifest`: 422 names the failing JSON pointer.

Start (from Windows):
`wsl -d Ubuntu -e bash -lc 'cd ~/software-factory && FACTORY_NO_BROWSER=1 ./build.sh run <repo-root>'`
(run in the background; wait for `/api/health`). From inside WSL, drop the `wsl` wrapper. A
different repo means restarting with that repo's root; one server serves one repo.

## 2. Work through tickets, not ad-hoc edits

1. New ticket: if the repo has the `new-ticket` skill/`/new-ticket`, use it. Ids come from the tool,
   never `ls`: `curl -s -X POST http://127.0.0.1:7777/api/tickets/ids -H 'Content-Type: application/json' -d '{"prefix":"<PREFIX>"}'`
   (prefix = the workspace's, e.g. `SRV-FIX`; an epic is `FAC-EPIC`).
2. Run it: claim a lane (`POST /api/lanes`), then start the agent (`POST /api/lanes/{lane}/runs`,
   optional body `{prompt, agent, model, effort, tree}`). Request shapes are in the factory repo's
   `docs/technical/contracts/http.schema.json`; read it rather than guessing.
3. Watch/steer: `GET /api/lanes`, `GET /api/tickets/{id}/runs`, `POST /api/lanes/{lane}/messages`,
   `POST /api/lanes/{lane}/interrupt`. Tell the user the UI link http://localhost:7779.
4. Cost of a run: `GET /api/metrics`, `GET /api/runs`.
5. Finish: QA is a separate session (`qa-ticket`). Merge only on `status: pass`.

## Rules

- Never remove a lane checkout by hand. Release through `DELETE /api/lanes/<lane>?force=true`;
  `git status` cannot tell whether an agent is running in it.
- A 404 for a ticket id usually means the server is pointed at a different repo.
- Agent credentials: the manifest's `agents.<name>.credentials_dir` must exist in WSL
  (`~/.claude` after `claude` login there).
- Tell the user what you did and give the lane and ticket ids.
