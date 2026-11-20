#include "json.h"

#include <cmath>
#include <cstdlib>
#include <sstream>

namespace manifest {

namespace {

constexpr int MAX_DEPTH = 64;
}

class Parser {
public:
    Parser(const std::string& text, Json::Error& err) : t_(text), err_(err) {}

    Json run()
    {
        Json v = value(0);
        if (!err_.ok) return Json();
        skipSpace();
        if (i_ != t_.size()) fail("trailing content after the document");
        return v;
    }

private:
    const std::string& t_;
    Json::Error& err_;
    std::size_t i_ = 0;

    void fail(const std::string& why)
    {
        if (!err_.ok) return;
        err_.ok = false;
        err_.message = why;
        err_.offset = i_;
    }

    void skipSpace()
    {
        while (i_ < t_.size()) {
            char c = t_[i_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ++i_;
            else break;
        }
    }

    bool literal(const char* word)
    {
        std::size_t n = std::string(word).size();
        if (t_.compare(i_, n, word) != 0) return false;
        i_ += n;
        return true;
    }

    Json value(int depth)
    {
        if (depth > MAX_DEPTH) { fail("nested too deeply"); return Json(); }
        skipSpace();
        if (i_ >= t_.size()) { fail("unexpected end of document"); return Json(); }

        switch (t_[i_]) {
            case '{': return object(depth);
            case '[': return array(depth);
            case '"': {
                Json v;
                v.type_ = Json::Type::String;
                v.string_ = string();
                return v;
            }
            case 't': case 'f': {
                Json v;
                v.type_ = Json::Type::Bool;
                if (literal("true")) v.bool_ = true;
                else if (literal("false")) v.bool_ = false;
                else fail("expected true or false");
                return v;
            }
            case 'n': {
                Json v;
                if (!literal("null")) fail("expected null");
                return v;
            }
            default: return numberValue();
        }
    }

    Json object(int depth)
    {
        Json v;
        v.type_ = Json::Type::Object;
        ++i_;
        skipSpace();
        if (i_ < t_.size() && t_[i_] == '}') { ++i_; return v; }
        while (true) {
            skipSpace();
            if (i_ >= t_.size() || t_[i_] != '"') { fail("expected a member name"); return v; }
            std::string key = string();
            if (!err_.ok) return v;
            skipSpace();
            if (i_ >= t_.size() || t_[i_] != ':') { fail("expected ':' after a member name"); return v; }
            ++i_;
            Json member = value(depth + 1);
            if (!err_.ok) return v;
            v.members_.emplace_back(std::move(key), std::move(member));
            skipSpace();
            if (i_ < t_.size() && t_[i_] == ',') { ++i_; continue; }
            if (i_ < t_.size() && t_[i_] == '}') { ++i_; return v; }
            fail("expected ',' or '}'");
            return v;
        }
    }

    Json array(int depth)
    {
        Json v;
        v.type_ = Json::Type::Array;
        ++i_;
        skipSpace();
        if (i_ < t_.size() && t_[i_] == ']') { ++i_; return v; }
        while (true) {
            Json item = value(depth + 1);
            if (!err_.ok) return v;
            v.items_.push_back(std::move(item));
            skipSpace();
            if (i_ < t_.size() && t_[i_] == ',') { ++i_; continue; }
            if (i_ < t_.size() && t_[i_] == ']') { ++i_; return v; }
            fail("expected ',' or ']'");
            return v;
        }
    }

    void appendUtf8(std::string& out, unsigned cp)
    {
        if (cp < 0x80) {
            out += static_cast<char>(cp);
        } else if (cp < 0x800) {
            out += static_cast<char>(0xC0 | (cp >> 6));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        }
    }

    bool hex4(unsigned& out)
    {
        if (i_ + 4 > t_.size()) return false;
        out = 0;
        for (int k = 0; k < 4; ++k) {
            char c = t_[i_ + k];
            out <<= 4;
            if (c >= '0' && c <= '9') out |= unsigned(c - '0');
            else if (c >= 'a' && c <= 'f') out |= unsigned(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') out |= unsigned(c - 'A' + 10);
            else return false;
        }
        i_ += 4;
        return true;
    }

    std::string string()
    {
        std::string out;
        ++i_;
        while (i_ < t_.size()) {
            char c = t_[i_++];
            if (c == '"') return out;
            if (c != '\\') {
                if (static_cast<unsigned char>(c) < 0x20) { fail("control character in a string"); return out; }
                out += c;
                continue;
            }
            if (i_ >= t_.size()) break;
            char e = t_[i_++];
            switch (e) {
                case '"':  out += '"';  break;
                case '\\': out += '\\'; break;
                case '/':  out += '/';  break;
                case 'b':  out += '\b'; break;
                case 'f':  out += '\f'; break;
                case 'n':  out += '\n'; break;
                case 'r':  out += '\r'; break;
                case 't':  out += '\t'; break;
                case 'u': {
                    unsigned cp = 0;
                    if (!hex4(cp)) { fail("bad \\u escape"); return out; }
                    if (cp >= 0xD800 && cp <= 0xDBFF && i_ + 1 < t_.size()
                        && t_[i_] == '\\' && t_[i_ + 1] == 'u') {
                        std::size_t save = i_;
                        i_ += 2;
                        unsigned lo = 0;
                        if (hex4(lo) && lo >= 0xDC00 && lo <= 0xDFFF)
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        else
                            i_ = save;
                    }
                    appendUtf8(out, cp);
                    break;
                }
                default: fail("unknown escape"); return out;
            }
        }
        fail("unterminated string");
        return out;
    }

    Json numberValue()
    {
        std::size_t start = i_;
        if (i_ < t_.size() && (t_[i_] == '-' || t_[i_] == '+')) ++i_;
        bool any = false;
        while (i_ < t_.size() && std::isdigit(static_cast<unsigned char>(t_[i_]))) { ++i_; any = true; }
        if (i_ < t_.size() && t_[i_] == '.') {
            ++i_;
            while (i_ < t_.size() && std::isdigit(static_cast<unsigned char>(t_[i_]))) { ++i_; any = true; }
        }
        if (any && i_ < t_.size() && (t_[i_] == 'e' || t_[i_] == 'E')) {
            ++i_;
            if (i_ < t_.size() && (t_[i_] == '-' || t_[i_] == '+')) ++i_;
            while (i_ < t_.size() && std::isdigit(static_cast<unsigned char>(t_[i_]))) ++i_;
        }
        Json v;
        if (!any) { i_ = start; fail("expected a value"); return v; }
        v.type_ = Json::Type::Number;
        v.number_ = std::strtod(t_.substr(start, i_ - start).c_str(), nullptr);
        return v;
    }
};

Json Json::parse(const std::string& text, Error& err)
{
    err = Error();
    Parser p(text, err);
    return p.run();
}

bool Json::isInteger() const
{
    if (type_ != Type::Number) return false;
    double intPart = 0;
    return std::modf(number_, &intPart) == 0.0;
}

const Json* Json::find(const std::string& key) const
{
    for (auto& m : members_)
        if (m.first == key) return &m.second;
    return nullptr;
}

const std::string& Json::stringOr(const std::string& key, const std::string& fallback) const
{
    const Json* v = find(key);
    return (v && v->isString()) ? v->str() : fallback;
}

std::string jsonEscape(const std::string& s)
{
    std::string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b";  break;
            case '\f': out += "\\f";  break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20) {
                    char buf[7];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    return out;
}

}
