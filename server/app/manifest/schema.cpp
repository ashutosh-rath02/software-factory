#include "schema.h"

namespace manifest {

namespace {

std::string escapeToken(const std::string& token)
{
    std::string out;
    for (char c : token) {
        if (c == '~') out += "~0";
        else if (c == '/') out += "~1";
        else out += c;
    }
    return out;
}

std::string child(const std::string& pointer, const std::string& token)
{
    return pointer + "/" + escapeToken(token);
}

bool typeMatches(const std::string& want, const Json& v)
{
    if (want == "object")  return v.isObject();
    if (want == "string")  return v.isString();
    if (want == "array")   return v.isArray();
    if (want == "boolean") return v.isBool();
    if (want == "integer") return v.isInteger();
    if (want == "number")  return v.isNumber();
    if (want == "null")    return v.isNull();
    return true;
}

struct Checker {
    Violation& out;
    std::vector<UnknownKey>& unknown;
    bool failed = false;

    void reject(const std::string& pointer, const std::string& reason, const std::string& message)
    {
        if (failed) return;
        failed = true;
        out.pointer = pointer;
        out.reason = reason;
        out.message = message;
    }

    void node(const Json& schema, const Json& value, const std::string& pointer)
    {
        if (failed || !schema.isObject()) return;

        if (const Json* t = schema.find("type")) {
            if (t->isString() && !typeMatches(t->str(), value)) {
                reject(pointer, "wrong_type",
                       "expected " + t->str() + " at " + (pointer.empty() ? "the document" : pointer));
                return;
            }
        }

        if (const Json* req = schema.find("required")) {
            if (req->isArray() && value.isObject()) {
                for (auto& r : req->items()) {
                    if (!r.isString()) continue;
                    if (!value.find(r.str())) {
                        reject(child(pointer, r.str()), "required",
                               "required member " + r.str() + " is missing");
                        return;
                    }
                }
            }
        }

        if (const Json* minP = schema.find("minProperties")) {
            if (minP->isNumber() && value.isObject()
                && value.members().size() < static_cast<std::size_t>(minP->number())) {
                reject(pointer, "min_properties",
                       (pointer.empty() ? std::string("the document") : pointer) + " needs at least "
                       + std::to_string(static_cast<long long>(minP->number())) + " member(s)");
                return;
            }
        }

        if (const Json* minimum = schema.find("minimum")) {
            if (minimum->isNumber() && value.isNumber() && value.number() < minimum->number()) {
                reject(pointer, "minimum",
                       pointer + " must be at least "
                       + std::to_string(static_cast<long long>(minimum->number())));
                return;
            }
        }
        if (const Json* maximum = schema.find("maximum")) {
            if (maximum->isNumber() && value.isNumber() && value.number() > maximum->number()) {
                reject(pointer, "maximum", pointer + " must be at most "
                       + std::to_string(static_cast<long long>(maximum->number())));
                return;
            }
        }
        if (const Json* minLength = schema.find("minLength")) {
            if (minLength->isNumber() && value.isString()
                && value.str().size() < static_cast<std::size_t>(minLength->number())) {
                reject(pointer, "min_length", pointer + " must be nonempty");
                return;
            }
        }

        if (value.isObject()) {
            const Json* props = schema.find("properties");
            const Json* extra = schema.find("additionalProperties");
            for (auto& member : value.members()) {
                const Json* sub = props ? props->find(member.first) : nullptr;
                if (sub) {
                    node(*sub, member.second, child(pointer, member.first));
                    if (failed) return;
                    continue;
                }
                if (extra && extra->isObject()) {
                    node(*extra, member.second, child(pointer, member.first));
                    if (failed) return;
                    continue;
                }
                if (extra && extra->isBool() && !extra->boolean()) {
                    unknown.push_back({child(pointer, member.first),
                                       "the schema does not define " + member.first
                                       + "; it was read and ignored"});
                }
            }
        }

        if (value.isArray()) {
            if (const Json* items = schema.find("items")) {
                for (std::size_t k = 0; k < value.items().size(); ++k) {
                    node(*items, value.items()[k], child(pointer, std::to_string(k)));
                    if (failed) return;
                }
            }
        }
    }
};

}

bool validate(const Json& schema, const Json& doc,
              Violation& out, std::vector<UnknownKey>& unknown)
{
    Checker c{out, unknown};
    c.node(schema, doc, "");
    return !c.failed;
}

}
