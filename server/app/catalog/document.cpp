#include "document.h"

#include <cctype>
#include <cstdlib>

namespace {

std::string trim(const std::string& s)
{
    std::size_t a = 0, b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

bool isKeyChar(char c)
{
    return std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '-' || c == '.';
}

std::string stripComment(const std::string& raw)
{
    if (!raw.empty() && (raw.front() == '"' || raw.front() == '\'')) return raw;
    for (std::size_t i = 1; i < raw.size(); ++i)
        if (raw[i] == '#' && std::isspace(static_cast<unsigned char>(raw[i - 1])))
            return raw.substr(0, i);
    return raw;
}

std::string unquote(const std::string& raw)
{
    if (raw.size() >= 2 && raw.front() == raw.back() && (raw.front() == '"' || raw.front() == '\''))
        return raw.substr(1, raw.size() - 2);
    return raw;
}

}

const std::string* catalog::Document::field(const std::string& key) const
{
    for (auto& f : fields)
        if (f.first == key) return &f.second;
    return nullptr;
}

std::vector<std::string> catalog::splitList(const std::string& value)
{
    std::string v = trim(value);
    if (v.size() >= 2 && v.front() == '[' && v.back() == ']') v = v.substr(1, v.size() - 2);

    std::vector<std::string> out;
    std::string cur;
    for (char c : v) {
        if (std::isspace(static_cast<unsigned char>(c)) || c == ',') {
            if (!cur.empty()) { out.push_back(unquote(cur)); cur.clear(); }
        } else {
            cur += c;
        }
    }
    if (!cur.empty()) out.push_back(unquote(cur));
    return out;
}

std::string catalog::summarise(const std::string& body)
{
    std::string paragraph;
    std::size_t i = 0;
    while (i <= body.size()) {
        std::size_t eol = body.find('\n', i);
        std::string line = trim(body.substr(i, (eol == std::string::npos ? body.size() : eol) - i));
        if (line.empty()) {
            if (!paragraph.empty()) break;
        } else if (paragraph.empty() &&
                   (line[0] == '#' || line[0] == '>' || line.rfind("---", 0) == 0)) {
        } else {
            if (!paragraph.empty()) paragraph += " ";
            paragraph += line;
        }
        if (eol == std::string::npos) break;
        i = eol + 1;
    }
    return paragraph;
}

catalog::Document catalog::parseDocument(const std::string& path, const std::string& stem,
                                         const std::string& text)
{
    Document d;
    d.path = path;

    std::size_t bodyStart = 0;
    std::size_t firstEol = text.find('\n');
    const std::string firstLine =
        trim(text.substr(0, firstEol == std::string::npos ? text.size() : firstEol));

    if (firstLine == "---" && firstEol != std::string::npos) {
        std::size_t i = firstEol + 1, close = std::string::npos, closeEol = std::string::npos;
        while (i <= text.size()) {
            std::size_t eol = text.find('\n', i);
            std::size_t end = (eol == std::string::npos) ? text.size() : eol;
            if (trim(text.substr(i, end - i)) == "---") { close = i; closeEol = eol; break; }
            if (eol == std::string::npos) break;
            i = eol + 1;
        }

        if (close == std::string::npos) {
            d.parseError = "frontmatter opened with --- and was never closed";
            bodyStart = 0;
        } else {
            d.hasFrontmatter = true;
            const std::string front = text.substr(firstEol + 1, close - (firstEol + 1));
            int lineNo = 1;
            std::size_t p = 0;
            std::string lastKey;
            while (p <= front.size()) {
                std::size_t eol = front.find('\n', p);
                std::size_t end = (eol == std::string::npos) ? front.size() : eol;
                const std::string raw = front.substr(p, end - p);
                ++lineNo;
                const std::string line = trim(raw);

                if (!line.empty() && line[0] != '#') {
                    if (line == "-" || line.rfind("- ", 0) == 0) {
                        if (lastKey.empty()) {
                            if (d.parseError.empty())
                                d.parseError = "line " + std::to_string(lineNo) +
                                               ": list item before any key";
                        } else {
                            std::string item = unquote(trim(line.size() > 1 ? line.substr(2) : ""));
                            for (auto& f : d.fields)
                                if (f.first == lastKey) {
                                    if (!f.second.empty()) f.second += " ";
                                    f.second += item;
                                    break;
                                }
                        }
                    } else {
                        std::size_t k = 0;
                        while (k < line.size() && isKeyChar(line[k])) ++k;
                        std::size_t colon = k;
                        while (colon < line.size() &&
                               std::isspace(static_cast<unsigned char>(line[colon]))) ++colon;
                        if (k > 0 && colon < line.size() && line[colon] == ':') {
                            lastKey = line.substr(0, k);
                            d.fields.emplace_back(
                                lastKey, unquote(trim(stripComment(trim(line.substr(colon + 1))))));
                        } else if (d.parseError.empty()) {
                            d.parseError = "line " + std::to_string(lineNo) +
                                           ": not a key, a list item or a comment";
                        }
                    }
                }

                if (eol == std::string::npos) break;
                p = eol + 1;
            }
            bodyStart = (closeEol == std::string::npos) ? text.size() : closeEol + 1;
        }
    }

    d.body = text.substr(bodyStart);

    auto take = [&d](const char* key) -> std::string {
        const std::string* v = d.field(key);
        return v ? *v : std::string();
    };
    d.id = take("id");
    if (d.id.empty()) d.id = stem;
    d.title = take("title");
    if (d.title.empty()) d.title = d.id;
    d.status = take("status");
    d.parent = take("parent");
    d.workspace = take("workspace");
    if (const std::string* s = d.field("stages")) {
        d.stages = splitList(*s);
        d.hasStages = !d.stages.empty();
    }
    return d;
}

namespace {

std::vector<std::string> linesOf(const std::string& text)
{
    std::vector<std::string> out;
    std::size_t start = 0;
    while (start <= text.size()) {
        const std::size_t nl = text.find('\n', start);
        if (nl == std::string::npos) {
            out.push_back(text.substr(start));
            break;
        }
        out.push_back(text.substr(start, nl - start));
        start = nl + 1;
    }
    return out;
}

std::size_t indentOf(const std::string& line)
{
    std::size_t i = 0;
    while (i < line.size() && (line[i] == ' ' || line[i] == '\t')) ++i;
    return i;
}

std::string lstrip(const std::string& line)
{
    return line.substr(indentOf(line));
}

bool startsBlock(const std::string& line)
{
    const std::string body = lstrip(line);
    std::size_t i = 0;
    if (indentOf(line) == 0 && !body.empty() && body[0] == '#') {
        while (i < body.size() && body[i] == '#') ++i;
        if (i == 0 || i > 4) return false;
        while (i < body.size() && (body[i] == ' ' || body[i] == '\t')) ++i;
        return body.compare(i, 13, "Pass criteria") == 0;
    }
    if (body.empty() || (body[0] != '-' && body[0] != '*')) return false;
    i = 1;
    while (i < body.size() && (body[i] == ' ' || body[i] == '\t')) ++i;
    while (i < body.size() && body[i] == '*') ++i;
    return body.compare(i, 13, "Pass criteria") == 0;
}

bool endsBlock(const std::string& line)
{
    if (indentOf(line) != 0) return false;
    const std::string body = line;
    if (body.size() >= 2 && (body[0] == '-' || body[0] == '*')
        && (body[1] == ' ' || body[1] == '\t')) return true;
    if (body.empty() || body[0] != '#') return false;
    std::size_t i = 0;
    while (i < body.size() && body[i] == '#') ++i;
    return i >= 1 && i <= 4 && i < body.size() && (body[i] == ' ' || body[i] == '\t');
}

bool numbered(const std::string& line, std::size_t& indent, int& number)
{
    indent = indentOf(line);
    std::size_t i = indent;
    std::size_t digits = 0;
    while (i < line.size() && std::isdigit(static_cast<unsigned char>(line[i]))) { ++i; ++digits; }
    if (!digits || i >= line.size() || line[i] != '.') return false;
    ++i;
    if (i >= line.size() || !std::isspace(static_cast<unsigned char>(line[i]))) return false;
    number = std::atoi(line.c_str() + indent);
    return true;
}

std::string collapse(const std::string& text)
{
    std::string out;
    bool space = false;
    for (char c : text) {
        if (std::isspace(static_cast<unsigned char>(c))) { space = !out.empty(); continue; }
        if (space) { out += ' '; space = false; }
        out += c;
    }
    return out;
}

std::string firstToken(const std::string& text)
{
    const std::size_t end = text.find(' ');
    return end == std::string::npos ? text : text.substr(0, end);
}

std::string pathOf(const std::string& spelling)
{
    std::string token = firstToken(spelling);
    const std::size_t sep = token.find("::");
    if (sep != std::string::npos) token = token.substr(0, sep);
    while (!token.empty() && (token.back() == '.' || token.back() == ',' || token.back() == '`'
                              || token.back() == ')'))
        token.pop_back();
    while (!token.empty() && (token.front() == '`' || token.front() == '('))
        token.erase(token.begin());
    return token;
}

}

std::vector<catalog::EvidenceRef> catalog::evidenceRefs(const std::string& body,
                                                        const std::string& linePrefix)
{
    std::vector<EvidenceRef> out;
    if (linePrefix.empty()) return out;

    const std::vector<std::string> lines = linesOf(body);
    std::vector<std::size_t> starts;
    for (std::size_t i = 0; i < lines.size(); ++i)
        if (startsBlock(lines[i])) starts.push_back(i);

    for (std::size_t s = 0; s < starts.size(); ++s) {
        const std::size_t from = starts[s];
        std::size_t to = lines.size();
        bool fenced = false;
        for (std::size_t i = from + 1; i < lines.size(); ++i) {
            if (s + 1 < starts.size() && i == starts[s + 1]) { to = i; break; }
            if (lstrip(lines[i]).compare(0, 3, "```") == 0) { fenced = !fenced; continue; }
            if (!fenced && endsBlock(lines[i])) { to = i; break; }
        }

        std::size_t level = std::string::npos;
        for (std::size_t i = from; i < to; ++i) {
            std::size_t indent = 0;
            int number = 0;
            if (numbered(lines[i], indent, number) && indent < level) level = indent;
        }
        if (level == std::string::npos) continue;

        int current = 0;
        for (std::size_t i = from; i < to; ++i) {
            std::size_t indent = 0;
            int number = 0;
            if (numbered(lines[i], indent, number) && indent == level) current = number;
            if (!current) continue;

            const std::size_t at = lines[i].find(linePrefix);
            if (at == std::string::npos) continue;
            const std::string rest = collapse(lines[i].substr(at + linePrefix.size()));
            if (rest.empty()) continue;

            EvidenceRef ref;
            ref.criterion = current;
            ref.spelling = rest;
            ref.path = pathOf(rest);
            if (!ref.path.empty()) out.push_back(ref);
        }
    }
    return out;
}
