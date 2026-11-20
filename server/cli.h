#pragma once

#include <string>

#include <config.h>

// This process's command line. It sits beside config.h rather than under app/ because neither
// of them is a COMPONENT: app/ holds what the engine runs, and startup is not that. Keeping it
// here leaves main.cpp as wiring — read the root, refuse without one, register, start.
namespace Cli {

// Given, never discovered. Walking up from the working directory is how a tool ends up
// serving a repository nobody chose.
inline std::string repositoryRoot(int argc, char* argv[])
{
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--repo" && i + 1 < argc) return argv[i + 1];
        if (arg.rfind("--repo=", 0) == 0) return arg.substr(7);
    }
    return Config::envStr("FACTORY_REPO", "");
}

inline constexpr const char* USAGE =
    "factory-server: no repository given.\n"
    "  usage: factory-server --repo <path>   (or set FACTORY_REPO)\n";

}
