#pragma once

#include <string>
#include <vector>
#include <utility>

namespace manifest {

class Json {
public:
    enum class Type { Null, Bool, Number, String, Array, Object };

    struct Error {
        std::string message;
        std::size_t offset = 0;
        bool ok = true;
    };

    static Json parse(const std::string& text, Error& err);

    Type type() const { return type_; }
    bool isNull() const { return type_ == Type::Null; }
    bool isBool() const { return type_ == Type::Bool; }
    bool isNumber() const { return type_ == Type::Number; }
    bool isString() const { return type_ == Type::String; }
    bool isArray() const { return type_ == Type::Array; }
    bool isObject() const { return type_ == Type::Object; }
    bool isInteger() const;

    bool boolean() const { return bool_; }
    double number() const { return number_; }
    const std::string& str() const { return string_; }
    const std::vector<Json>& items() const { return items_; }
    const std::vector<std::pair<std::string, Json>>& members() const { return members_; }

    const Json* find(const std::string& key) const;
    const std::string& stringOr(const std::string& key, const std::string& fallback) const;

private:
    Type type_ = Type::Null;
    bool bool_ = false;
    double number_ = 0;
    std::string string_;
    std::vector<Json> items_;
    std::vector<std::pair<std::string, Json>> members_;

    friend class Parser;
};

std::string jsonEscape(const std::string& s);

inline std::string jstr(const std::string& s) { return "\"" + jsonEscape(s) + "\""; }

}
