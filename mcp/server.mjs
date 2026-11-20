#!/usr/bin/env node
import readline from "node:readline";

const BASE = (process.env.FACTORY_URL || "http://127.0.0.1:7777").replace(/\/$/, "");

const lanePath = (lane) => lane.split("/").map(encodeURIComponent).join("/");

async function call(method, path, body) {
  const res = await fetch(BASE + path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let parsed = text;
  try { parsed = JSON.parse(text); } catch {}
  return { status: res.status, body: parsed };
}

const str = (description) => ({ type: "string", description });
const obj = (properties, required = []) => ({ type: "object", properties, required });

const TOOLS = [
  {
    name: "factory_health",
    description: "Liveness and which repository the factory server is pointed at (repo.root). A 404 for a ticket usually means the server is pointed at a different repo.",
    inputSchema: obj({}),
    run: () => call("GET", "/api/health"),
  },
  {
    name: "factory_manifest",
    description: "The loaded manifest (workspaces, stages, agents). 422 names the JSON pointer that failed validation.",
    inputSchema: obj({}),
    run: () => call("GET", "/api/manifest"),
  },
  {
    name: "factory_home",
    description: "Overview: what needs you, what is running, unpushed work.",
    inputSchema: obj({}),
    run: () => call("GET", "/api/home"),
  },
  {
    name: "factory_list_requirements",
    description: "List requirements. filter is all|needs|running|blocked.",
    inputSchema: obj({ q: str("search text"), filter: str("all|needs|running|blocked") }),
    run: ({ q = "", filter = "all" }) =>
      call("GET", `/api/requirements?q=${encodeURIComponent(q)}&filter=${encodeURIComponent(filter)}`),
  },
  {
    name: "factory_list_tickets",
    description: "List tickets of the repository the server is pointed at.",
    inputSchema: obj({}),
    run: () => call("GET", "/api/tickets"),
  },
  {
    name: "factory_get_ticket",
    description: "One ticket document by id.",
    inputSchema: obj({ id: str("ticket id, e.g. APP-FEAT-001") }, ["id"]),
    run: ({ id }) => call("GET", `/api/tickets/${encodeURIComponent(id)}`),
  },
  {
    name: "factory_ticket_report",
    description: "The report for a ticket.",
    inputSchema: obj({ id: str("ticket id") }, ["id"]),
    run: ({ id }) => call("GET", `/api/tickets/${encodeURIComponent(id)}/report`),
  },
  {
    name: "factory_ticket_runs",
    description: "Runs (with usage) recorded for a ticket.",
    inputSchema: obj({ id: str("ticket id") }, ["id"]),
    run: ({ id }) => call("GET", `/api/tickets/${encodeURIComponent(id)}/runs`),
  },
  {
    name: "factory_allocate_ticket_id",
    description: "Allocate the next ticket id from the server. Never invent or derive an id from the directory listing. prefix is the workspace's prefix, e.g. SRV-FIX or FAC-EPIC.",
    inputSchema: obj({ prefix: str("id prefix") }, ["prefix"]),
    run: ({ prefix }) => call("POST", "/api/tickets/ids", { prefix }),
  },
  {
    name: "factory_list_lanes",
    description: "All lanes with state. Only this answers whether an agent is running in a lane; git status cannot.",
    inputSchema: obj({}),
    run: () => call("GET", "/api/lanes"),
  },
  {
    name: "factory_claim_lane",
    description: "Claim an isolated lane (a checkout) for a ticket. role is optional (e.g. qa).",
    inputSchema: obj(
      { ticket_id: str("ticket id"), workspace: str("workspace name declared in the manifest"), role: str("optional role") },
      ["ticket_id", "workspace"],
    ),
    run: ({ ticket_id, workspace, role }) =>
      call("POST", "/api/lanes", { ticket_id, workspace, ...(role ? { role } : {}) }),
  },
  {
    name: "factory_start_run",
    description: "Start the agent in a claimed lane. Optional prompt, agent, model, effort, tree (a declared preset).",
    inputSchema: obj(
      { lane: str("lane name from claim/list"), prompt: str("optional"), agent: str("optional"), model: str("optional"), effort: str("optional"), tree: str("optional") },
      ["lane"],
    ),
    run: ({ lane, ...rest }) => call("POST", `/api/lanes/${lanePath(lane)}/runs`, rest),
  },
  {
    name: "factory_send_message",
    description: "Send a steering message to the agent running in a lane.",
    inputSchema: obj({ lane: str("lane name"), text: str("the message") }, ["lane", "text"]),
    run: ({ lane, text }) => call("POST", `/api/lanes/${lanePath(lane)}/messages`, { text }),
  },
  {
    name: "factory_interrupt_run",
    description: "Interrupt the current turn of the agent in a lane.",
    inputSchema: obj({ lane: str("lane name") }, ["lane"]),
    run: ({ lane }) => call("POST", `/api/lanes/${lanePath(lane)}/interrupt`, {}),
  },
  {
    name: "factory_end_run",
    description: "End the run in a lane gracefully.",
    inputSchema: obj({ lane: str("lane name") }, ["lane"]),
    run: ({ lane }) => call("POST", `/api/lanes/${lanePath(lane)}/end`, {}),
  },
  {
    name: "factory_lane_diff",
    description: "The diff a lane has produced.",
    inputSchema: obj({ lane: str("lane name") }, ["lane"]),
    run: ({ lane }) => call("GET", `/api/lanes/${lanePath(lane)}/diff`),
  },
  {
    name: "factory_runs",
    description: "Index of all runs with token usage and cost where recorded.",
    inputSchema: obj({}),
    run: () => call("GET", "/api/runs"),
  },
  {
    name: "factory_run_transcript",
    description: "Transcript of one run.",
    inputSchema: obj({ run_id: str("run id from factory_runs") }, ["run_id"]),
    run: ({ run_id }) => call("GET", `/api/runs/${encodeURIComponent(run_id)}/transcript`),
  },
  {
    name: "factory_metrics",
    description: "Server metrics, useful for comparing cost of factory runs.",
    inputSchema: obj({}),
    run: () => call("GET", "/api/metrics"),
  },
];

const BY_NAME = new Map(TOOLS.map((t) => [t.name, t]));

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

async function handle(msg) {
  const { id, method, params } = msg;
  const isRequest = id !== undefined && id !== null;
  try {
    if (method === "initialize") {
      return send({
        jsonrpc: "2.0", id,
        result: {
          protocolVersion: params?.protocolVersion || "2025-06-18",
          capabilities: { tools: {} },
          serverInfo: { name: "factory", version: "0.1.0" },
        },
      });
    }
    if (method === "ping") return send({ jsonrpc: "2.0", id, result: {} });
    if (method === "tools/list") {
      return send({
        jsonrpc: "2.0", id,
        result: { tools: TOOLS.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })) },
      });
    }
    if (method === "tools/call") {
      const tool = BY_NAME.get(params?.name);
      if (!tool) return send({ jsonrpc: "2.0", id, error: { code: -32602, message: `unknown tool ${params?.name}` } });
      try {
        const out = await tool.run(params.arguments || {});
        const text = typeof out.body === "string" ? out.body : JSON.stringify(out.body, null, 2);
        return send({
          jsonrpc: "2.0", id,
          result: { content: [{ type: "text", text: `HTTP ${out.status}\n${text}` }], isError: out.status >= 400 },
        });
      } catch (e) {
        return send({
          jsonrpc: "2.0", id,
          result: {
            content: [{ type: "text", text: `cannot reach the factory at ${BASE}: ${e.message}. Start it with ./build.sh run <repo-root> in WSL.` }],
            isError: true,
          },
        });
      }
    }
    if (isRequest) send({ jsonrpc: "2.0", id, error: { code: -32601, message: `method not found: ${method}` } });
  } catch (e) {
    if (isRequest) send({ jsonrpc: "2.0", id, error: { code: -32603, message: String(e) } });
  }
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  handle(msg);
});
