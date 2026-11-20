#pragma once

#include <string>
#include <vector>

#include "json.h"

namespace manifest {

struct Violation {
    std::string pointer;
    std::string reason;
    std::string message;
};

struct UnknownKey {
    std::string pointer;
    std::string message;
};

bool validate(const Json& schema, const Json& doc,
              Violation& out, std::vector<UnknownKey>& unknown);

}
