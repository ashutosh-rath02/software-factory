#include "manager.h"

#include <app/catalog/render.h>

HTTP_HANDLER(api::Manager, Escapes)
{
    const catalog::Result result = catalog::renderEscapes();
    resp.status = result.status;
    resp.body = result.body;
}
