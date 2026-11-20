#pragma once

#include <string>
#include <vector>

#include <app/catalog/render.h>
#include <app/lane/store.h>

namespace api {

void rolesOnStage(const std::string& lane, const std::string& ticketId,
                  const std::string& runId, const std::string& stage);

void wireRoles();

catalog::RolesLookup roleLookup(const lane::Store::RoleStates& states);

}
