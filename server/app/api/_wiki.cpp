#include "manager.h"

#include <app/catalog/render.h>

HTTP_HANDLER(api::Manager, Wiki)
{
    const catalog::Result result = catalog::renderWiki();
    resp.status = result.status;
    resp.body = result.body;
}
