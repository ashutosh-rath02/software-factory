#pragma once

#include <boost/asio/ip/tcp.hpp>

namespace core {

// The one place a listener learns which interface it may accept on.
//
// Every acceptor in this server goes through this, so exposure is one decision in
// Config::BIND_ADDRESS rather than a default repeated at each call site. A listener that
// builds its own endpoint is a listener nobody reviewed — both of the ones here bound
// 0.0.0.0 while the specification this tool is built from said, in §5, "loopback only",
// and while §1 gave that sentence as the reason the tool needs no permission system at all.
//
// An address that will not parse is fatal at startup. Falling back to a wider bind than was
// asked for is precisely the failure this function exists to prevent.
boost::asio::ip::tcp::endpoint bindEndpoint(int port);

}  // namespace core
