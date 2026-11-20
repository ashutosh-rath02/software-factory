#include "manager.h"
#include <app/manifest/store.h>

HTTP_HANDLER(api::Manager, Manifest)
{
    auto& store = manifest::Store::instance();
    resp.status = store.httpStatus();
    resp.body = (resp.status == 200) ? store.toJson() : store.errorJson();
}
