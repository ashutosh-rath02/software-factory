#pragma once

#include <string>
#include <vector>

#include <app/catalog/document.h>
#include <app/permissions/store.h>

namespace api {

std::vector<permissions::Store::Frozen> freezeFor(const catalog::Document& ticket,
                                                  const std::string& reportedStage);

}
