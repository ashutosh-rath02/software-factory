#pragma once

#include <functional>
#include <string>
#include <vector>

#include <app/lane/store.h>

namespace api::routingInputs {

using GitRead = std::function<lane::git::Result(const std::vector<std::string>&)>;

struct Result {
    std::string source;
    std::string error;
};

Result fingerprint(const lane::Store::Record& record, const std::string& effectiveManifest,
                   const std::string& currentManifestPath, const GitRead& readGit);

}
