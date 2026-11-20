#include "bytes.h"

#include <array>
#include <limits>
#include <memory>
#include <stdexcept>

#include <openssl/evp.h>

std::string routing::sha256(const std::string& bytes)
{
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context(EVP_MD_CTX_new(),
                                                                 EVP_MD_CTX_free);
    std::array<unsigned char, EVP_MAX_MD_SIZE> output {};
    unsigned int length = 0;
    if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1
        || EVP_DigestUpdate(context.get(), bytes.data(), bytes.size()) != 1
        || EVP_DigestFinal_ex(context.get(), output.data(), &length) != 1 || length != 32)
        throw std::runtime_error("SHA-256 could not be computed");
    static const char hex[] = "0123456789abcdef";
    std::string result;
    result.reserve(length * 2);
    for (unsigned int i = 0; i < length; ++i) {
        result += hex[output[i] >> 4];
        result += hex[output[i] & 15];
    }
    return result;
}

std::string routing::base64(const std::string& bytes)
{
    if (bytes.size() > static_cast<std::size_t>(std::numeric_limits<int>::max() / 4) * 3)
        throw std::runtime_error("capture is too large to encode");
    std::string result(((bytes.size() + 2) / 3) * 4 + 1, '\0');
    const int length = EVP_EncodeBlock(reinterpret_cast<unsigned char*>(result.data()),
                                       reinterpret_cast<const unsigned char*>(bytes.data()),
                                       static_cast<int>(bytes.size()));
    if (length < 0) throw std::runtime_error("capture could not be encoded");
    result.resize(static_cast<std::size_t>(length));
    return result;
}
