#pragma once

namespace core {

class Engine;

class Component {
public:
    explicit Component() {}
    virtual ~Component() { this->release(); }

    virtual void init() {}
    virtual void start() {}
    virtual void stop() {}
    virtual void release() {}

private:
    Engine* engine_;
    friend class Engine;
};

}

#define COMPONENT(module, name) \
namespace module { \
class name : public core::Component { \
    using CLASS = module::name; \
public: \
    virtual ~name() {} \
    static name& instance() { \
        static name _instance; \
        return _instance; \
    }

#define COMPONENT_END }; }
