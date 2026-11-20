#pragma once

#include <cstdlib>
#include <string>

// The vendored core/http.cpp reads its port and CORS origin from a Config the product
// supplies. This is the factory's, so that core/ stays untouched.
namespace Config {

// The manifest major this build speaks. A repo whose manifest asks for a different major
// is refused rather than guessed at.
inline constexpr const char* FACTORY_VERSION = "0.1";

// Ports are settable at runtime so a test can point one process at one repository without
// two of them fighting over 7777. The defaults are what a developer gets by typing nothing.
inline int envPort(const char* name, int fallback)
{
    const char* v = std::getenv(name);
    if (!v || !*v) return fallback;
    int port = std::atoi(v);
    return (port > 0 && port < 65536) ? port : fallback;
}

// One port. The socket is a path on it (/ws), not a listener of its own — see app/api/socket.h.
inline const int HTTP_PORT = envPort("FACTORY_HTTP_PORT", 7777);

inline int envInt(const char* name, int fallback)
{
    const char* v = std::getenv(name);
    if (!v || !*v) return fallback;
    const int n = std::atoi(v);
    return n > 0 ? n : fallback;
}

// How many transcript frames per lane the socket can replay from memory. SPEC.md §4.6 backfills
// the last 200 entries, so a client that reconnects inside that window resumes; outside it, it
// gets a snapshot, which is the fallback path and is tested. History on disk is E8's job.
inline const int TRANSCRIPT_LOG = envInt("FACTORY_TRANSCRIPT_LOG", 200);

// A connection that has sent nothing for this long is closed rather than left half-open. The
// client pings every 10 s (SPEC.md §6.2), so this is three missed pings. It is a liveness
// deadline, not a state timer: it produces no frames and a quiet lane still produces none.
inline const int WS_IDLE_S = envInt("FACTORY_WS_IDLE_S", 30);

inline std::string envStr(const char* name, const char* fallback)
{
    const char* v = std::getenv(name);
    return (v && *v) ? std::string(v) : std::string(fallback);
}

// Loopback, and every listener goes through core::bindEndpoint to get here. SPEC.md §5 says
// loopback only, and §1 gives that as the reason this tool has no authentication at all:
// "every one of those changes the security model, and the tool binds to loopback only".
// Widening this is not a configuration change, it is a decision to need a permission system.
inline const std::string BIND_ADDRESS = envStr("FACTORY_BIND", "127.0.0.1");

// The client's own origin, not "*".
//
// Loopback binding does not make the browser safe: a page on any site the developer visits can
// reach 127.0.0.1, and "*" tells the browser to hand that page the response. By wave 3 this API
// starts a process. What stops it is the preflight — these endpoints take JSON, which is never a
// simple request — so the origin allowed here is the whole of the defence and it is one value.
//
// In development the client is served by Vite on 7779, which proxies /api here, so the browser
// sees same-origin and never consults this. It matters for anything reaching the port directly.
inline const std::string CORS_ORIGIN = envStr("FACTORY_CORS_ORIGIN", "http://localhost:7779");

}
