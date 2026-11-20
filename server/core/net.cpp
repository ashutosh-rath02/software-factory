#include "net.h"

#include <config.h>
#include <glog/logging.h>

#include <boost/asio/ip/address.hpp>

boost::asio::ip::tcp::endpoint core::bindEndpoint(int port)
{
    boost::system::error_code ec;
    auto addr = boost::asio::ip::make_address(Config::BIND_ADDRESS, ec);
    if (ec) {
        LOG(FATAL) << "BIND_ADDRESS '" << Config::BIND_ADDRESS << "' is not an address: "
                   << ec.message();
    }
    LOG(INFO) << "binding " << addr.to_string() << ":" << port;
    return {addr, static_cast<unsigned short>(port)};
}
