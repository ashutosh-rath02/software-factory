# Software Factory

A local tool you point at a repository. It reads that repository's requirements, tickets and
reports, gives each piece of work an isolated lane, runs an agent in it, and lets you watch and
steer the agent while it works.

It knows agents, lanes, exclusive resources and sessions. Everything specific to the repository
being worked on is declared by that repository in a manifest (`.factory/factory.json`).

```
software-factory/
  build.sh   builds and runs both halves
  server/    C++17: core/ (vendored framework) and app/ (manifest, catalog, lane, agent, api)
  client/    React, TypeScript and Vite: services, pages, components
  docs/      technical and product documentation
  scripts/   environment setup
  mcp/       optional MCP server so Claude Code can drive the API
```

## Requirements

Linux, or Windows through WSL2 with Ubuntu. The server reads `/proc` and the scripts are bash, so
it does not build natively on Windows or macOS.

Packages: a C++17 compiler, cmake, Boost headers, OpenSSL, glog, bubblewrap (lane sandboxing), Node 20 or newer, Python 3.
On Ubuntu, `scripts/setup-ubuntu.sh` installs all of them.

## Running it

```bash
./build.sh run          # build both halves and start both
```

Open http://localhost:7779. The client proxies `/api` and `/ws` to the server on 7777, so the
browser sees a single origin. Port 7777 on its own serves JSON.

Other commands: `./build.sh build`, `./build.sh test`, `./build.sh clean`, and
`./build.sh run <repo-root>` to point the server at a different repository. By default it works on
its own checkout. Ctrl-C stops both halves.

Running agents on tickets needs the agent CLI installed and signed in on the same machine
(`claude` or `codex`). Their credentials are read from the directory named in the manifest.

## Documentation

`docs/technical/ARCHITECTURE.md` is the module map and the direction dependencies point.
`docs/technical/MODEL.md` describes the boundary between the tool and the repositories it serves.
