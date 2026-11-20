cmake_minimum_required(VERSION 3.16)
project(factory-server LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(Boost REQUIRED)
find_package(OpenSSL REQUIRED COMPONENTS Crypto)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GLOG REQUIRED libglog)

# core/ is vendored — see core/VENDORED.md for what was taken and what was not.
file(GLOB_RECURSE SOURCES main.cpp core/*.cpp app/*.cpp)

add_executable(factory-server ${SOURCES})

target_compile_options(factory-server PRIVATE -Wall -Wextra)

target_include_directories(factory-server PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${GLOG_INCLUDE_DIRS}
)

# The manifest is validated against the schema file itself rather than a hand-written copy of
# it that can drift. FACTORY_SCHEMA overrides it at runtime.
target_compile_definitions(factory-server PRIVATE
    FACTORY_SCHEMA_PATH="${CMAKE_CURRENT_SOURCE_DIR}/../docs/technical/contracts/manifest.schema.json"
)

target_link_libraries(factory-server PRIVATE
    Boost::headers
    OpenSSL::Crypto
    ${GLOG_LIBRARIES}
    pthread
)
