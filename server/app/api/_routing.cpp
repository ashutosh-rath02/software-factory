#include "manager.h"
#include "routing.h"

HTTP_ASYNC_HANDLER(api::Manager, SubmitRouting) { Routing::submit(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, SubmitRoutingSegments) { Routing::submit(req, std::move(respond)); }
