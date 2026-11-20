#pragma once

#include <string>

namespace routing {

std::string sha256(const std::string& bytes);
std::string base64(const std::string& bytes);

}
