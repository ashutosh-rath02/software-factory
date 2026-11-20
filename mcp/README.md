# factory MCP server

A zero-dependency stdio MCP server that exposes the factory's HTTP API (`:7777`) as tools, so
Claude Code can list tickets, claim lanes, start runs and steer them without `curl`.

It is a client of the factory, not part of it: it only calls the public API and needs Node 18 or newer.

```bash
claude mcp add --scope user factory -- node /path/to/software-factory/mcp/server.mjs
```

`FACTORY_URL` overrides the default `http://127.0.0.1:7777`. The factory must be running
(`./build.sh run <repo-root>`); otherwise every tool returns "cannot reach the factory".

Tools: `factory_health`, `factory_manifest`, `factory_home`, `factory_list_requirements`,
`factory_list_tickets`, `factory_get_ticket`, `factory_ticket_report`, `factory_ticket_runs`,
`factory_allocate_ticket_id`, `factory_list_lanes`, `factory_claim_lane`, `factory_start_run`,
`factory_send_message`, `factory_interrupt_run`, `factory_end_run`, `factory_lane_diff`,
`factory_runs`, `factory_run_transcript`, `factory_metrics`.

Killing runs, releasing lanes and changing permission rules are deliberately not exposed; do those
in the UI or with `.claude/scripts/api.sh`.
