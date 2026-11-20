# Vendored from `server/core`

Copied at `7d72bcc` — the commit this snapshot came from. A copy whose origin is not written
down becomes a fork nobody can reconcile.

## Taken, unmodified

`clock.h` · `engine.cpp` · `worker.{h,cpp}`

`http.cpp` includes `<config.h>`, so the factory supplies its own — see `factory/server/config.h`.

## Taken and since diverged — re-apply these when re-copying

`http.{h,cpp}`, `engine.h` and the new `net.{h,cpp}`. Rows 1–2 are `SRV-FIX-020` and exist because
this product is a local tool while the origin is a networked server; rows 3–5 are `SRV-FEAT-004`
and are capability the origin's router does not have; rows 6–7 are `SRV-FIX-025` and are the only
edits any ticket has ever been permitted to make to this directory. They are listed here so a
re-copy restores them deliberately instead of silently reverting a security property.

| Change | Why the origin does not want it |
|---|---|
| listeners call `core::bindEndpoint(port)` instead of building `tcp::endpoint(tcp::v4(), …)` | the origin serves a dashboard and devices over the network, so `0.0.0.0` is correct there. Here `SPEC.md` §5 is "loopback only" and §1 gives that as the reason the tool has no authentication at all. |
| `net.{h,cpp}` — one place a listener learns its interface, fatal on an address that will not parse | the origin has no such policy to enforce; it would be a knob with one value |
| `Server::upgrade(path, handler)` — a protocol upgrade hands the socket over | the origin's WebSocket is a separate component on its own port, with its own protocol. Here `SPEC.md` §6 says "one socket, `/ws`" — a path — and one listener is one thing to secure. |
| `{id}` path parameters — a route is matched segment by segment and bindings land in `req.params` | the origin's routes are all fixed paths. Ten of `SPEC.md`'s twenty-three endpoints carry an id in the path, so string-matching prefixes in every handler was the alternative. |
| `Server::routeAsync(method, path, handler)` — a handler answers through a `Responder` instead of filling a `Response&` | the origin answers from a database it holds in memory. Here every read opens files in the repository the tool was pointed at, and `SPEC.md` §10 says the engine thread is never blocked — a synchronous handler has nowhere to put that read except the io thread. |
| `http.h` gains `Server::acceptStrand_`; `http.cpp` builds the acceptor on it in `init()`/`start()` and `stop()` **posts** the close onto it instead of closing inline (6 lines of code, `SRV-FIX-025`) | the origin **does** want this — it has the identical defect at `server/core/http.cpp:25` against `:39`, driven by the same `engine.cpp:103`. It is not fixed from here. `SRV-FIX-020` set the rule and it still holds: editing another project while fixing this one is how a change nobody scoped reaches a deployed system. This row exists so a re-copy does not silently take the race back, and so whoever re-copies knows to file it upstream instead. |
| `engine.h` gains a 37-line **threading contract** above `class Engine` — thread count, which thread each of `init()`/`start()`/`stop()` arrives on, that a `stop()`'s posted work may never run, and the strand-not-mutex rule (`SRV-FIX-025`) | comment only; `engine.cpp` is byte-identical to the origin and the behaviour described is the origin's own. It diverges only because nobody has written it down there yet. It is **description**: `tests/test_concurrency.py::test_the_threading_contract_is_in_the_header` re-derives each claim from `engine.cpp` and from a running process, so if the origin's engine ever changes, the paragraph turns a build red rather than becoming a confident lie. The origin should take this verbatim; until it does, a re-copy of `engine.h` deletes it. |

The origin has the same wide-bind behaviour and a `SERVER_IP` that is documented as "Bind address",
set in production, and read by nothing. That is a real defect **in that product** and it is not
fixed from here — it is reported to its owners as its own ticket. Editing another project while
fixing this one is how a change nobody scoped ends up in a deployed system.

**What was considered and deliberately not done to `engine.cpp`.** `forceStop_()` calls every
component's `stop()` from the signal thread, so the hazard is one level up from `http`. It was left
alone on purpose, and not out of caution: a strand belonging to `Engine` cannot fix it. Posting a
component's `stop()` onto the engine's own executor serialises it with the *other* components'
stops, not with that component's handlers — the state being raced on is the component's, so only
the component's own executor can serialise it. That is now the last sentence of the contract in
`engine.h`, because it is the thing a reader would otherwise try first.

## Written here — no origin counterpart

These four files did **not** come from `server/core` and have no counterpart there. A file in this
directory whose provenance is unrecorded is exactly the fork this document exists to prevent, so
they are listed before the vendored ones can be re-copied over them.

| file | what it is | why the origin has nothing like it |
|---|---|---|
| `queue.{h,cpp}` | `core::Queue` — `post`, and `offload(work, done)` which runs `work` on a `core::Worker` and the completion back on this queue. One strand, behind a pimpl. | The origin's `app/` holds its own strands and writes the `Worker::post` → `boost::asio::post(strand, …)` hop by hand at each call site. Here `app/` may not name `boost::asio` at all, so the executor has to be a type rather than a convention. `offload` exists because the hand-written hop is where a completion lands on the wrong executor. |
| `component.h` — **the two lines are GONE again** (`SRV-FIX-443`). It no longer declares `Queue queue_` or `Queue& queue()`, and no longer includes `core/queue.h`. | They were added because `core::Task`'s constructor took a `Component*` and nothing else, so a component was the only place a timer could find an executor. That was the defect, not the fix: the base queue is a strand **no component posts a handler onto** — every one of them keeps its state on a queue of its own — so every timer in `app/` ran beside the state it mutated, measured as 11 ThreadSanitizer races on the lane store. `core::Task` now takes a `core::Queue&`, so the executor is passed rather than found, and the base needs nothing. **This file can no longer say whether `component.h` is byte-identical to the origin: the origin is not reachable from a lane checkout, and nothing here may depend on the parent repository existing.** Whoever next re-copies `core/` should diff it and, if it matches, delete this row — the divergence it existed to record is the one this ticket removed. |
| `ws.{h,cpp}` | `ws::Connection` — Beast's `websocket::stream` behind an opaque handle, plus `ws::serve(path, queue, name, onConnection)` which owns the HTTP upgrade. Transport only: accept, read a text message, write one, close, report `lastHeard`. | The origin HAS `ws.{h,cpp}` and it is listed below as deliberately not taken — for the MESSAGE layer, which is nanopb over a `uint16_t` envelope with `token`/`userId`/`role`, Acme's web protocol. That reason never applied to the transport, which this file's own note already called "Boost.Beast and is fine". `SRV-FIX-236` split the two: the transport is here, the factory's JSON-text-frame protocol stays in `app/api/socket.cpp`. Taking the origin's file would take its message layer with it. |
| `file.{h,cpp}` | `file::Watch` — inotify wrapped in a `posix::stream_descriptor` on the engine's own `io_context`, reporting `Created`/`Modified`/`Deleted` per directory, plus `FILE_WATCH`/`FILE_HANDLER`. | The origin's state is a RocksDB it holds in memory; it has no reason to watch a disk. Here the documents in the target repository **are** the database, and `catalog` polled 752 of them every 250 ms. Queue overflow, a directory that does not exist yet and a kernel with no inotify are absorbed inside the component so no caller re-implements them. |

Neither is a candidate for pushing upstream: `Queue` encodes a layering rule that is this product's,
and `Watch` answers a question the origin does not ask. If `core/` is ever extracted into a library
both servers depend on, they go with it — they depend on nothing in `app/`.

`file.cpp` is **Linux-only**: `inotify_init1` and `boost::asio::posix::stream_descriptor`. That is
already true of this product — `lane/confine.cpp` execs `bwrap` and the tests read `/proc` — but it
is a constraint the origin does not have, and a re-copy that carried `core/` to another platform
would have to answer it with `kqueue` or FSEvents.

## Deliberately not taken

| | Why |
|---|---|
| `db.{h,cpp}`, `audit.h` | they include `db/models.h`, `db/key.h`, `db/entity.h` — the server product's data model, not framework. The factory has no database: the documents in the target repo are the state. |
| `tcp.{h,cpp}` | the device protocol. Nothing here talks to a device. |
| `ws.{h,cpp}` | **the one that surprised us**, and see the transport row above — `SRV-FIX-236` took the Beast half into a `core/ws.{h,cpp}` written here. The transport is Boost.Beast and is fine. The message layer is nanopb over a `uint16_t` big-endian type envelope on binary frames, with `token`, `userId`, `role`, `authenticated` — Acme's web protocol, not a generic socket. See the decision below. |

`EPIC-001` claimed "core already has ws, so the live session can be a real socket". Half right: Beast
is there, the message layer is not reusable. Recorded here rather than quietly worked around.

## Decision: the factory socket carries JSON text frames

An earlier version of this note claimed adapting `core/ws` "would have cost more than the wrapper".
That was not true and is corrected here, because a decision recorded with the wrong reason is one the
next person will reverse for the right one. Putting JSON in the payload and having the client strip a
two-byte envelope is *fewer* lines of C++ than the hundred-odd in `app/api/socket.cpp`.

Two things actually decided it.

**nanopb is an ESP32 constraint, and the factory has no ESP32.** `ws.h` includes `<pb.h>`,
`<pb_encode.h>` and `<pb_decode.h>`, so compiling `ws.cpp` pulls this repo's proto directory into a
tool whose entire premise is knowing nothing about the repo it is pointed at. Cloned beside another
project, that directory does not exist. Real protobuf would lift that constraint — but it is not
installed here either (`protoc 2.6.1`, proto2-only, and no `libprotobuf` headers), so adopting it
means a toolchain every machine that clones the factory has to install first.

**The thing worth protecting is the typed contract, not the encoding.** `ARCHITECTURE.md` asks that
client and server types be generated from one schema so they cannot drift; that holds with JSON on
the wire, and it keeps frames readable in devtools — which matters for a tool whose whole job is
letting you see what is happening. Protobuf is at its worst while a schema still churns, and these
messages will churn for months.

`SPEC.md` §6 names messages and payload shapes and deliberately does not pin an encoding, so this
decision contradicts nothing. It is also cheap to revisit: `app/api/socket.cpp` and the client's
`services/socket.ts` are the only two modules that touch the wire, by design. If the contract
settles and size or speed ever matters, changing it is two files.

## Reconciling later

Extracting `core/` into a library both servers depend on is the intended end state and is not
attempted while the factory lives inside this repo. Until then: re-copy from the origin commit above,
re-read this file, and do not hand-edit anything in this directory.

`SRV-FIX-025` is the one exception granted so far, and it was granted narrowly: a shutdown race that
`SRV-FIX-024` could demonstrate and could not fix from `app/`. Six lines of code. The reason the bar
is that high is in `ESCAPES.md` — an earlier attempt rewrote 207 lines of this directory and was
reverted whole, and a divergence nobody can reconcile is worse than the defect it fixed.
