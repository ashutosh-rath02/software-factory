#pragma once

#include <string>
#include <utility>
#include <vector>

namespace catalog {

struct Document {
    std::string path;
    std::string id;
    std::string title;
    std::string status;
    std::string parent;
    std::string workspace;
    std::vector<std::string> stages;
    bool hasStages = false;
    bool hasFrontmatter = false;

    std::vector<std::pair<std::string, std::string>> fields;
    std::string body;
    std::string parseError;
    long long mtime = 0;

    int repo = 0;

    const std::string* field(const std::string& key) const;
};

Document parseDocument(const std::string& path, const std::string& stem, const std::string& text);

struct EvidenceRef {
    int criterion = 0;
    std::string spelling;
    std::string path;
};

std::vector<EvidenceRef> evidenceRefs(const std::string& body, const std::string& linePrefix);

std::vector<std::string> splitList(const std::string& value);

std::string summarise(const std::string& body);

}
