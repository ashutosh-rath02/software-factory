#include "manager.h"
#include "routing.h"

HTTP_ASYNC_HANDLER(api::Manager, RoutingState) { Routing::get(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, RoutingStateSegments) { Routing::get(req, std::move(respond)); }
