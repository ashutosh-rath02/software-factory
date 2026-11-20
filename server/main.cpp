#include <cstdio>
#include <string>

#include <cli.h>
#include <core/engine.h>
#include <core/worker.h>
#include <core/file.h>
#include <core/http.h>
#include <app/manifest/store.h>
#include <app/catalog/ids.h>
#include <app/catalog/store.h>
#include <app/lane/store.h>
#include <app/routing/store.h>
#include <app/agent/launch.h>
#include <app/agent/session.h>
#include <app/delivery/probe.h>
#include <app/land/lander.h>
#include <app/permissions/store.h>
#include <app/api/manager.h>
#include <app/api/socket.h>
#include <glog/logging.h>

int main(int argc, char* argv[])
{
    const std::string root = Cli::repositoryRoot(argc, argv);
    if (root.empty()) {
        std::fputs(Cli::USAGE, stderr);
        return 2;
    }

    google::InitGoogleLogging(argv[0]);
    FLAGS_logtostderr = 1;

    manifest::Store::instance().setRoot(root);
    manifest::Store::instance().setCarriedCredentialsDir(agent::launch::carriedConfigDir());

    auto& engine = core::Engine::instance();
    engine.registerComponent<core::Worker>();
    engine.registerComponent<file::Watch>();
    engine.registerComponent<manifest::Store>();
    engine.registerComponent<catalog::Store>();      // after the door it takes its paths from
    engine.registerComponent<catalog::Ids>();        // and after the documents it counts (E26)
    engine.registerComponent<lane::Store>();         // likewise: seats and resources are the
                                                     // manifest's, never this module's own
    engine.registerComponent<routing::Store>();
    engine.registerComponent<agent::Store>();        // runs live in lanes, so it follows one
    engine.registerComponent<delivery::Probe>();     // and so does the one that reads git
    engine.registerComponent<land::Lander>();        // and the one that writes it, on a PASS
    engine.registerComponent<permissions::Store>();  // the policy and its file are the
                                                     // manifest's repository's, so it follows too
    engine.registerComponent<http::Server>();
    engine.registerComponent<api::Socket>();
    engine.registerComponent<api::Manager>();
    engine.start();
    return 0;
}
