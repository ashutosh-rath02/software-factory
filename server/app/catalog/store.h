#pragma once

#include <functional>
#include <map>
#include <string>
#include <vector>

#include <core/component.h>
#include <core/file.h>
#include <core/queue.h>

#include "document.h"

COMPONENT(catalog, Store)
    enum class Kind { Requirement, Ticket, Report };
    enum class Lookup { Missing, Found, Ambiguous };

    struct Roots {
        std::string epics, tickets, reports;
    };

    struct IdSurvey {
        std::vector<std::string> prefixes;
        std::map<std::string, long long> highest;
    };

    struct Change {
        Kind kind = Kind::Ticket;
        std::string id;
        std::string path;
        std::string change;
    };

    struct Listing {
        std::vector<Document> documents;
        bool directoryExists = false;
        std::string path;
    };

    static const char* kindName(Kind kind);
    static std::string seriesOf(const std::string& prefix);

    void init() override;
    void stop() override;

    using ChangeHandler = std::function<void(const Change&)>;
    void onChange(ChangeHandler handler);

    Listing requirements() const;
    Listing tickets() const;
    IdSurvey surveyIds() const;
    std::vector<std::string> idsOnEveryRef() const;
    Lookup findTicket(const std::string& id, Document& out, std::vector<std::string>& paths) const;
    bool ticketById(const std::string& id, Document& out) const;
    bool requirementById(const std::string& id, Document& out) const;

    std::string reportPathFor(const std::string& ticketId) const;
    std::string reportPathFor(const Document& ticket) const;
    bool readReport(const std::string& ticketId, Document& out) const;
    std::vector<std::string> runIds() const;
    std::string evidenceFor(const std::string& ticketId, const std::vector<std::string>& runs,
                            std::string& runIdOut) const;
    std::string evidenceFor(const Document& ticket, const std::vector<std::string>& runs,
                            std::string& runIdOut) const;

    const std::string& requirementsPath() const;
    const std::string& ticketsPath() const;
    std::vector<std::string> documentPaths() const;

    FILE_WATCH(Requirement)
    FILE_WATCH(Ticket)
    FILE_WATCH(Report)

private:
    void report_(Kind kind, const file::Change& change);
    void watch_(const std::string& dir, Kind kind, file::Watcher& watcher);

    Listing read_(const std::string& dir) const;
    Listing readAll_(std::string Roots::*which) const;
    std::string evidenceAt_(const std::string& reportPath,
                            const std::vector<std::string>& runs, std::string& runIdOut) const;
    bool insideRepository_(const std::string& path) const;
    const std::string& reportsPath() const;
    static bool splitTicketId(const std::string& id, std::string& prefix,
                              long long& number);
    static bool safeId_(const std::string& id);
    static bool readFile(const std::string& path, std::string& out);
    static long long modifiedAt(const std::string& path);
    static bool isDocument(const std::string& path);
    static std::string ticketOfReport(const std::string& stem);

    static constexpr const char* SUFFIX = ".md";
    static constexpr const char* REPORT_SUFFIX = "-report";

    std::string root_;
    std::vector<std::string> ledgers_;
    std::vector<Roots> roots_;

    core::Queue queue_;
    std::vector<ChangeHandler> handlers_;
COMPONENT_END
