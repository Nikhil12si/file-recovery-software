// =============================================================================
// engine_common.hpp
// -----------------------------------------------------------------------------
// Shared, dependency-free utilities for win_recovery_engine.exe (Track A).
//
//   * UTF-8 <-> UTF-16 conversion (portable, surrogate-aware)
//   * Minimal streaming JSON writer (RFC 8259 compliant escaping)
//   * Minimal JSON reader (used for the `recover --manifest` contract)
//   * Base64 encode/decode (resident $DATA payloads)
//   * Timestamp helpers (FILETIME -> ISO-8601, FAT date/time -> ISO-8601)
//   * Thread-safe NDJSON event sink (stdout streaming contract)
//
// Everything in this header is platform-neutral so that the parsing logic can
// be unit-tested against disk images in CI. Win32 specifics live in the .cpp
// files behind `#ifdef _WIN32`.
// =============================================================================
#pragma once

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace re {

// ---------------------------------------------------------------------------
// Version / contract identifiers
// ---------------------------------------------------------------------------
inline constexpr const char* kEngineName = "win_recovery_engine";
inline constexpr const char* kEngineVersion = "1.0.0";
inline constexpr const char* kSchemaVersion = "1.0";

// Process exit codes (documented in contracts/README.md)
enum ExitCode : int {
    kExitOk = 0,
    kExitUsage = 1,
    kExitNotElevated = 2,
    kExitUnsafeDestination = 3,
    kExitSourceError = 4,
    kExitCancelled = 5,
    kExitInternal = 10,
};

// ---------------------------------------------------------------------------
// UTF conversions (portable; invalid sequences become U+FFFD)
// ---------------------------------------------------------------------------
inline void AppendUtf8(std::string& out, uint32_t cp) {
    if (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) cp = 0xFFFD;
    if (cp < 0x80) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
}

// Decode a sequence of UTF-16 code units (host order) to UTF-8.
inline std::string Utf16UnitsToUtf8(const uint16_t* units, size_t count) {
    std::string out;
    out.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        uint32_t u = units[i];
        if (u >= 0xD800 && u <= 0xDBFF && i + 1 < count) {
            uint32_t lo = units[i + 1];
            if (lo >= 0xDC00 && lo <= 0xDFFF) {
                AppendUtf8(out, 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00));
                ++i;
                continue;
            }
        }
        AppendUtf8(out, u);
    }
    return out;
}

// Decode little-endian UTF-16 bytes (as stored on disk by NTFS / FAT LFN).
inline std::string Utf16LeBytesToUtf8(const uint8_t* bytes, size_t unitCount) {
    std::vector<uint16_t> units(unitCount);
    for (size_t i = 0; i < unitCount; ++i)
        units[i] = static_cast<uint16_t>(bytes[2 * i] | (bytes[2 * i + 1] << 8));
    return Utf16UnitsToUtf8(units.data(), units.size());
}

// Decode UTF-8 into code points (lenient; bad bytes -> U+FFFD).
inline std::vector<uint32_t> Utf8ToCodepoints(const std::string& s) {
    std::vector<uint32_t> cps;
    size_t i = 0;
    while (i < s.size()) {
        uint8_t c = static_cast<uint8_t>(s[i]);
        uint32_t cp = 0xFFFD;
        size_t len = 1;
        if (c < 0x80) { cp = c; }
        else if ((c >> 5) == 0x6 && i + 1 < s.size()) { cp = ((c & 0x1F) << 6) | (s[i + 1] & 0x3F); len = 2; }
        else if ((c >> 4) == 0xE && i + 2 < s.size()) { cp = ((c & 0x0F) << 12) | ((s[i + 1] & 0x3F) << 6) | (s[i + 2] & 0x3F); len = 3; }
        else if ((c >> 3) == 0x1E && i + 3 < s.size()) { cp = ((c & 0x07) << 18) | ((s[i + 1] & 0x3F) << 12) | ((s[i + 2] & 0x3F) << 6) | (s[i + 3] & 0x3F); len = 4; }
        cps.push_back(cp);
        i += len;
    }
    return cps;
}

// UTF-8 -> std::wstring. On Windows wchar_t is UTF-16 (surrogates emitted);
// elsewhere wchar_t is UTF-32.
inline std::wstring Utf8ToWide(const std::string& s) {
    std::wstring out;
    for (uint32_t cp : Utf8ToCodepoints(s)) {
        if (sizeof(wchar_t) == 2 && cp >= 0x10000) {
            cp -= 0x10000;
            out.push_back(static_cast<wchar_t>(0xD800 + (cp >> 10)));
            out.push_back(static_cast<wchar_t>(0xDC00 + (cp & 0x3FF)));
        } else {
            out.push_back(static_cast<wchar_t>(cp));
        }
    }
    return out;
}

inline std::string WideToUtf8(const std::wstring& w) {
    if (sizeof(wchar_t) == 2) {
        std::vector<uint16_t> units(w.begin(), w.end());
        return Utf16UnitsToUtf8(units.data(), units.size());
    }
    std::string out;
    for (wchar_t c : w) AppendUtf8(out, static_cast<uint32_t>(c));
    return out;
}

// ---------------------------------------------------------------------------
// String helpers
// ---------------------------------------------------------------------------
inline std::string ToLowerAscii(std::string s) {
    for (auto& c : s) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    return s;
}
inline std::string ToUpperAscii(std::string s) {
    for (auto& c : s) if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
    return s;
}
inline bool IEquals(const std::string& a, const std::string& b) { return ToLowerAscii(a) == ToLowerAscii(b); }
inline bool StartsWith(const std::string& s, const std::string& p) { return s.size() >= p.size() && s.compare(0, p.size(), p) == 0; }

inline std::string ExtensionOf(const std::string& name) {
    auto dot = name.find_last_of('.');
    auto slash = name.find_last_of("\\/");
    if (dot == std::string::npos || (slash != std::string::npos && dot < slash) || dot + 1 >= name.size()) return "";
    std::string ext = ToLowerAscii(name.substr(dot + 1));
    if (ext.size() > 16) return "";
    return ext;
}

inline std::string HexU64(uint64_t v, int width = 0) {
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%0*llx", width, static_cast<unsigned long long>(v));
    return buf;
}

// Replace characters that are illegal in Windows file names and neutralise
// reserved device names. Used for every path component the engine writes.
inline std::string SanitizeFileComponent(const std::string& in, const std::string& fallback = "unnamed") {
    std::string out;
    for (char ch : in) {
        unsigned char c = static_cast<unsigned char>(ch);
        if (c < 0x20 || std::strchr("<>:\"/\\|?*", ch) != nullptr) out.push_back('_');
        else out.push_back(ch);
    }
    // Strip trailing dots/spaces (invalid on Win32) and leading spaces.
    while (!out.empty() && (out.back() == '.' || out.back() == ' ')) out.pop_back();
    while (!out.empty() && out.front() == ' ') out.erase(out.begin());
    if (out.empty() || out == "." || out == "..") out = fallback;
    static const char* kReserved[] = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6",
                                      "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7",
                                      "LPT8", "LPT9"};
    std::string stem = ToUpperAscii(out.substr(0, out.find('.')));
    for (const char* r : kReserved)
        if (stem == r) { out = "_" + out; break; }
    if (out.size() > 200) {  // keep well below MAX_PATH components
        std::string ext = ExtensionOf(out);
        out = out.substr(0, 180) + (ext.empty() ? "" : "." + ext);
    }
    return out;
}

// ---------------------------------------------------------------------------
// Base64
// ---------------------------------------------------------------------------
inline std::string Base64Encode(const uint8_t* data, size_t len) {
    static const char* tbl = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((len + 2) / 3) * 4);
    for (size_t i = 0; i < len; i += 3) {
        uint32_t n = static_cast<uint32_t>(data[i]) << 16;
        if (i + 1 < len) n |= static_cast<uint32_t>(data[i + 1]) << 8;
        if (i + 2 < len) n |= data[i + 2];
        out.push_back(tbl[(n >> 18) & 63]);
        out.push_back(tbl[(n >> 12) & 63]);
        out.push_back(i + 1 < len ? tbl[(n >> 6) & 63] : '=');
        out.push_back(i + 2 < len ? tbl[n & 63] : '=');
    }
    return out;
}

inline std::vector<uint8_t> Base64Decode(const std::string& s) {
    auto val = [](char c) -> int {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    };
    std::vector<uint8_t> out;
    uint32_t buf = 0;
    int bits = 0;
    for (char c : s) {
        int v = val(c);
        if (v < 0) continue;  // skip padding / whitespace
        buf = (buf << 6) | static_cast<uint32_t>(v);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(static_cast<uint8_t>((buf >> bits) & 0xFF));
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// Time helpers
// ---------------------------------------------------------------------------
// Howard Hinnant's civil-from-days algorithm (proleptic Gregorian).
inline void CivilFromDays(int64_t z, int& y, unsigned& m, unsigned& d) {
    z += 719468;
    const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    const unsigned doe = static_cast<unsigned>(z - era * 146097);
    const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const int64_t yy = static_cast<int64_t>(yoe) + era * 400;
    const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const unsigned mp = (5 * doy + 2) / 153;
    d = doy - (153 * mp + 2) / 5 + 1;
    m = mp < 10 ? mp + 3 : mp - 9;
    y = static_cast<int>(yy + (m <= 2));
}

inline std::string UnixSecondsToIso(int64_t secs) {
    int64_t days = secs / 86400;
    int64_t rem = secs % 86400;
    if (rem < 0) { rem += 86400; --days; }
    int y; unsigned mo, d;
    CivilFromDays(days, y, mo, d);
    char buf[40];
    std::snprintf(buf, sizeof(buf), "%04d-%02u-%02uT%02d:%02d:%02dZ", y, mo, d, static_cast<int>(rem / 3600),
                  static_cast<int>((rem % 3600) / 60), static_cast<int>(rem % 60));
    return buf;
}

// NTFS FILETIME (100ns ticks since 1601-01-01 UTC) -> ISO-8601 UTC. Returns
// nullopt for zero/implausible values so the JSON emits null.
inline std::optional<std::string> FileTimeToIso(uint64_t ft) {
    if (ft == 0) return std::nullopt;
    int64_t secs = static_cast<int64_t>(ft / 10000000ULL) - 11644473600LL;
    if (secs < 0 || secs > 7258118400LL /* year 2200 */) return std::nullopt;
    return UnixSecondsToIso(secs);
}

// FAT date/time are stored in *local* time without zone; we emit them without a
// trailing 'Z' so consumers know the offset is unknown.
inline std::optional<std::string> FatDateTimeToIso(uint16_t date, uint16_t time) {
    if (date == 0) return std::nullopt;
    int year = 1980 + ((date >> 9) & 0x7F);
    int month = (date >> 5) & 0x0F;
    int day = date & 0x1F;
    int hour = (time >> 11) & 0x1F, minute = (time >> 5) & 0x3F, sec = (time & 0x1F) * 2;
    if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || sec > 59) return std::nullopt;
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d", year, month, day, hour, minute, sec);
    return std::string(buf);
}

inline std::string NowIso() {
    using namespace std::chrono;
    return UnixSecondsToIso(duration_cast<seconds>(system_clock::now().time_since_epoch()).count());
}

inline double MonotonicSeconds() {
    using namespace std::chrono;
    return duration_cast<duration<double>>(steady_clock::now().time_since_epoch()).count();
}

// ---------------------------------------------------------------------------
// Little-endian readers with bounds safety
// ---------------------------------------------------------------------------
inline uint16_t LE16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
inline uint32_t LE32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) | (static_cast<uint32_t>(p[2]) << 16) |
           (static_cast<uint32_t>(p[3]) << 24);
}
inline uint64_t LE64(const uint8_t* p) { return static_cast<uint64_t>(LE32(p)) | (static_cast<uint64_t>(LE32(p + 4)) << 32); }
inline uint16_t BE16(const uint8_t* p) { return static_cast<uint16_t>((p[0] << 8) | p[1]); }
inline uint32_t BE32(const uint8_t* p) {
    return (static_cast<uint32_t>(p[0]) << 24) | (static_cast<uint32_t>(p[1]) << 16) | (static_cast<uint32_t>(p[2]) << 8) | p[3];
}
inline uint64_t BE64(const uint8_t* p) { return (static_cast<uint64_t>(BE32(p)) << 32) | BE32(p + 4); }

// ---------------------------------------------------------------------------
// JSON writer
// ---------------------------------------------------------------------------
class JsonWriter {
public:
    explicit JsonWriter(bool pretty = false) : pretty_(pretty) {}

    JsonWriter& BeginObject() { Prefix(); out_.push_back('{'); stack_.push_back(true); return *this; }
    JsonWriter& EndObject() { Close('}'); return *this; }
    JsonWriter& BeginArray() { Prefix(); out_.push_back('['); stack_.push_back(true); return *this; }
    JsonWriter& EndArray() { Close(']'); return *this; }

    JsonWriter& Key(const std::string& k) {
        Prefix();
        AppendString(k);
        out_.push_back(':');
        if (pretty_) out_.push_back(' ');
        afterKey_ = true;
        return *this;
    }

    JsonWriter& String(const std::string& v) { Prefix(); AppendString(v); return *this; }
    JsonWriter& Int(int64_t v) { Prefix(); out_ += std::to_string(v); return *this; }
    JsonWriter& UInt(uint64_t v) { Prefix(); out_ += std::to_string(v); return *this; }
    JsonWriter& Double(double v, int precision = 4) {
        Prefix();
        if (!std::isfinite(v)) { out_ += "null"; return *this; }
        char buf[64];
        std::snprintf(buf, sizeof(buf), "%.*f", precision, v);
        out_ += buf;
        return *this;
    }
    JsonWriter& Bool(bool v) { Prefix(); out_ += v ? "true" : "false"; return *this; }
    JsonWriter& Null() { Prefix(); out_ += "null"; return *this; }
    JsonWriter& OptString(const std::optional<std::string>& v) { return v ? String(*v) : Null(); }
    // Splice an already-serialised JSON fragment.
    JsonWriter& Raw(const std::string& json) { Prefix(); out_ += json; return *this; }

    // Convenience key/value setters.
    JsonWriter& KV(const std::string& k, const std::string& v) { return Key(k).String(v); }
    JsonWriter& KV(const std::string& k, const char* v) { return Key(k).String(v); }
    JsonWriter& KV(const std::string& k, bool v) { return Key(k).Bool(v); }
    JsonWriter& KVi(const std::string& k, int64_t v) { return Key(k).Int(v); }
    JsonWriter& KVu(const std::string& k, uint64_t v) { return Key(k).UInt(v); }
    JsonWriter& KVd(const std::string& k, double v, int precision = 4) { return Key(k).Double(v, precision); }
    JsonWriter& KVopt(const std::string& k, const std::optional<std::string>& v) { return Key(k).OptString(v); }
    JsonWriter& KVnullable(const std::string& k, const std::string& v) { return v.empty() ? Key(k).Null() : KV(k, v); }

    const std::string& str() const { return out_; }
    std::string take() { return std::move(out_); }

private:
    void Prefix() {
        if (afterKey_) { afterKey_ = false; return; }
        if (!stack_.empty()) {
            if (!stack_.back()) out_.push_back(',');
            stack_.back() = false;
            Newline();
        }
    }
    void Close(char c) {
        bool empty = stack_.back();
        stack_.pop_back();
        if (!empty) Newline();
        out_.push_back(c);
    }
    void Newline() {
        if (!pretty_) return;
        out_.push_back('\n');
        out_.append(stack_.size() * 2, ' ');
    }
    void AppendString(const std::string& s) {
        out_.push_back('"');
        // Re-encode through code points so that invalid UTF-8 never reaches
        // the consumer (Python's json module rejects it).
        for (uint32_t cp : Utf8ToCodepoints(s)) {
            switch (cp) {
                case '"': out_ += "\\\""; break;
                case '\\': out_ += "\\\\"; break;
                case '\b': out_ += "\\b"; break;
                case '\f': out_ += "\\f"; break;
                case '\n': out_ += "\\n"; break;
                case '\r': out_ += "\\r"; break;
                case '\t': out_ += "\\t"; break;
                default:
                    if (cp < 0x20) {
                        char buf[8];
                        std::snprintf(buf, sizeof(buf), "\\u%04x", cp);
                        out_ += buf;
                    } else {
                        AppendUtf8(out_, cp);
                    }
            }
        }
        out_.push_back('"');
    }

    std::string out_;
    std::vector<bool> stack_;  // true == container still empty
    bool afterKey_ = false;
    bool pretty_ = false;
};

// ---------------------------------------------------------------------------
// Minimal JSON reader (objects, arrays, strings, numbers, bools, null)
// ---------------------------------------------------------------------------
struct JsonValue {
    enum class Type { Null, Bool, Number, String, Array, Object } type = Type::Null;
    bool b = false;
    std::string text;  // string payload or raw number text (keeps uint64 precision)
    std::vector<JsonValue> arr;
    // Object members are kept as two parallel vectors: std::vector of an
    // incomplete type is allowed (C++17), std::pair of one is not.
    std::vector<std::string> keys;
    std::vector<JsonValue> vals;

    bool isNull() const { return type == Type::Null; }
    const JsonValue* get(const std::string& k) const {
        if (type != Type::Object) return nullptr;
        for (size_t i = 0; i < keys.size(); ++i) if (keys[i] == k) return &vals[i];
        return nullptr;
    }
    std::string str(const std::string& k, const std::string& def = "") const {
        auto* v = get(k);
        return (v && v->type == Type::String) ? v->text : def;
    }
    uint64_t u64(const std::string& k, uint64_t def = 0) const {
        auto* v = get(k);
        return (v && v->type == Type::Number) ? std::strtoull(v->text.c_str(), nullptr, 10) : def;
    }
    bool boolean(const std::string& k, bool def = false) const {
        auto* v = get(k);
        return (v && v->type == Type::Bool) ? v->b : def;
    }
};

class JsonParser {
public:
    explicit JsonParser(const std::string& s) : s_(s) {}
    JsonValue Parse() {
        JsonValue v = ParseValue();
        SkipWs();
        if (i_ != s_.size()) Fail("trailing characters");
        return v;
    }

private:
    [[noreturn]] void Fail(const std::string& why) {
        throw std::runtime_error("JSON parse error at offset " + std::to_string(i_) + ": " + why);
    }
    void SkipWs() { while (i_ < s_.size() && std::strchr(" \t\r\n", s_[i_]) && s_[i_] != '\0') ++i_; }
    JsonValue ParseValue() {
        if (++depth_ > 256) Fail("nesting too deep");
        SkipWs();
        if (i_ >= s_.size()) Fail("unexpected end");
        JsonValue v;
        char c = s_[i_];
        if (c == '{') {
            v.type = JsonValue::Type::Object;
            ++i_;
            SkipWs();
            if (i_ < s_.size() && s_[i_] == '}') { ++i_; --depth_; return v; }
            while (true) {
                SkipWs();
                if (i_ >= s_.size() || s_[i_] != '"') Fail("expected key");
                std::string k = ParseString();
                SkipWs();
                if (i_ >= s_.size() || s_[i_] != ':') Fail("expected ':'");
                ++i_;
                JsonValue member = ParseValue();
                v.keys.push_back(std::move(k));
                v.vals.push_back(std::move(member));
                SkipWs();
                if (i_ < s_.size() && s_[i_] == ',') { ++i_; continue; }
                if (i_ < s_.size() && s_[i_] == '}') { ++i_; break; }
                Fail("expected ',' or '}'");
            }
        } else if (c == '[') {
            v.type = JsonValue::Type::Array;
            ++i_;
            SkipWs();
            if (i_ < s_.size() && s_[i_] == ']') { ++i_; --depth_; return v; }
            while (true) {
                v.arr.push_back(ParseValue());
                SkipWs();
                if (i_ < s_.size() && s_[i_] == ',') { ++i_; continue; }
                if (i_ < s_.size() && s_[i_] == ']') { ++i_; break; }
                Fail("expected ',' or ']'");
            }
        } else if (c == '"') {
            v.type = JsonValue::Type::String;
            v.text = ParseString();
        } else if (s_.compare(i_, 4, "true") == 0) { v.type = JsonValue::Type::Bool; v.b = true; i_ += 4; }
        else if (s_.compare(i_, 5, "false") == 0) { v.type = JsonValue::Type::Bool; i_ += 5; }
        else if (s_.compare(i_, 4, "null") == 0) { i_ += 4; }
        else if (c == '-' || (c >= '0' && c <= '9')) {
            size_t start = i_;
            while (i_ < s_.size() && std::strchr("+-0123456789.eE", s_[i_]) && s_[i_] != '\0') ++i_;
            v.type = JsonValue::Type::Number;
            v.text = s_.substr(start, i_ - start);
        } else {
            Fail("unexpected character");
        }
        --depth_;
        return v;
    }
    std::string ParseString() {
        ++i_;  // opening quote
        std::string out;
        while (i_ < s_.size()) {
            char c = s_[i_++];
            if (c == '"') return out;
            if (c != '\\') { out.push_back(c); continue; }
            if (i_ >= s_.size()) break;
            char e = s_[i_++];
            switch (e) {
                case '"': out.push_back('"'); break;
                case '\\': out.push_back('\\'); break;
                case '/': out.push_back('/'); break;
                case 'b': out.push_back('\b'); break;
                case 'f': out.push_back('\f'); break;
                case 'n': out.push_back('\n'); break;
                case 'r': out.push_back('\r'); break;
                case 't': out.push_back('\t'); break;
                case 'u': {
                    auto hex4 = [&]() -> uint32_t {
                        if (i_ + 4 > s_.size()) Fail("bad \\u escape");
                        uint32_t v = static_cast<uint32_t>(std::strtoul(s_.substr(i_, 4).c_str(), nullptr, 16));
                        i_ += 4;
                        return v;
                    };
                    uint32_t cp = hex4();
                    if (cp >= 0xD800 && cp <= 0xDBFF && i_ + 6 <= s_.size() && s_[i_] == '\\' && s_[i_ + 1] == 'u') {
                        i_ += 2;
                        uint32_t lo = hex4();
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    AppendUtf8(out, cp);
                    break;
                }
                default: Fail("bad escape");
            }
        }
        Fail("unterminated string");
    }
    const std::string& s_;
    size_t i_ = 0;
    int depth_ = 0;
};

// ---------------------------------------------------------------------------
// NDJSON event sink -> stdout (the streaming half of the CLI contract)
// ---------------------------------------------------------------------------
class EventSink {
public:
    static EventSink& Instance() {
        static EventSink s;
        return s;
    }
    void Emit(const std::string& jsonLine) {
        std::lock_guard<std::mutex> lk(mu_);
        std::fwrite(jsonLine.data(), 1, jsonLine.size(), stdout);
        std::fputc('\n', stdout);
        std::fflush(stdout);
    }
    void Log(const std::string& level, const std::string& message, const std::string& sourceId = "") {
        JsonWriter w;
        w.BeginObject().KV("event", "log").KV("ts", NowIso()).KV("level", level).KV("message", message);
        if (!sourceId.empty()) w.KV("source_id", sourceId);
        w.EndObject();
        Emit(w.str());
    }
    void Error(const std::string& code, const std::string& message, bool fatal) {
        JsonWriter w;
        w.BeginObject().KV("event", "error").KV("ts", NowIso()).KV("code", code).KV("message", message).KV("fatal", fatal).EndObject();
        Emit(w.str());
    }

private:
    std::mutex mu_;
};

inline void LogInfo(const std::string& m, const std::string& src = "") { EventSink::Instance().Log("info", m, src); }
inline void LogWarn(const std::string& m, const std::string& src = "") { EventSink::Instance().Log("warning", m, src); }
inline void LogError(const std::string& m, const std::string& src = "") { EventSink::Instance().Log("error", m, src); }

}  // namespace re
