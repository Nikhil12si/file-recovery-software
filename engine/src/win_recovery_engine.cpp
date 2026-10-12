// =============================================================================
// win_recovery_engine.cpp  —  MODULE 3: Dual Scan Recovery Core
// -----------------------------------------------------------------------------
// Sections
//   1. Cancellation, aligned buffers, raw device I/O (Win32 CreateFileW +
//      FILE_FLAG_NO_BUFFERING, VirtualAlloc buffers, bad-sector salvage)
//   2. Output helpers (directories, files, long paths)
//   3. Partition table (MBR/EBR/GPT) and boot-sector file-system detection
//   4. Scan context (progress streaming, statistics, cancellation, limits)
//   5. Signature registry + structure-aware carving parsers
//   6. NTFS Quick Scan ($MFT parsing, fixups, data runs, $ATTRIBUTE_LIST,
//      $Bitmap overwrite analysis, path reconstruction, folder filter)
//   7. FAT32 Quick Scan (deleted 0xE5 entries, LFN reconstruction)
//   8. Deep Scan (sector-by-sector signature carving)
//   9. candidates.json export (streamed, atomic rename)
//  10. Scan orchestration (All Drives / Single Drive / Single Folder)
//  11. Recovery (manifest-driven extraction to a guarded destination)
// =============================================================================
#include "win_recovery_engine.hpp"

#include <array>
#include <cctype>
#include <deque>
#include <functional>
#include <unordered_map>
#include <unordered_set>

#include "win_security_guard.hpp"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <winioctl.h>
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#endif

namespace re {

// =============================================================================
// 1. Cancellation, aligned buffers, raw device I/O
// =============================================================================
namespace {
std::atomic<bool> g_cancel{false};
}
void RequestCancel() { g_cancel.store(true); }
bool CancelRequested() { return g_cancel.load(); }

AlignedBuffer::AlignedBuffer(size_t size) : size_(size) {
#ifdef _WIN32
    // VirtualAlloc returns page-aligned (4 KiB) zeroed memory, which satisfies
    // the FILE_FLAG_NO_BUFFERING alignment rule for every sector size <= 4 KiB.
    ptr_ = static_cast<uint8_t*>(VirtualAlloc(nullptr, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
#else
    void* p = nullptr;
    if (posix_memalign(&p, 4096, size) != 0) p = nullptr;
    ptr_ = static_cast<uint8_t*>(p);
    if (ptr_) std::memset(ptr_, 0, size);
#endif
    if (!ptr_) throw std::bad_alloc();
}

AlignedBuffer::~AlignedBuffer() {
#ifdef _WIN32
    if (ptr_) VirtualFree(ptr_, 0, MEM_RELEASE);
#else
    std::free(ptr_);
#endif
}

RawDevice::RawDevice() = default;
RawDevice::~RawDevice() { Close(); }

#ifdef _WIN32
namespace {
std::string Win32ErrorText(DWORD code) {
    wchar_t* msg = nullptr;
    DWORD n = FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                             nullptr, code, 0, reinterpret_cast<LPWSTR>(&msg), 0, nullptr);
    std::string s = "Win32 error " + std::to_string(code);
    if (n && msg) {
        std::wstring w(msg, n);
        while (!w.empty() && (w.back() == L'\r' || w.back() == L'\n' || w.back() == L' ')) w.pop_back();
        s += " (" + WideToUtf8(w) + ")";
    }
    if (msg) LocalFree(msg);
    return s;
}
}  // namespace

bool RawDevice::Open(const std::string& path, SourceKind kind, std::string* error) {
    Close();
    path_ = path;
    std::wstring wpath = Utf8ToWide(path);
    // Raw devices: bypass the cache entirely (FILE_FLAG_NO_BUFFERING) so reads
    // reflect what is physically on the platter, and request write-through
    // semantics as mandated (the handle is GENERIC_READ only, so nothing is
    // ever written to the source).
    DWORD flags = (kind == SourceKind::Image) ? FILE_FLAG_SEQUENTIAL_SCAN : (FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH);
    HANDLE h = CreateFileW(wpath.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, flags,
                           nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        DWORD e = GetLastError();
        if (error) {
            *error = "CreateFileW(" + path + ") failed: " + Win32ErrorText(e);
            if (e == ERROR_ACCESS_DENIED) *error += ". Raw disk access requires running the engine as Administrator.";
        }
        return false;
    }
    handle_ = h;

    DWORD ret = 0;
    if (kind == SourceKind::Image) {
        LARGE_INTEGER sz{};
        GetFileSizeEx(h, &sz);
        size_ = static_cast<uint64_t>(sz.QuadPart);
        sectorSize_ = 512;
    } else {
        if (kind == SourceKind::Volume) {
            // Allow reads past the file-system-reported end (e.g. backup boot sector).
            DeviceIoControl(h, FSCTL_ALLOW_EXTENDED_DASD_IO, nullptr, 0, nullptr, 0, &ret, nullptr);
        }
        std::vector<BYTE> gbuf(sizeof(DISK_GEOMETRY_EX) + 512);
        if (DeviceIoControl(h, IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, nullptr, 0, gbuf.data(), static_cast<DWORD>(gbuf.size()),
                            &ret, nullptr)) {
            auto* g = reinterpret_cast<DISK_GEOMETRY_EX*>(gbuf.data());
            if (g->Geometry.BytesPerSector) sectorSize_ = g->Geometry.BytesPerSector;
            size_ = static_cast<uint64_t>(g->DiskSize.QuadPart);
        } else {
            DISK_GEOMETRY dg{};
            if (DeviceIoControl(h, IOCTL_DISK_GET_DRIVE_GEOMETRY, nullptr, 0, &dg, sizeof(dg), &ret, nullptr) &&
                dg.BytesPerSector)
                sectorSize_ = dg.BytesPerSector;
        }
        GET_LENGTH_INFORMATION li{};
        if (DeviceIoControl(h, IOCTL_DISK_GET_LENGTH_INFO, nullptr, 0, &li, sizeof(li), &ret, nullptr))
            size_ = static_cast<uint64_t>(li.Length.QuadPart);
    }
    if (sectorSize_ < 512 || (sectorSize_ & (sectorSize_ - 1)) != 0) sectorSize_ = 512;
    if (size_ == 0) {
        if (error) *error = "Could not determine the size of " + path;
        Close();
        return false;
    }
    bounce_ = std::make_unique<AlignedBuffer>(1u << 20);
    return true;
}

void RawDevice::Close() {
    if (handle_ && handle_ != INVALID_HANDLE_VALUE) CloseHandle(static_cast<HANDLE>(handle_));
    handle_ = nullptr;
}

bool RawDevice::IsOpen() const { return handle_ != nullptr; }

bool RawDevice::RawRead(uint64_t offset, size_t len, uint8_t* out, size_t* got) {
    OVERLAPPED ov{};
    ov.Offset = static_cast<DWORD>(offset & 0xFFFFFFFFULL);
    ov.OffsetHigh = static_cast<DWORD>(offset >> 32);
    DWORD n = 0;
    // Synchronous handle + OVERLAPPED offset = positioned read without a
    // separate SetFilePointerEx call (thread-safe positioning).
    BOOL ok = ReadFile(static_cast<HANDLE>(handle_), out, static_cast<DWORD>(len), &n, &ov);
    *got = n;
    if (!ok) {
        DWORD e = GetLastError();
        if (e == ERROR_HANDLE_EOF) return true;
        return false;
    }
    return true;
}

#else  // POSIX (image files only — test harness)
bool RawDevice::Open(const std::string& path, SourceKind, std::string* error) {
    Close();
    path_ = path;
    fd_ = ::open(path.c_str(), O_RDONLY);
    if (fd_ < 0) {
        if (error) *error = "open(" + path + ") failed: " + std::strerror(errno);
        return false;
    }
    struct stat st{};
    fstat(fd_, &st);
    size_ = static_cast<uint64_t>(st.st_size);
    sectorSize_ = 512;
    if (size_ == 0) {
        if (error) *error = "Empty image: " + path;
        Close();
        return false;
    }
    bounce_ = std::make_unique<AlignedBuffer>(1u << 20);
    return true;
}
void RawDevice::Close() {
    if (fd_ >= 0) ::close(fd_);
    fd_ = -1;
}
bool RawDevice::IsOpen() const { return fd_ >= 0; }
bool RawDevice::RawRead(uint64_t offset, size_t len, uint8_t* out, size_t* got) {
    size_t total = 0;
    while (total < len) {
        ssize_t n = ::pread(fd_, out + total, len - total, static_cast<off_t>(offset + total));
        if (n < 0) { *got = total; return false; }
        if (n == 0) break;
        total += static_cast<size_t>(n);
    }
    *got = total;
    return true;
}
#endif

size_t RawDevice::ReadAligned(uint64_t offset, size_t len, uint8_t* alignedOut) {
    if (!IsOpen() || offset >= size_) return 0;
    size_t want = static_cast<size_t>(std::min<uint64_t>(len, size_ - offset));
    size_t got = 0;
    if (RawRead(offset, want, alignedOut, &got) && got == want) return got;

    // Bulk read failed (bad sector / CRC error). Salvage sector by sector,
    // zero-filling unreadable sectors so offsets stay consistent.
    size_t done = 0;
    while (done < want) {
        size_t chunk = std::min<size_t>(sectorSize_, want - done);
        size_t g = 0;
        if (!RawRead(offset + done, chunk, alignedOut + done, &g) || g < chunk) {
            std::memset(alignedOut + done + g, 0, chunk - g);
            ++readErrors_;
        }
        done += chunk;
    }
    return want;
}

size_t RawDevice::Read(uint64_t offset, size_t len, uint8_t* out) {
    if (!IsOpen() || len == 0) return 0;
    const uint64_t ss = sectorSize_;
    size_t total = 0;
    while (total < len) {
        uint64_t cur = offset + total;
        if (cur >= size_) break;
        uint64_t alignedStart = cur - (cur % ss);
        size_t lead = static_cast<size_t>(cur - alignedStart);
        uint64_t need = lead + (len - total);
        need = ((need + ss - 1) / ss) * ss;
        size_t want = static_cast<size_t>(std::min<uint64_t>(need, bounce_->size()));
        size_t got = ReadAligned(alignedStart, want, bounce_->data());
        if (got <= lead) break;
        size_t usable = std::min(got - lead, len - total);
        std::memcpy(out + total, bounce_->data() + lead, usable);
        total += usable;
    }
    return total;
}

// =============================================================================
// 2. Output helpers
// =============================================================================
namespace {

bool FileExists(const std::string& path) {
    if (path.empty()) return false;
#ifdef _WIN32
    return GetFileAttributesW(Utf8ToWide(path).c_str()) != INVALID_FILE_ATTRIBUTES;
#else
    struct stat st{};
    return ::stat(path.c_str(), &st) == 0;
#endif
}

char PathSep() {
#ifdef _WIN32
    return '\\';
#else
    return '/';
#endif
}

std::string JoinPath(const std::string& a, const std::string& b) {
    if (a.empty()) return b;
    char last = a.back();
    if (last == '\\' || last == '/') return a + b;
    return a + PathSep() + b;
}

#ifdef _WIN32
// Prefix with \\?\ so paths longer than MAX_PATH work.
std::wstring LongPath(const std::string& p) {
    std::wstring w = Utf8ToWide(p);
    for (auto& c : w) if (c == L'/') c = L'\\';
    if (w.size() >= 240 && w.rfind(L"\\\\?\\", 0) != 0) {
        if (w.rfind(L"\\\\", 0) == 0) return L"\\\\?\\UNC\\" + w.substr(2);
        return L"\\\\?\\" + w;
    }
    return w;
}
#endif

bool MakeDirs(const std::string& path) {
    if (path.empty()) return false;
    std::string acc;
    size_t i = 0;
#ifdef _WIN32
    // Keep drive root ("E:\") or UNC prefix intact.
    if (path.size() >= 2 && path[1] == ':') { acc = path.substr(0, 2); i = 2; }
    else if (StartsWith(path, "\\\\")) {
        size_t p = path.find('\\', 2);
        p = (p == std::string::npos) ? path.size() : path.find('\\', p + 1);
        if (p == std::string::npos) p = path.size();
        acc = path.substr(0, p);
        i = p;
    }
#endif
    while (i <= path.size()) {
        size_t j = path.find_first_of("\\/", i);
        if (j == std::string::npos) j = path.size();
        std::string comp = path.substr(i, j - i);
        if (!comp.empty()) {
            acc = acc.empty() ? comp : (acc == "/" ? "/" + comp : acc + PathSep() + comp);
#ifdef _WIN32
            if (acc.size() > 2 || acc.back() != ':') {
                if (!CreateDirectoryW(LongPath(acc).c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS) return false;
            }
#else
            if (::mkdir(acc.c_str(), 0755) != 0 && errno != EEXIST) return false;
#endif
        } else if (i == 0 && j == 0) {
            acc = "/";  // absolute POSIX path
        }
        i = j + 1;
    }
    return true;
}

// Minimal exclusive-create output file wrapper.
class OutFile {
public:
    ~OutFile() { Close(); }
    bool CreateNew(const std::string& path) {
#ifdef _WIN32
        h_ = CreateFileW(LongPath(path).c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        return h_ != INVALID_HANDLE_VALUE;
#else
        f_ = std::fopen(path.c_str(), "wbx");
        return f_ != nullptr;
#endif
    }
    bool CreateAlways(const std::string& path) {
#ifdef _WIN32
        h_ = CreateFileW(LongPath(path).c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        return h_ != INVALID_HANDLE_VALUE;
#else
        f_ = std::fopen(path.c_str(), "wb");
        return f_ != nullptr;
#endif
    }
    bool Write(const void* data, size_t len) {
        if (len == 0) return true;
#ifdef _WIN32
        const uint8_t* p = static_cast<const uint8_t*>(data);
        while (len > 0) {
            DWORD chunk = static_cast<DWORD>(std::min<size_t>(len, 64u << 20));
            DWORD n = 0;
            if (!WriteFile(h_, p, chunk, &n, nullptr) || n != chunk) return false;
            p += chunk;
            len -= chunk;
        }
        return true;
#else
        return std::fwrite(data, 1, len, f_) == len;
#endif
    }
    bool Write(const std::string& s) { return Write(s.data(), s.size()); }
    void Close() {
#ifdef _WIN32
        if (h_ != INVALID_HANDLE_VALUE) CloseHandle(h_);
        h_ = INVALID_HANDLE_VALUE;
#else
        if (f_) std::fclose(f_);
        f_ = nullptr;
#endif
    }

private:
#ifdef _WIN32
    HANDLE h_ = INVALID_HANDLE_VALUE;
#else
    FILE* f_ = nullptr;
#endif
};

bool AtomicReplace(const std::string& from, const std::string& to) {
#ifdef _WIN32
    return MoveFileExW(LongPath(from).c_str(), LongPath(to).c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
#else
    return std::rename(from.c_str(), to.c_str()) == 0;
#endif
}

std::string ReadWholeFile(const std::string& path) {
#ifdef _WIN32
    FILE* f = _wfopen(LongPath(path).c_str(), L"rb");
#else
    FILE* f = std::fopen(path.c_str(), "rb");
#endif
    if (!f) throw std::runtime_error("Cannot open " + path);
    std::string data;
    char buf[65536];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) data.append(buf, n);
    std::fclose(f);
    return data;
}

// =============================================================================
// 3. Partition table + file-system detection
// =============================================================================
enum class FsType { Unknown, NTFS, FAT32, FAT16, ExFAT };

const char* FsName(FsType t) {
    switch (t) {
        case FsType::NTFS: return "NTFS";
        case FsType::FAT32: return "FAT32";
        case FsType::FAT16: return "FAT16";
        case FsType::ExFAT: return "exFAT";
        default: return "Unknown";
    }
}

FsType DetectFs(const uint8_t* bs) {
    if (std::memcmp(bs + 3, "NTFS    ", 8) == 0) return FsType::NTFS;
    if (std::memcmp(bs + 3, "EXFAT   ", 8) == 0) return FsType::ExFAT;
    if (bs[510] != 0x55 || bs[511] != 0xAA) return FsType::Unknown;
    uint16_t bps = LE16(bs + 0x0B);
    bool bpsOk = bps == 512 || bps == 1024 || bps == 2048 || bps == 4096;
    if (!bpsOk || bs[0x0D] == 0) return FsType::Unknown;
    if (std::memcmp(bs + 0x52, "FAT32   ", 8) == 0) return FsType::FAT32;
    if (LE16(bs + 0x16) == 0 && LE32(bs + 0x24) != 0 && LE16(bs + 0x11) == 0) return FsType::FAT32;
    if (std::memcmp(bs + 0x36, "FAT1", 4) == 0) return FsType::FAT16;
    return FsType::Unknown;
}

struct FoundPartition {
    int number = 0;
    uint64_t offset = 0;
    uint64_t length = 0;
    std::string label;
};

// Parses MBR (incl. extended/EBR chains) and GPT. Used for disk images and
// physical drives whose partitions are not mounted.
std::vector<FoundPartition> ParsePartitionTable(RawDevice& dev) {
    std::vector<FoundPartition> parts;
    std::vector<uint8_t> s0(512);
    if (dev.Read(0, 512, s0.data()) < 512 || s0[510] != 0x55 || s0[511] != 0xAA) return parts;

    bool gpt = false;
    int number = 0;
    for (int i = 0; i < 4; ++i) {
        const uint8_t* e = s0.data() + 446 + i * 16;
        uint8_t type = e[4];
        uint64_t start = LE32(e + 8), count = LE32(e + 12);
        if (type == 0) continue;
        if (type == 0xEE) { gpt = true; break; }
        ++number;
        if (type == 0x05 || type == 0x0F || type == 0x85) {
            // Walk the EBR chain of logical partitions.
            uint64_t extBase = start, next = start;
            for (int guard = 0; guard < 128 && next; ++guard) {
                std::vector<uint8_t> ebr(512);
                if (dev.Read(next * 512, 512, ebr.data()) < 512 || ebr[510] != 0x55 || ebr[511] != 0xAA) break;
                const uint8_t* l = ebr.data() + 446;
                const uint8_t* n = ebr.data() + 462;
                if (l[4] != 0 && LE32(l + 12) != 0) {
                    FoundPartition fp;
                    fp.number = 4 + static_cast<int>(parts.size()) + 1;
                    fp.offset = (next + LE32(l + 8)) * 512;
                    fp.length = static_cast<uint64_t>(LE32(l + 12)) * 512;
                    char lb[24];
                    std::snprintf(lb, sizeof(lb), "MBR logical 0x%02X", l[4]);
                    fp.label = lb;
                    parts.push_back(fp);
                }
                next = (n[4] != 0) ? extBase + LE32(n + 8) : 0;
            }
            continue;
        }
        if (count == 0) continue;
        FoundPartition fp;
        fp.number = number;
        fp.offset = start * 512;
        fp.length = count * 512;
        char lb[24];
        std::snprintf(lb, sizeof(lb), "MBR 0x%02X", type);
        fp.label = lb;
        parts.push_back(fp);
    }
    if (!gpt) return parts;

    for (uint32_t ss : {512u, 4096u}) {
        std::vector<uint8_t> hdr(ss);
        if (dev.Read(ss, ss, hdr.data()) < ss || std::memcmp(hdr.data(), "EFI PART", 8) != 0) continue;
        uint64_t entriesLba = LE64(hdr.data() + 72);
        uint32_t count = std::min<uint32_t>(LE32(hdr.data() + 80), 1024);
        uint32_t esz = LE32(hdr.data() + 84);
        if (esz < 128 || esz > 4096) continue;
        std::vector<uint8_t> ents(static_cast<size_t>(count) * esz);
        dev.Read(entriesLba * ss, ents.size(), ents.data());
        for (uint32_t i = 0; i < count; ++i) {
            const uint8_t* e = ents.data() + static_cast<size_t>(i) * esz;
            bool empty = std::all_of(e, e + 16, [](uint8_t b) { return b == 0; });
            if (empty) continue;
            FoundPartition fp;
            fp.number = static_cast<int>(i) + 1;
            uint64_t first = LE64(e + 32), last = LE64(e + 40);
            if (last < first) continue;
            fp.offset = first * ss;
            fp.length = (last - first + 1) * ss;
            fp.label = Utf16LeBytesToUtf8(e + 56, 36);
            fp.label = fp.label.substr(0, fp.label.find('\0'));
            if (fp.label.empty()) fp.label = "GPT partition";
            parts.push_back(fp);
        }
        break;
    }
    return parts;
}

}  // namespace

// =============================================================================
// 4. Scan context: progress streaming, statistics, cancellation, limits
// =============================================================================
namespace {

std::string CandidateJson(const Candidate& c) {
    JsonWriter w;
    w.BeginObject()
        .KV("id", c.id)
        .KV("source_id", c.sourceId)
        .KV("source_device", c.sourceDevice)
        .KV("source_kind", c.sourceKind)
        .KV("method", c.method)
        .KVnullable("file_name", c.fileName)
        .KVnullable("original_path", c.originalPath)
        .KV("extension", c.extension)
        .KVnullable("signature", c.signature)
        .KV("mime_type", c.mimeType.empty() ? "application/octet-stream" : c.mimeType)
        .KVu("size_bytes", c.sizeBytes)
        .KVu("lba", c.lba)
        .KVu("absolute_lba", c.absoluteLba)
        .KVu("sector_size", c.sectorSize);
    w.Key("extents").BeginArray();
    for (const auto& e : c.extents)
        w.BeginObject().KVu("offset", e.offset).KVu("length", e.length).KV("sparse", e.sparse).EndObject();
    w.EndArray();
    w.KVu("fragment_count", static_cast<uint64_t>(std::count_if(c.extents.begin(), c.extents.end(), [](const Extent& e) { return !e.sparse; })));
    w.KVnullable("resident_data_b64", c.residentDataB64);
    w.Key("timestamps").BeginObject().KVopt("created", c.created).KVopt("modified", c.modified).KVopt("mft_modified", c.mftModified).KVopt("accessed", c.accessed).EndObject();
    if (c.hasMft) {
        w.Key("mft").BeginObject()
            .KVu("record_number", c.recordNumber)
            .KVu("sequence", c.sequence)
            .KVu("parent_record", c.parentRecord)
            .KVu("record_flags", c.recordFlags)
            .KVu("file_attributes", c.fileAttributes)
            .KV("is_compressed", c.compressed)
            .KV("is_encrypted", c.encrypted)
            .KV("is_sparse", c.sparseFile)
            .KV("fixup_error", c.fixupError)
            .EndObject();
    } else {
        w.Key("mft").Null();
    }
    w.Key("integrity").BeginObject().KV("header_check", c.headerCheck);
    if (c.footerFound) w.KV("footer_found", *c.footerFound); else w.Key("footer_found").Null();
    w.KV("size_estimated", c.sizeEstimated).KV("size_source", c.sizeSource).KV("truncated", c.truncated);
    if (c.allocatedRatio >= 0) w.KVd("allocated_ratio", c.allocatedRatio); else w.Key("allocated_ratio").Null();
    w.KV("path_orphaned", c.pathOrphaned).EndObject();
    w.KVnullable("partition_hint", c.partitionHint);
    w.Key("notes").BeginArray();
    for (const auto& n : c.notes) w.String(n);
    w.EndArray();
    w.KVnullable("sample_path", c.samplePath).KVu("sample_bytes", c.sampleBytes);
    w.EndObject();
    return w.take();
}

struct SourceRun {
    SourceDescriptor desc;
    std::string filesystem = "Unknown";
    uint32_t sectorSize = 512;
    uint32_t clusterSize = 0;
    uint64_t totalBytes = 0;
    uint64_t bytesScanned = 0;
    uint64_t mftRecordsScanned = 0;
    uint64_t deletedEntriesFound = 0;
    uint64_t candidates = 0;
    uint64_t readErrors = 0;
    std::string status = "pending";  // pending | completed | cancelled | failed | unsupported
    std::string error;
    double elapsedSeconds = 0;
    std::vector<FoundPartition> partitions;  // for physical / image sources
};

class ScanContext {
public:
    explicit ScanContext(const ScanOptions& o) : opts(o) {}

    const ScanOptions& opts;
    std::vector<Candidate> candidates;
    std::vector<SourceRun> sources;
    size_t currentSource = 0;
    uint64_t sampleBytesUsed = 0;
    bool samplingEnabled = false;
    bool limitReached = false;

    bool ShouldStop() {
        if (CancelRequested() || limitReached) return true;
        double now = MonotonicSeconds();
        if (!opts.cancelFile.empty() && now - lastCancelCheck_ > 1.0) {
            lastCancelCheck_ = now;
            if (FileExists(opts.cancelFile)) {
                LogWarn("Cancel file detected - stopping scan and writing partial results.");
                RequestCancel();
                return true;
            }
        }
        return false;
    }

    bool TypeAllowed(const std::string& ext) const { return opts.typeFilter.empty() || opts.typeFilter.count(ext) > 0; }

    void Add(Candidate&& c, SourceRun& src) {
        if (candidates.size() >= opts.maxCandidates) {
            if (!limitReached) LogWarn("Candidate limit (" + std::to_string(opts.maxCandidates) + ") reached; stopping.");
            limitReached = true;
            return;
        }
        ++src.candidates;
        if (opts.streamCandidates) EventSink::Instance().Emit("{\"event\":\"candidate\",\"candidate\":" + CandidateJson(c) + "}");
        candidates.push_back(std::move(c));
    }

    void BeginPhase() {
        phaseStart_ = MonotonicSeconds();
        lastProgress_ = 0;
    }

    void Progress(const SourceRun& src, const std::string& phase, uint64_t done, uint64_t total, bool force = false,
                  const char* unit = "bytes") {
        double now = MonotonicSeconds();
        if (!force && now - lastProgress_ < 0.5) return;
        lastProgress_ = now;
        double frac = total ? std::min(1.0, static_cast<double>(done) / static_cast<double>(total)) : 0.0;
        double elapsed = std::max(1e-6, now - phaseStart_);
        double rate = static_cast<double>(done) / elapsed;
        double eta = (rate > 0 && total > done) ? static_cast<double>(total - done) / rate : 0.0;
        size_t n = std::max<size_t>(1, sources.size());
        double overall = (static_cast<double>(currentSource) + frac) / static_cast<double>(n) * 100.0;
        JsonWriter w;
        w.BeginObject()
            .KV("event", "progress")
            .KV("ts", NowIso())
            .KV("scan_id", opts.scanId)
            .KV("source_id", src.desc.sourceId)
            .KVu("source_index", currentSource)
            .KVu("source_count", n)
            .KV("phase", phase)
            .KV("unit", unit)
            .KVu("bytes_done", done)
            .KVu("bytes_total", total)
            .KVd("percent", frac * 100.0, 2)
            .KVd("overall_percent", overall, 2)
            .KVu("candidates_found", candidates.size())
            .KVd("rate_mb_s", rate / (1024.0 * 1024.0), 2)
            .KVd("eta_seconds", eta, 0)
            .KVu("read_errors", src.readErrors)
            .EndObject();
        EventSink::Instance().Emit(w.str());
    }

private:
    double lastCancelCheck_ = 0;
    double lastProgress_ = 0;
    double phaseStart_ = 0;
};

// Read up to `maxBytes` of a candidate's content (resident or via extents).
size_t ReadCandidateBytes(RawDevice& dev, const Candidate& c, uint64_t maxBytes, std::vector<uint8_t>& out) {
    out.clear();
    uint64_t want = std::min<uint64_t>(maxBytes, c.sizeBytes);
    if (!c.residentDataB64.empty()) {
        out = Base64Decode(c.residentDataB64);
        if (out.size() > want) out.resize(static_cast<size_t>(want));
        return out.size();
    }
    out.resize(static_cast<size_t>(want));
    uint64_t pos = 0;
    for (const auto& e : c.extents) {
        if (pos >= want) break;
        uint64_t n = std::min<uint64_t>(e.length, want - pos);
        if (e.sparse) std::memset(out.data() + pos, 0, static_cast<size_t>(n));
        else {
            size_t got = dev.Read(e.offset, static_cast<size_t>(n), out.data() + pos);
            if (got < n) std::memset(out.data() + pos + got, 0, static_cast<size_t>(n - got));
        }
        pos += n;
    }
    out.resize(static_cast<size_t>(pos));
    return out.size();
}

void MaybeWriteSample(ScanContext& ctx, Candidate& c, const uint8_t* data, size_t len) {
    if (!ctx.samplingEnabled || len == 0) return;
    if (ctx.sampleBytesUsed + len > ctx.opts.sampleBudgetBytes) return;
    std::string name = SanitizeFileComponent(c.id) + (c.extension.empty() ? ".bin" : "." + c.extension);
    std::string path = JoinPath(ctx.opts.sampleDir, name);
    OutFile f;
    if (!f.CreateAlways(path) || !f.Write(data, len)) return;
    c.samplePath = path;
    c.sampleBytes = len;
    ctx.sampleBytesUsed += len;
}

// =============================================================================
// 5. Signature registry + structure-aware carving parsers
// =============================================================================
constexpr uint64_t kNpos = ~0ULL;
constexpr uint64_t MiB = 1024ULL * 1024ULL;
constexpr uint64_t kEstimateCap = 2 * MiB;  // size assumed when no end marker exists

// Windowed random-access reader over [base, end) of a device.
class ByteSource {
public:
    ByteSource(RawDevice& d, uint64_t base, uint64_t end) : dev_(d), base_(base), end_(end) {}
    uint64_t Avail() const { return end_ > base_ ? end_ - base_ : 0; }
    uint64_t Base() const { return base_; }

    size_t Read(uint64_t rel, size_t n, uint8_t* out) {
        if (rel >= Avail()) return 0;
        n = static_cast<size_t>(std::min<uint64_t>(n, Avail() - rel));
        if (rel >= winStart_ && rel + n <= winStart_ + win_.size()) {
            std::memcpy(out, win_.data() + (rel - winStart_), n);
            return n;
        }
        if (n <= 4096) {  // small reads: refill the window and serve from it
            Fill(rel);
            size_t m = std::min(n, win_.size());
            std::memcpy(out, win_.data(), m);
            return m;
        }
        return dev_.Read(base_ + rel, n, out);
    }
    int At(uint64_t rel) {
        if (rel >= Avail()) return -1;
        if (rel < winStart_ || rel >= winStart_ + win_.size()) {
            Fill(rel);
            if (win_.empty()) return -1;
        }
        return win_[static_cast<size_t>(rel - winStart_)];
    }

private:
    void Fill(uint64_t rel) {
        size_t n = static_cast<size_t>(std::min<uint64_t>(kWin, Avail() - rel));
        win_.resize(n);
        size_t got = dev_.Read(base_ + rel, n, win_.data());
        win_.resize(got);
        winStart_ = rel;
    }
    static constexpr size_t kWin = 256 * 1024;
    RawDevice& dev_;
    uint64_t base_, end_;
    std::vector<uint8_t> win_;
    uint64_t winStart_ = 0;
};

// Forward search for `pat` in [from, maxRel). Returns kNpos when absent.
uint64_t FindPattern(ByteSource& s, uint64_t from, const void* patv, size_t plen, uint64_t maxRel) {
    const uint8_t* pat = static_cast<const uint8_t*>(patv);
    const size_t chunk = 1 << 20;
    std::vector<uint8_t> buf(chunk + plen);
    maxRel = std::min(maxRel, s.Avail());
    uint64_t pos = from;
    while (pos + plen <= maxRel) {
        size_t want = static_cast<size_t>(std::min<uint64_t>(chunk + plen - 1, maxRel - pos));
        size_t got = s.Read(pos, want, buf.data());
        if (got < plen) return kNpos;
        auto it = std::search(buf.begin(), buf.begin() + static_cast<std::ptrdiff_t>(got), pat, pat + plen);
        if (it != buf.begin() + static_cast<std::ptrdiff_t>(got)) return pos + static_cast<uint64_t>(it - buf.begin());
        if (got < want) return kNpos;
        pos += got - plen + 1;
        if (CancelRequested()) return kNpos;
    }
    return kNpos;
}

struct CarveResult {
    uint64_t size = 0;
    bool footerFound = false;
    bool sizeEstimated = false;
    bool truncated = false;
    std::string sizeSource = "structure";
    std::string ext;   // override (e.g. zip -> docx)
    std::string mime;
    std::string note;
};

void Estimate(ByteSource& s, CarveResult& r, uint64_t reachedPos, const std::string& note) {
    r.size = std::min<uint64_t>(std::max<uint64_t>(reachedPos, std::min<uint64_t>(kEstimateCap, s.Avail())), s.Avail());
    r.sizeEstimated = true;
    r.footerFound = false;
    r.truncated = true;
    r.sizeSource = "estimated";
    r.note = note;
}

// ---- JPEG: walk marker segments, then entropy-coded data to EOI (FF D9) -----
bool ParseJpeg(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[4];
    if (s.Read(0, 4, h) < 4 || h[0] != 0xFF || h[1] != 0xD8 || h[2] != 0xFF) return false;
    uint8_t m = h[3];
    if (!((m >= 0xE0 && m <= 0xEF) || m == 0xDB || m == 0xC0 || m == 0xC2 || m == 0xC4 || m == 0xFE || m == 0xDD)) return false;
    const uint64_t limit = std::min(maxSize, s.Avail());
    uint64_t pos = 2;
    int segments = 0;
    bool sawSos = false;
    std::vector<uint8_t> buf(64 * 1024);
    while (pos + 4 <= limit) {
        if (s.At(pos) != 0xFF) break;
        uint64_t mp = pos;
        while (mp + 1 < limit && s.At(mp + 1) == 0xFF) ++mp;  // fill bytes
        int mk = s.At(mp + 1);
        if (mk < 0) break;
        if (mk == 0xD9) {
            if (!sawSos) return false;  // header-only fragment
            r.size = mp + 2;
            r.footerFound = true;
            r.sizeSource = "footer";
            return true;
        }
        if (mk == 0xD8 || mk == 0x01 || (mk >= 0xD0 && mk <= 0xD7)) { pos = mp + 2; continue; }
        uint8_t lb[2];
        if (s.Read(mp + 2, 2, lb) < 2) break;
        uint16_t len = BE16(lb);
        if (len < 2) break;
        ++segments;
        pos = mp + 2 + len;
        if (mk == 0xDA) {
            sawSos = true;
            bool found = false;
            while (pos < limit) {
                size_t n = s.Read(pos, static_cast<size_t>(std::min<uint64_t>(buf.size(), limit - pos)), buf.data());
                if (n < 2) { pos = limit; break; }
                size_t i = 0;
                for (; i + 1 < n; ++i) {
                    if (buf[i] != 0xFF) continue;
                    uint8_t nx = buf[i + 1];
                    if (nx == 0x00 || (nx >= 0xD0 && nx <= 0xD7)) { ++i; continue; }  // stuffed byte / restart marker
                    if (nx == 0xFF) continue;                                       // fill byte before a marker
                    found = true;
                    break;
                }
                if (found) { pos += i; break; }
                pos += n - 1;
                if (CancelRequested()) return false;
            }
            if (!found) break;
        }
    }
    if (!sawSos || segments == 0) return false;
    Estimate(s, r, std::min(pos, limit), "JPEG end-of-image marker (FF D9) not found; file is likely fragmented or truncated");
    return true;
}

// ---- PNG: chunk walk to IEND ------------------------------------------------
bool ParsePng(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    static const uint8_t sig[8] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
    uint8_t h[8];
    if (s.Read(0, 8, h) < 8 || std::memcmp(h, sig, 8) != 0) return false;
    const uint64_t limit = std::min(maxSize, s.Avail());
    uint64_t pos = 8;
    bool first = true;
    while (pos + 12 <= limit) {
        uint8_t b[8];
        if (s.Read(pos, 8, b) < 8) break;
        uint32_t len = BE32(b);
        for (int i = 4; i < 8; ++i)
            if (!std::isalpha(b[i])) { if (first) return false; goto done; }
        if (first && std::memcmp(b + 4, "IHDR", 4) != 0) return false;
        first = false;
        if (len > 0x7FFFFFFFu) break;
        pos += 12ULL + len;
        if (std::memcmp(b + 4, "IEND", 4) == 0) {
            r.size = pos;
            r.footerFound = true;
            r.sizeSource = "footer";
            return true;
        }
    }
done:
    if (first) return false;
    Estimate(s, r, std::min(pos, limit), "PNG IEND chunk not found");
    return true;
}

// ---- GIF: block walk to trailer 0x3B ---------------------------------------
bool ParseGif(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[13];
    if (s.Read(0, 13, h) < 13 || std::memcmp(h, "GIF8", 4) != 0 || (h[4] != '7' && h[4] != '9') || h[5] != 'a') return false;
    const uint64_t limit = std::min(maxSize, s.Avail());
    uint64_t pos = 13;
    if (h[10] & 0x80) pos += 3ULL * (1ULL << ((h[10] & 7) + 1));
    auto skipSubBlocks = [&](uint64_t& p) -> bool {
        for (int guard = 0; guard < 10000000; ++guard) {
            int n = s.At(p);
            if (n < 0) return false;
            ++p;
            if (n == 0) return true;
            p += static_cast<uint64_t>(n);
            if (p > limit) return false;
        }
        return false;
    };
    int frames = 0;
    while (pos < limit) {
        int b = s.At(pos);
        if (b == 0x3B) {
            if (frames == 0) return false;
            r.size = pos + 1;
            r.footerFound = true;
            r.sizeSource = "footer";
            return true;
        } else if (b == 0x21) {
            pos += 2;
            if (!skipSubBlocks(pos)) break;
        } else if (b == 0x2C) {
            int flags = s.At(pos + 9);
            if (flags < 0) break;
            pos += 10;
            if (flags & 0x80) pos += 3ULL * (1ULL << ((flags & 7) + 1));
            pos += 1;  // LZW minimum code size
            if (!skipSubBlocks(pos)) break;
            ++frames;
        } else {
            break;
        }
    }
    if (frames == 0) return false;
    Estimate(s, r, std::min(pos, limit), "GIF trailer not found");
    return true;
}

// ---- BMP: BITMAPFILEHEADER size field ---------------------------------------
bool ParseBmp(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[30];
    if (s.Read(0, 30, h) < 30 || h[0] != 'B' || h[1] != 'M') return false;
    uint32_t size = LE32(h + 2), reserved = LE32(h + 6), dataOff = LE32(h + 10), dib = LE32(h + 14);
    if (reserved != 0) return false;
    if (dib != 12 && dib != 40 && dib != 52 && dib != 56 && dib != 64 && dib != 108 && dib != 124) return false;
    if (dataOff < 14 + dib || dataOff >= size || size > maxSize) return false;
    if (dib >= 40) {
        int32_t w = static_cast<int32_t>(LE32(h + 18));
        uint16_t planes = LE16(h + 26), bpp = LE16(h + 28);
        if (w <= 0 || w > 65535 || planes != 1 || (bpp != 1 && bpp != 4 && bpp != 8 && bpp != 16 && bpp != 24 && bpp != 32)) return false;
    }
    r.size = std::min<uint64_t>(size, s.Avail());
    r.truncated = size > s.Avail();
    r.footerFound = !r.truncated;
    r.sizeSource = "length_field";
    return true;
}

// ---- PDF: last %%EOF of the incremental-update chain ------------------------
bool ParsePdf(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[8];
    if (s.Read(0, 8, h) < 8 || std::memcmp(h, "%PDF-", 5) != 0 || !std::isdigit(h[5]) || h[6] != '.') return false;
    const uint64_t limit = std::min(maxSize, s.Avail());
    uint64_t eof = FindPattern(s, 5, "%%EOF", 5, limit);
    if (eof == kNpos) {
        Estimate(s, r, 0, "PDF %%EOF trailer not found");
        return true;
    }
    uint64_t end = eof + 5;
    for (int guard = 0; guard < 64; ++guard) {
        uint64_t window = std::min(limit, end + 4 * MiB);
        uint64_t next = FindPattern(s, end, "%%EOF", 5, window);
        if (next == kNpos) break;
        if (FindPattern(s, end, "%PDF-", 5, next) != kNpos) break;  // belongs to another PDF
        end = next + 5;
    }
    for (int k = 0; k < 2; ++k) {
        int c = s.At(end);
        if (c == '\r' || c == '\n') ++end;
    }
    r.size = end;
    r.footerFound = true;
    r.sizeSource = "footer";
    return true;
}

void DetectZipKind(const std::string& blob, CarveResult& r) {
    auto has = [&](const char* needle) { return blob.find(needle) != std::string::npos; };
    if (has("[Content_Types].xml") || has("_rels/")) {
        if (has("word/")) { r.ext = "docx"; r.mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"; return; }
        if (has("xl/")) { r.ext = "xlsx"; r.mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"; return; }
        if (has("ppt/")) { r.ext = "pptx"; r.mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"; return; }
    }
    if (has("mimetypeapplication/vnd.oasis.opendocument.text")) { r.ext = "odt"; r.mime = "application/vnd.oasis.opendocument.text"; return; }
    if (has("mimetypeapplication/vnd.oasis.opendocument.spreadsheet")) { r.ext = "ods"; r.mime = "application/vnd.oasis.opendocument.spreadsheet"; return; }
    if (has("mimetypeapplication/vnd.oasis.opendocument.presentation")) { r.ext = "odp"; r.mime = "application/vnd.oasis.opendocument.presentation"; return; }
    if (has("mimetypeapplication/epub+zip")) { r.ext = "epub"; r.mime = "application/epub+zip"; return; }
    if (has("AndroidManifest.xml")) { r.ext = "apk"; r.mime = "application/vnd.android.package-archive"; return; }
    if (has("META-INF/MANIFEST.MF")) { r.ext = "jar"; r.mime = "application/java-archive"; return; }
}

// ---- ZIP / OOXML / ODF: EOCD validated against central-directory offset ----
bool ParseZip(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[30];
    if (s.Read(0, 30, h) < 30 || std::memcmp(h, "PK\x03\x04", 4) != 0) return false;
    if (LE16(h + 4) > 63 || LE16(h + 26) == 0 || LE16(h + 26) > 1024) return false;  // version needed / name length sanity
    const uint64_t limit = std::min(maxSize, s.Avail());
    uint64_t from = 4;
    for (int tries = 0; tries < 32; ++tries) {
        uint64_t eocd = FindPattern(s, from, "PK\x05\x06", 4, limit);
        if (eocd == kNpos) break;
        uint8_t e[22];
        if (s.Read(eocd, 22, e) < 22) break;
        uint32_t cdSize = LE32(e + 12), cdOff = LE32(e + 16);
        uint16_t commentLen = LE16(e + 20);
        bool zip64 = cdOff == 0xFFFFFFFFu;
        if (zip64 || static_cast<uint64_t>(cdOff) + cdSize == eocd) {
            r.size = std::min<uint64_t>(eocd + 22 + commentLen, s.Avail());
            r.footerFound = true;
            r.sizeSource = "footer";
            if (zip64) r.note = "ZIP64 archive";
            std::string blob(static_cast<size_t>(std::min<uint64_t>(zip64 ? 65536 : cdSize, 1 * MiB)), '\0');
            size_t got = s.Read(zip64 ? 0 : cdOff, blob.size(), reinterpret_cast<uint8_t*>(&blob[0]));
            blob.resize(got);
            DetectZipKind(blob, r);
            return true;
        }
        from = eocd + 4;
    }
    std::string blob(65536, '\0');
    blob.resize(s.Read(0, blob.size(), reinterpret_cast<uint8_t*>(&blob[0])));
    DetectZipKind(blob, r);
    Estimate(s, r, 0, "ZIP end-of-central-directory record not found");
    return true;
}

// ---- RAR 4.x / 5.x: end-of-archive block -----------------------------------
bool ParseRar(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[8];
    if (s.Read(0, 8, h) < 8 || std::memcmp(h, "Rar!\x1A\x07", 6) != 0) return false;
    const uint64_t limit = std::min(maxSize, s.Avail());
    if (h[6] == 0x00) {
        static const uint8_t end4[7] = {0xC4, 0x3D, 0x7B, 0x00, 0x40, 0x07, 0x00};
        uint64_t p = FindPattern(s, 7, end4, 7, limit);
        if (p != kNpos) { r.size = p + 7; r.footerFound = true; r.sizeSource = "footer"; return true; }
    } else if (h[6] == 0x01 && h[7] == 0x00) {
        static const uint8_t end5[8] = {0x1D, 0x77, 0x56, 0x51, 0x03, 0x05, 0x04, 0x00};
        uint64_t p = FindPattern(s, 8, end5, 8, limit);
        if (p != kNpos) { r.size = p + 8; r.footerFound = true; r.sizeSource = "footer"; return true; }
    } else {
        return false;
    }
    Estimate(s, r, 0, "RAR end-of-archive block not found");
    return true;
}

// ---- 7-Zip: start header gives exact size -----------------------------------
bool Parse7z(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[32];
    static const uint8_t sig[6] = {'7', 'z', 0xBC, 0xAF, 0x27, 0x1C};
    if (s.Read(0, 32, h) < 32 || std::memcmp(h, sig, 6) != 0 || h[6] != 0) return false;
    uint64_t off = LE64(h + 12), sz = LE64(h + 20);
    if (sz == 0 || sz > maxSize || off > maxSize) return false;
    uint64_t total = 32 + off + sz;
    if (total > maxSize) return false;
    r.size = std::min(total, s.Avail());
    r.truncated = total > s.Avail();
    r.footerFound = !r.truncated;
    r.sizeSource = "length_field";
    return true;
}

// ---- OLE2 compound file (doc/xls/ppt/msg): highest used FAT sector ---------
bool ParseOle(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    std::vector<uint8_t> h(512);
    static const uint8_t sig[8] = {0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1};
    if (s.Read(0, 512, h.data()) < 512 || std::memcmp(h.data(), sig, 8) != 0) return false;
    uint16_t shift = LE16(h.data() + 0x1E);
    if (shift != 9 && shift != 12) return false;
    const uint32_t ss = 1u << shift;
    uint32_t numFat = LE32(h.data() + 0x2C);
    if (numFat == 0 || numFat > 1000000) return false;
    uint64_t maxSect = 0;
    bool any = false;
    std::vector<uint8_t> fat(ss);
    for (uint32_t i = 0; i < std::min<uint32_t>(numFat, 109); ++i) {
        uint32_t fs = LE32(h.data() + 0x4C + i * 4);
        if (fs >= 0xFFFFFFFAu) continue;
        uint64_t off = (static_cast<uint64_t>(fs) + 1) * ss;
        if (off + ss > s.Avail()) continue;
        if (s.Read(off, ss, fat.data()) < ss) continue;
        for (uint32_t j = 0; j < ss / 4; ++j) {
            if (LE32(fat.data() + j * 4) != 0xFFFFFFFFu) {  // not FREESECT
                maxSect = std::max<uint64_t>(maxSect, static_cast<uint64_t>(i) * (ss / 4) + j);
                any = true;
            }
        }
    }
    if (!any) return false;
    uint64_t total = (maxSect + 2) * ss;
    if (total > maxSize) return false;
    r.size = std::min(total, s.Avail());
    r.truncated = total > s.Avail();
    r.footerFound = !r.truncated;
    r.sizeSource = "structure";
    if (numFat > 109) { r.sizeEstimated = true; r.note = "DIFAT chain beyond header not followed; size may be underestimated"; }

    // Identify the Office application by its well-known stream names (UTF-16LE).
    auto u16 = [](const char* a) { std::string o; for (; *a; ++a) { o.push_back(*a); o.push_back('\0'); } return o; };
    uint64_t searchLimit = std::min<uint64_t>(r.size, 8 * MiB);
    struct { const char* stream; const char* ext; const char* mime; } kinds[] = {
        {"WordDocument", "doc", "application/msword"},
        {"Workbook", "xls", "application/vnd.ms-excel"},
        {"PowerPoint Document", "ppt", "application/vnd.ms-powerpoint"},
        {"__substg1.0_", "msg", "application/vnd.ms-outlook"},
    };
    r.ext = "ole";
    r.mime = "application/x-ole-storage";
    for (auto& k : kinds) {
        std::string pat = u16(k.stream);
        if (FindPattern(s, 512, pat.data(), pat.size(), searchLimit) != kNpos) { r.ext = k.ext; r.mime = k.mime; break; }
    }
    return true;
}

// ---- ISO-BMFF (MP4/MOV/M4A/3GP/HEIC): top-level box walk -------------------
bool ParseMp4(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[16];
    if (s.Read(0, 16, h) < 16 || std::memcmp(h + 4, "ftyp", 4) != 0) return false;
    uint32_t firstSize = BE32(h);
    if (firstSize < 16 || firstSize > 4096) return false;
    static const char* known[] = {"ftyp", "moov", "mdat", "free", "skip", "wide", "uuid", "pdin", "moof", "mfra",
                                  "meta", "styp", "sidx", "prft", "emsg", "pnot", "junk", "iinf", "iloc"};
    auto isKnown = [&](const uint8_t* t) {
        for (const char* k : known) if (std::memcmp(t, k, 4) == 0) return true;
        return false;
    };
    const uint64_t limit = std::min(maxSize, s.Avail());
    uint64_t pos = 0;
    bool moov = false, mdat = false, sizeToEnd = false;
    int boxes = 0;
    while (pos + 8 <= limit) {
        uint8_t b[16];
        if (s.Read(pos, 16, b) < 8) break;
        if (!isKnown(b + 4)) break;
        uint64_t sz = BE32(b);
        if (sz == 1) sz = BE64(b + 8);
        else if (sz == 0) { sizeToEnd = true; break; }
        if (sz < 8) break;
        if (std::memcmp(b + 4, "moov", 4) == 0) moov = true;
        if (std::memcmp(b + 4, "mdat", 4) == 0) mdat = true;
        if (std::memcmp(b + 4, "meta", 4) == 0) moov = true;  // HEIF keeps structure in meta
        pos += sz;
        ++boxes;
    }
    if (boxes == 0) return false;
    // Subtype from major brand.
    std::string brand(reinterpret_cast<char*>(h + 8), 4);
    if (brand == "qt  ") { r.ext = "mov"; r.mime = "video/quicktime"; }
    else if (brand == "M4A " || brand == "M4B ") { r.ext = "m4a"; r.mime = "audio/mp4"; }
    else if (brand.compare(0, 3, "3gp") == 0) { r.ext = "3gp"; r.mime = "video/3gpp"; }
    else if (brand == "heic" || brand == "heix" || brand == "mif1") { r.ext = "heic"; r.mime = "image/heic"; }
    if (sizeToEnd || pos > limit) {
        Estimate(s, r, std::min(pos, limit), "ISO-BMFF box extends beyond carve limit");
        return true;
    }
    r.size = pos;
    r.footerFound = moov && (mdat || r.ext == "heic");
    r.sizeSource = "structure";
    if (!r.footerFound) r.note = "moov/mdat box missing - media index may be incomplete";
    return true;
}

// ---- RIFF (WAV / AVI / WEBP): chunk size field ------------------------------
bool ParseRiff(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[12];
    if (s.Read(0, 12, h) < 12 || std::memcmp(h, "RIFF", 4) != 0) return false;
    uint64_t total = static_cast<uint64_t>(LE32(h + 4)) + 8;
    std::string form(reinterpret_cast<char*>(h + 8), 4);
    if (form == "WAVE") { r.ext = "wav"; r.mime = "audio/wav"; }
    else if (form == "AVI ") { r.ext = "avi"; r.mime = "video/x-msvideo"; }
    else if (form == "WEBP") { r.ext = "webp"; r.mime = "image/webp"; }
    else return false;
    if (total < 44 || total > maxSize) return false;
    r.size = std::min(total, s.Avail());
    r.truncated = total > s.Avail();
    r.footerFound = !r.truncated;
    r.sizeSource = "length_field";
    return true;
}

// ---- SQLite 3: page size * page count ---------------------------------------
bool ParseSqlite(ByteSource& s, uint64_t maxSize, CarveResult& r) {
    uint8_t h[100];
    if (s.Read(0, 100, h) < 100 || std::memcmp(h, "SQLite format 3\0", 16) != 0) return false;
    uint32_t pageSize = BE16(h + 16);
    if (pageSize == 1) pageSize = 65536;
    if (pageSize < 512 || (pageSize & (pageSize - 1)) != 0) return false;
    uint64_t pages = BE32(h + 28);
    if (pages == 0) {
        Estimate(s, r, pageSize, "SQLite in-header page count is zero (legacy writer)");
        return true;
    }
    uint64_t total = pages * pageSize;
    if (total > maxSize) return false;
    r.size = std::min(total, s.Avail());
    r.truncated = total > s.Avail();
    r.footerFound = !r.truncated;
    r.sizeSource = "length_field";
    return true;
}

using ParseFn = bool (*)(ByteSource&, uint64_t, CarveResult&);

struct SigDef {
    const char* id;
    const char* ext;
    const char* mime;
    const char* description;
    std::vector<uint8_t> magic;
    uint32_t magicOffset;
    uint64_t maxSize;
    ParseFn parse;
    std::vector<std::string> extensions;  // every extension this signature can yield
};

std::vector<uint8_t> Bytes(std::initializer_list<int> l) {
    std::vector<uint8_t> v;
    for (int x : l) v.push_back(static_cast<uint8_t>(x));
    return v;
}
std::vector<uint8_t> Ascii(const char* s, size_t n) { return std::vector<uint8_t>(s, s + n); }

const std::vector<SigDef>& Signatures() {
    static const std::vector<SigDef> sigs = {
        {"jpg", "jpg", "image/jpeg", "JPEG image (FF D8 FF ... FF D9)", Bytes({0xFF, 0xD8, 0xFF}), 0, 64 * MiB, ParseJpeg, {"jpg", "jpeg"}},
        {"png", "png", "image/png", "PNG image (89 50 4E 47 ... IEND)", Bytes({0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A}), 0, 128 * MiB, ParsePng, {"png"}},
        {"gif", "gif", "image/gif", "GIF image (GIF87a/GIF89a ... 3B)", Ascii("GIF8", 4), 0, 64 * MiB, ParseGif, {"gif"}},
        {"bmp", "bmp", "image/bmp", "Windows bitmap (BM + size field)", Ascii("BM", 2), 0, 256 * MiB, ParseBmp, {"bmp"}},
        {"pdf", "pdf", "application/pdf", "PDF document (%PDF- ... %%EOF)", Ascii("%PDF-", 5), 0, 512 * MiB, ParsePdf, {"pdf"}},
        {"zip", "zip", "application/zip", "ZIP / DOCX / XLSX / PPTX / ODF (50 4B 03 04 ... EOCD)", Bytes({'P', 'K', 3, 4}), 0, 512 * MiB, ParseZip,
         {"zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "jar", "apk"}},
        {"rar", "rar", "application/vnd.rar", "RAR archive (Rar! 1A 07)", Ascii("Rar!\x1A\x07", 6), 0, 512 * MiB, ParseRar, {"rar"}},
        {"7z", "7z", "application/x-7z-compressed", "7-Zip archive (37 7A BC AF 27 1C)", Bytes({'7', 'z', 0xBC, 0xAF, 0x27, 0x1C}), 0, 4096 * MiB, Parse7z, {"7z"}},
        {"ole", "ole", "application/x-ole-storage", "OLE2 compound file: DOC / XLS / PPT / MSG (D0 CF 11 E0)",
         Bytes({0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1}), 0, 512 * MiB, ParseOle, {"doc", "xls", "ppt", "msg", "ole"}},
        {"mp4", "mp4", "video/mp4", "ISO-BMFF video/audio/HEIC (....ftyp)", Ascii("ftyp", 4), 4, 8192 * MiB, ParseMp4, {"mp4", "mov", "m4a", "3gp", "heic"}},
        {"riff", "riff", "application/octet-stream", "RIFF container: WAV / AVI / WEBP", Ascii("RIFF", 4), 0, 4096 * MiB, ParseRiff, {"wav", "avi", "webp"}},
        {"sqlite", "sqlite", "application/vnd.sqlite3", "SQLite 3 database", Ascii("SQLite format 3\0", 16), 0, 4096 * MiB, ParseSqlite, {"sqlite", "db"}},
    };
    return sigs;
}

// Identify a signature from the first bytes of a buffer (MFT/FAT header check).
const SigDef* IdentifySignature(const uint8_t* p, size_t n) {
    for (const auto& s : Signatures()) {
        if (n < s.magicOffset + s.magic.size()) continue;
        if (std::memcmp(p + s.magicOffset, s.magic.data(), s.magic.size()) == 0) return &s;
    }
    return nullptr;
}

// Map a file extension to the signature id expected for it.
std::string ExpectedSignatureFor(const std::string& ext) {
    for (const auto& s : Signatures())
        for (const auto& e : s.extensions)
            if (e == ext) return s.id;
    if (ext == "jpeg" || ext == "jpe" || ext == "jfif") return "jpg";
    if (ext == "m4v") return "mp4";
    return "";
}

std::string MimeForExtension(const std::string& ext) {
    static const std::map<std::string, std::string> m = {
        {"jpg", "image/jpeg"}, {"jpeg", "image/jpeg"}, {"png", "image/png"}, {"gif", "image/gif"}, {"bmp", "image/bmp"},
        {"webp", "image/webp"}, {"heic", "image/heic"}, {"tif", "image/tiff"}, {"tiff", "image/tiff"},
        {"pdf", "application/pdf"}, {"txt", "text/plain"}, {"csv", "text/csv"}, {"rtf", "application/rtf"},
        {"htm", "text/html"}, {"html", "text/html"}, {"xml", "application/xml"}, {"json", "application/json"},
        {"doc", "application/msword"}, {"xls", "application/vnd.ms-excel"}, {"ppt", "application/vnd.ms-powerpoint"},
        {"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        {"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        {"pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
        {"odt", "application/vnd.oasis.opendocument.text"}, {"zip", "application/zip"}, {"rar", "application/vnd.rar"},
        {"7z", "application/x-7z-compressed"}, {"mp3", "audio/mpeg"}, {"wav", "audio/wav"}, {"m4a", "audio/mp4"},
        {"mp4", "video/mp4"}, {"mov", "video/quicktime"}, {"avi", "video/x-msvideo"}, {"mkv", "video/x-matroska"},
        {"sqlite", "application/vnd.sqlite3"}, {"db", "application/vnd.sqlite3"}, {"msg", "application/vnd.ms-outlook"},
        {"exe", "application/vnd.microsoft.portable-executable"}, {"dll", "application/vnd.microsoft.portable-executable"},
    };
    auto it = m.find(ext);
    return it == m.end() ? "application/octet-stream" : it->second;
}

}  // namespace

std::string SignaturesJson() {
    JsonWriter w(true);
    w.BeginObject().KV("schema_version", kSchemaVersion).Key("signatures").BeginArray();
    for (const auto& s : Signatures()) {
        std::string hex;
        for (uint8_t b : s.magic) { if (!hex.empty()) hex.push_back(' '); hex += HexU64(b, 2); }
        w.BeginObject()
            .KV("id", s.id)
            .KV("default_extension", s.ext)
            .KV("mime_type", s.mime)
            .KV("description", s.description)
            .KV("magic_hex", ToUpperAscii(hex))
            .KVu("magic_offset", s.magicOffset)
            .KVu("max_size_bytes", s.maxSize);
        w.Key("extensions").BeginArray();
        for (const auto& e : s.extensions) w.String(e);
        w.EndArray().EndObject();
    }
    w.EndArray().EndObject();
    return w.take();
}

// =============================================================================
// 6. NTFS Quick Scan
// =============================================================================
namespace {

constexpr uint64_t kRefMask = 0x0000FFFFFFFFFFFFULL;  // low 48 bits = MFT record number
constexpr uint32_t kAttrStandardInfo = 0x10;
constexpr uint32_t kAttrAttributeList = 0x20;
constexpr uint32_t kAttrFileName = 0x30;
constexpr uint32_t kAttrData = 0x80;
constexpr uint32_t kAttrEnd = 0xFFFFFFFFu;
constexpr uint64_t kRootDirRecord = 5;
constexpr uint64_t kBitmapRecord = 6;

struct NtfsRun {
    uint64_t vcn = 0;
    uint64_t lcn = 0;
    uint64_t length = 0;  // clusters
    bool sparse = false;
};

// Decode an NTFS mapping-pairs array (data runs). Each run header byte holds
// the byte-width of the length (low nibble) and of the signed LCN delta (high
// nibble). A zero header terminates the list; an absent offset means sparse.
bool DecodeRunList(const uint8_t* p, size_t avail, uint64_t startVcn, uint64_t totalClusters, std::vector<NtfsRun>& out) {
    size_t i = 0;
    int64_t lcn = 0;
    uint64_t vcn = startVcn;
    while (i < avail) {
        uint8_t hdr = p[i];
        if (hdr == 0) return true;
        int lenSz = hdr & 0x0F, offSz = hdr >> 4;
        if (lenSz == 0 || lenSz > 8 || offSz > 8 || i + 1 + lenSz + offSz > avail) return false;
        uint64_t len = 0;
        for (int k = 0; k < lenSz; ++k) len |= static_cast<uint64_t>(p[i + 1 + k]) << (8 * k);
        uint64_t rawOff = 0;
        for (int k = 0; k < offSz; ++k) rawOff |= static_cast<uint64_t>(p[i + 1 + lenSz + k]) << (8 * k);
        if (offSz > 0 && offSz < 8 && (p[i + lenSz + offSz] & 0x80)) rawOff |= ~0ULL << (8 * offSz);  // sign-extend
        i += 1 + static_cast<size_t>(lenSz) + static_cast<size_t>(offSz);
        if (len == 0 || len > totalClusters) return false;
        NtfsRun r;
        r.vcn = vcn;
        r.length = len;
        if (offSz == 0) {
            r.sparse = true;
        } else {
            lcn += static_cast<int64_t>(rawOff);
            if (lcn < 0 || static_cast<uint64_t>(lcn) + len > totalClusters) return false;
            r.lcn = static_cast<uint64_t>(lcn);
        }
        out.push_back(r);
        vcn += len;
    }
    return true;
}

// Multi-sector transfer protection: the last two bytes of every 512-byte
// stride were replaced by the update sequence number on write; restore them.
bool ApplyFixups(uint8_t* rec, size_t size) {
    uint16_t usaOfs = LE16(rec + 4), usaCount = LE16(rec + 6);
    if (usaCount < 2 || usaOfs + static_cast<size_t>(usaCount) * 2 > size || static_cast<size_t>(usaCount - 1) * 512 > size) return false;
    uint16_t usn = LE16(rec + usaOfs);
    bool ok = true;
    for (uint16_t i = 1; i < usaCount; ++i) {
        size_t pos = static_cast<size_t>(i) * 512 - 2;
        if (LE16(rec + pos) != usn) ok = false;  // torn write: keep parsing, flag it
        rec[pos] = rec[usaOfs + 2 * i];
        rec[pos + 1] = rec[usaOfs + 2 * i + 1];
    }
    return ok;
}

struct FileNameInfo {
    uint64_t parentRef = 0;  // full reference: seq << 48 | record
    std::string name;
    uint8_t nameSpace = 0;   // 0 POSIX, 1 Win32, 2 DOS, 3 Win32&DOS
    uint64_t created = 0, modified = 0, mftModified = 0, accessed = 0;
    uint64_t realSize = 0;
};

struct DataAttr {
    bool present = false;
    bool resident = false;
    std::vector<uint8_t> residentData;
    std::vector<NtfsRun> runs;
    uint64_t realSize = 0;
    uint64_t initSize = 0;
    uint16_t flags = 0;  // 0x0001 compressed, 0x4000 encrypted, 0x8000 sparse
    bool runsValid = true;
};

struct MftRecord {
    bool fixupOk = true;
    uint16_t sequence = 0;
    uint16_t flags = 0;  // 0x01 in use, 0x02 directory
    uint64_t baseRef = 0;
    bool hasStdInfo = false;
    uint64_t siCreated = 0, siModified = 0, siMftModified = 0, siAccessed = 0;
    uint32_t siAttributes = 0;
    std::vector<FileNameInfo> names;
    DataAttr data;
    bool hasAttrList = false;
    bool attrListResident = true;
    std::vector<uint8_t> attrListData;
    std::vector<NtfsRun> attrListRuns;
    uint64_t attrListSize = 0;

    const FileNameInfo* BestName() const {
        const FileNameInfo* best = nullptr;
        auto rank = [](uint8_t ns) { return ns == 1 || ns == 3 ? 3 : ns == 0 ? 2 : 1; };
        for (const auto& n : names)
            if (!best || rank(n.nameSpace) > rank(best->nameSpace)) best = &n;
        return best;
    }
};

bool ParseMftRecord(uint8_t* rec, size_t size, uint64_t totalClusters, MftRecord& r) {
    if (size < 64 || std::memcmp(rec, "FILE", 4) != 0) return false;
    r.fixupOk = ApplyFixups(rec, size);
    r.sequence = LE16(rec + 0x10);
    r.flags = LE16(rec + 0x16);
    r.baseRef = LE64(rec + 0x20) & kRefMask;
    size_t off = LE16(rec + 0x14);
    size_t used = std::min<size_t>(LE32(rec + 0x18), size);
    for (int guard = 0; guard < 256 && off + 16 <= used; ++guard) {
        const uint8_t* a = rec + off;
        uint32_t type = LE32(a);
        if (type == kAttrEnd) break;
        uint32_t len = LE32(a + 4);
        if (len < 16 || off + len > used) break;
        uint8_t nonResident = a[8];
        uint8_t nameLen = a[9];
        uint16_t aflags = LE16(a + 0x0C);
        if (!nonResident) {
            if (len < 0x18) { off += len; continue; }
            uint32_t vlen = LE32(a + 0x10);
            uint16_t voff = LE16(a + 0x14);
            if (static_cast<size_t>(voff) + vlen > len) { off += len; continue; }
            const uint8_t* v = a + voff;
            if (type == kAttrStandardInfo && vlen >= 36) {
                r.hasStdInfo = true;
                r.siCreated = LE64(v);
                r.siModified = LE64(v + 8);
                r.siMftModified = LE64(v + 16);
                r.siAccessed = LE64(v + 24);
                r.siAttributes = LE32(v + 32);
            } else if (type == kAttrFileName && vlen >= 0x42) {
                FileNameInfo fn;
                fn.parentRef = LE64(v);
                fn.created = LE64(v + 0x08);
                fn.modified = LE64(v + 0x10);
                fn.mftModified = LE64(v + 0x18);
                fn.accessed = LE64(v + 0x20);
                fn.realSize = LE64(v + 0x30);
                uint8_t nlen = v[0x40];
                fn.nameSpace = v[0x41];
                if (0x42 + static_cast<size_t>(nlen) * 2 <= vlen) {
                    fn.name = Utf16LeBytesToUtf8(v + 0x42, nlen);
                    r.names.push_back(std::move(fn));
                }
            } else if (type == kAttrData && nameLen == 0 && !r.data.present) {
                r.data.present = true;
                r.data.resident = true;
                r.data.residentData.assign(v, v + vlen);
                r.data.realSize = r.data.initSize = vlen;
                r.data.flags = aflags;
            } else if (type == kAttrAttributeList) {
                r.hasAttrList = true;
                r.attrListResident = true;
                r.attrListData.assign(v, v + vlen);
                r.attrListSize = vlen;
            }
        } else {
            if (len < 0x40) { off += len; continue; }
            uint64_t startVcn = LE64(a + 0x10);
            uint16_t runOfs = LE16(a + 0x20);
            if (runOfs >= len) { off += len; continue; }
            if (type == kAttrData && nameLen == 0) {
                if (!r.data.present) {
                    r.data.present = true;
                    r.data.resident = false;
                    r.data.flags = aflags;
                }
                if (startVcn == 0) {
                    r.data.realSize = LE64(a + 0x30);
                    r.data.initSize = LE64(a + 0x38);
                }
                if (!DecodeRunList(a + runOfs, len - runOfs, startVcn, totalClusters, r.data.runs)) r.data.runsValid = false;
            } else if (type == kAttrAttributeList) {
                r.hasAttrList = true;
                r.attrListResident = false;
                r.attrListSize = LE64(a + 0x30);
                DecodeRunList(a + runOfs, len - runOfs, startVcn, totalClusters, r.attrListRuns);
            }
        }
        off += len;
    }
    return true;
}

class NtfsVolume {
public:
    NtfsVolume(RawDevice& dev, uint64_t volumeOffset) : dev_(dev), volOff_(volumeOffset) {}

    bool Init(std::string* error) {
        uint8_t bs[512];
        if (dev_.Read(volOff_, 512, bs) < 512 || std::memcmp(bs + 3, "NTFS    ", 8) != 0) {
            if (error) *error = "NTFS boot sector signature not found";
            return false;
        }
        bytesPerSector_ = LE16(bs + 0x0B);
        uint8_t spcRaw = bs[0x0D];
        uint32_t spc = spcRaw <= 0x80 ? spcRaw : (1u << (256 - spcRaw));
        if ((bytesPerSector_ != 512 && bytesPerSector_ != 1024 && bytesPerSector_ != 2048 && bytesPerSector_ != 4096) || spc == 0 ||
            (spc & (spc - 1)) != 0) {
            if (error) *error = "Invalid NTFS geometry in boot sector";
            return false;
        }
        clusterSize_ = bytesPerSector_ * spc;
        totalSectors_ = LE64(bs + 0x28);
        totalClusters_ = totalSectors_ / spc;
        mftLcn_ = LE64(bs + 0x30);
        mftMirrLcn_ = LE64(bs + 0x38);
        int8_t cpr = static_cast<int8_t>(bs[0x40]);
        recordSize_ = cpr > 0 ? static_cast<uint32_t>(cpr) * clusterSize_ : (1u << (-cpr));
        if (recordSize_ < 256 || recordSize_ > 65536 || mftLcn_ >= totalClusters_) {
            if (error) *error = "Invalid $MFT location or record size";
            return false;
        }

        // Read $MFT's own record (record 0) to learn where the MFT lives; fall
        // back to $MFTMirr if the primary copy is damaged.
        for (uint64_t lcn : {mftLcn_, mftMirrLcn_}) {
            std::vector<uint8_t> rec(recordSize_);
            if (lcn >= totalClusters_) continue;
            if (dev_.Read(volOff_ + lcn * clusterSize_, recordSize_, rec.data()) < recordSize_) continue;
            MftRecord r;
            if (!ParseMftRecord(rec.data(), rec.size(), totalClusters_, r) || !r.data.present || r.data.resident || r.data.runs.empty())
                continue;
            mftRuns_ = r.data.runs;
            mftSize_ = r.data.realSize;
            if (lcn == mftMirrLcn_) LogWarn("Primary $MFT record 0 unreadable; using $MFTMirr copy.");
            break;
        }
        if (mftRuns_.empty()) {
            if (error) *error = "Unable to read $MFT record 0 or its mirror";
            return false;
        }
        recordCount_ = mftSize_ / recordSize_;
        return true;
    }

    // Read `len` bytes at virtual byte offset `vOff` of a non-resident stream.
    bool ReadVirtual(const std::vector<NtfsRun>& runs, uint64_t vOff, size_t len, uint8_t* out) {
        size_t done = 0;
        while (done < len) {
            uint64_t v = vOff + done;
            uint64_t vcn = v / clusterSize_;
            const NtfsRun* run = nullptr;
            for (const auto& r : runs)
                if (vcn >= r.vcn && vcn < r.vcn + r.length) { run = &r; break; }
            if (!run) { std::memset(out + done, 0, len - done); return false; }
            uint64_t inRun = v - run->vcn * clusterSize_;
            uint64_t runBytes = run->length * clusterSize_;
            size_t n = static_cast<size_t>(std::min<uint64_t>(len - done, runBytes - inRun));
            if (run->sparse) std::memset(out + done, 0, n);
            else {
                size_t got = dev_.Read(volOff_ + run->lcn * clusterSize_ + inRun, n, out + done);
                if (got < n) std::memset(out + done + got, 0, n - got);
            }
            done += n;
        }
        return true;
    }

    // Bulk read of `count` consecutive records starting at `first`.
    bool ReadRecords(uint64_t first, uint64_t count, std::vector<uint8_t>& out) {
        if (first >= recordCount_) return false;
        count = std::min(count, recordCount_ - first);
        out.resize(static_cast<size_t>(count * recordSize_));
        return ReadVirtual(mftRuns_, first * recordSize_, out.size(), out.data());
    }

    bool ReadRecord(uint64_t recNo, std::vector<uint8_t>& out) {
        if (recNo >= recordCount_) return false;
        out.resize(recordSize_);
        return ReadVirtual(mftRuns_, recNo * recordSize_, recordSize_, out.data());
    }

    // Load $Bitmap (record 6): one bit per cluster, 1 = allocated.
    bool LoadBitmap() {
        std::vector<uint8_t> rec;
        if (!ReadRecord(kBitmapRecord, rec)) return false;
        MftRecord r;
        if (!ParseMftRecord(rec.data(), rec.size(), totalClusters_, r) || !r.data.present) return false;
        uint64_t size = r.data.realSize;
        if (size == 0 || size > 512 * MiB) return false;
        bitmap_.resize(static_cast<size_t>(size));
        if (r.data.resident) std::memcpy(bitmap_.data(), r.data.residentData.data(), std::min<size_t>(bitmap_.size(), r.data.residentData.size()));
        else ReadVirtual(r.data.runs, 0, bitmap_.size(), bitmap_.data());
        return true;
    }

    bool HasBitmap() const { return !bitmap_.empty(); }
    bool ClusterAllocated(uint64_t lcn) const {
        uint64_t byte = lcn >> 3;
        if (byte >= bitmap_.size()) return true;
        return (bitmap_[static_cast<size_t>(byte)] >> (lcn & 7)) & 1;
    }

    // Fraction of the given (non-sparse) clusters currently marked allocated.
    double AllocatedRatio(const std::vector<NtfsRun>& runs, uint64_t realSize) const {
        if (bitmap_.empty()) return -1.0;
        uint64_t needClusters = (realSize + clusterSize_ - 1) / clusterSize_;
        uint64_t total = 0, allocated = 0;
        for (const auto& r : runs) {
            if (r.sparse) continue;
            uint64_t n = std::min<uint64_t>(r.length, needClusters > total ? needClusters - total : 0);
            for (uint64_t c = 0; c < n; ++c) allocated += ClusterAllocated(r.lcn + c) ? 1 : 0;
            total += n;
            if (total >= needClusters) break;
        }
        return total ? static_cast<double>(allocated) / static_cast<double>(total) : -1.0;
    }

    uint32_t ClusterSize() const { return clusterSize_; }
    uint32_t RecordSize() const { return recordSize_; }
    uint32_t BytesPerSector() const { return bytesPerSector_; }
    uint64_t RecordCount() const { return recordCount_; }
    uint64_t TotalClusters() const { return totalClusters_; }
    uint64_t VolumeOffset() const { return volOff_; }
    uint64_t VolumeBytes() const { return totalSectors_ * bytesPerSector_; }

private:
    RawDevice& dev_;
    uint64_t volOff_;
    uint32_t bytesPerSector_ = 512, clusterSize_ = 4096, recordSize_ = 1024;
    uint64_t totalSectors_ = 0, totalClusters_ = 0, mftLcn_ = 0, mftMirrLcn_ = 0;
    std::vector<NtfsRun> mftRuns_;
    uint64_t mftSize_ = 0, recordCount_ = 0;
    std::vector<uint8_t> bitmap_;
};

struct DirNode {
    uint64_t parent = 0;
    uint16_t parentSeq = 0;
    uint16_t seq = 0;
    bool inUse = false;
    std::string name;
};

struct DeletedFile {
    uint64_t recNo = 0;
    MftRecord rec;
    FileNameInfo name;
};

std::string NormalizeWinPath(std::string p) {
    for (auto& c : p) if (c == '/') c = '\\';
    while (p.size() > 3 && p.back() == '\\') p.pop_back();
    return ToLowerAscii(p);
}

// Resolve a folder (live or deleted) to its MFT record number.
uint64_t ResolveFolderRecord(const std::string& folderPath, const SourceDescriptor& src,
                             const std::unordered_map<uint64_t, DirNode>& dirs,
                             const std::function<std::string(uint64_t)>& dirPath) {
#ifdef _WIN32
    // Fast path: ask the file system for the folder's file reference number.
    if (!src.letter.empty() && folderPath.size() >= 2 && IEquals(folderPath.substr(0, 2), src.letter)) {
        HANDLE h = CreateFileW(Utf8ToWide(folderPath).c_str(), FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                               nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
        if (h != INVALID_HANDLE_VALUE) {
            BY_HANDLE_FILE_INFORMATION info{};
            BOOL ok = GetFileInformationByHandle(h, &info);
            CloseHandle(h);
            if (ok) return ((static_cast<uint64_t>(info.nFileIndexHigh) << 32) | info.nFileIndexLow) & kRefMask;
        }
    }
#else
    (void)src;
#endif
    // Slow path (also works for folders that were themselves deleted): match
    // the reconstructed path of every directory record.
    std::string want = NormalizeWinPath(folderPath);
    std::string wantNoDrive = want.size() >= 2 && want[1] == ':' ? want.substr(2) : want;
    if (wantNoDrive.empty() || wantNoDrive == "\\") return kRootDirRecord;
    for (const auto& kv : dirs) {
        std::string p = NormalizeWinPath(dirPath(kv.first));
        std::string pNoDrive = p.find('\\') != std::string::npos ? p.substr(p.find('\\')) : p;
        if (p == want || pNoDrive == wantNoDrive) return kv.first;
    }
    return kNpos;
}

// Shared by NTFS and FAT32 quick scans: verify file headers against the
// expected signature for the extension and write preview samples. Reads are
// sorted by LBA so the HDD head sweeps in one direction.
void VerifyAndCommit(RawDevice& dev, std::vector<Candidate>& local, SourceRun& src, ScanContext& ctx) {
    std::sort(local.begin(), local.end(), [](const Candidate& a, const Candidate& b) { return a.lba < b.lba; });
    ctx.BeginPhase();
    uint64_t idx = 0;
    std::vector<uint8_t> buf;
    for (auto& c : local) {
        if (ctx.ShouldStop()) break;
        ++idx;
        if (ctx.opts.verifyHeaders || ctx.samplingEnabled) {
            uint64_t want = ctx.samplingEnabled ? ctx.opts.sampleBytes : 64;
            ReadCandidateBytes(dev, c, want, buf);
            if (!buf.empty()) {
                bool zeroed = std::all_of(buf.begin(), buf.begin() + static_cast<std::ptrdiff_t>(std::min<size_t>(buf.size(), 512)),
                                          [](uint8_t b) { return b == 0; });
                const SigDef* found = IdentifySignature(buf.data(), buf.size());
                std::string expected = ExpectedSignatureFor(c.extension);
                if (found) c.signature = found->id;
                if (!expected.empty()) c.headerCheck = (found && expected == found->id) ? "match" : "mismatch";
                else if (zeroed) c.headerCheck = "mismatch";
                if (zeroed) c.notes.push_back("header_zeroed: first sector is all zeros (likely overwritten or wiped)");
                if (found && !expected.empty() && expected != found->id)
                    c.notes.push_back(std::string("content_looks_like_") + found->id);
                MaybeWriteSample(ctx, c, buf.data(), buf.size());
            }
        }
        ctx.Progress(src, "verify", idx, local.size(), false, "items");
    }
    for (auto& c : local) ctx.Add(std::move(c), src);
    src.readErrors = dev.ReadErrors();
}

void ScanNtfs(RawDevice& dev, uint64_t volOffset, SourceRun& src, ScanContext& ctx, const std::string& idPrefix,
              uint64_t partitionAbsOffset) {
    NtfsVolume vol(dev, volOffset);
    std::string err;
    if (!vol.Init(&err)) {
        src.status = "failed";
        src.error = err;
        LogError("NTFS initialisation failed: " + err, src.desc.sourceId);
        return;
    }
    src.filesystem = "NTFS";
    src.clusterSize = vol.ClusterSize();
    src.totalBytes = std::max<uint64_t>(src.totalBytes, vol.VolumeBytes());
    const uint32_t cs = vol.ClusterSize();
    const uint32_t rs = vol.RecordSize();
    LogInfo("NTFS volume: cluster " + std::to_string(cs) + " B, MFT record " + std::to_string(rs) + " B, " +
                std::to_string(vol.RecordCount()) + " records", src.desc.sourceId);

    ctx.BeginPhase();
    if (vol.LoadBitmap()) LogInfo("$Bitmap loaded for overwrite analysis", src.desc.sourceId);
    else LogWarn("$Bitmap unavailable; overwrite ratios will be reported as unknown", src.desc.sourceId);

    // ---- Pass 1: sequential MFT sweep ---------------------------------------
    std::unordered_map<uint64_t, DirNode> dirs;
    std::vector<DeletedFile> deleted;
    const uint64_t total = vol.RecordCount();
    const uint64_t perChunk = std::max<uint64_t>(1, (4 * MiB) / rs);
    std::vector<uint8_t> chunk(static_cast<size_t>(perChunk * rs));
    uint64_t badRecords = 0, fixupErrors = 0;

    ctx.BeginPhase();
    for (uint64_t base = 0; base < total; base += perChunk) {
        if (ctx.ShouldStop()) break;
        uint64_t n = std::min(perChunk, total - base);
        // Read many records at once; ReadVirtual follows the (possibly
        // fragmented) $MFT runs transparently.
        if (!vol.ReadRecords(base, n, chunk)) LogWarn("Short read inside $MFT at record " + std::to_string(base), src.desc.sourceId);
        for (uint64_t i = 0; i < n; ++i) {
            uint64_t recNo = base + i;
            uint8_t* recPtr = chunk.data() + i * rs;
            ++src.mftRecordsScanned;
            MftRecord r;
            if (!ParseMftRecord(recPtr, rs, vol.TotalClusters(), r)) {
                if (std::memcmp(recPtr, "BAAD", 4) == 0) ++badRecords;
                continue;
            }
            if (!r.fixupOk) ++fixupErrors;
            if (r.baseRef != 0) continue;  // extension record; reached via $ATTRIBUTE_LIST
            const FileNameInfo* fn = r.BestName();
            if (!fn) continue;
            bool inUse = (r.flags & 0x01) != 0;
            bool isDir = (r.flags & 0x02) != 0;
            if (isDir) {
                DirNode d;
                d.parent = fn->parentRef & kRefMask;
                d.parentSeq = static_cast<uint16_t>(fn->parentRef >> 48);
                d.seq = r.sequence;
                d.inUse = inUse;
                d.name = fn->name;
                dirs[recNo] = std::move(d);
            } else if (!inUse) {
                // In-use flag (0x01) cleared => deleted file record.
                ++src.deletedEntriesFound;
                DeletedFile df;
                df.recNo = recNo;
                df.name = *fn;
                df.rec = std::move(r);
                deleted.push_back(std::move(df));
            }
        }
        src.bytesScanned = std::min<uint64_t>((base + n) * rs, total * rs);
        ctx.Progress(src, "mft", base + n, total, false, "records");
    }
    ctx.Progress(src, "mft", total, total, true, "records");
    if (badRecords) LogWarn(std::to_string(badRecords) + " MFT records marked BAAD (multi-sector transfer error)", src.desc.sourceId);
    if (fixupErrors) LogWarn(std::to_string(fixupErrors) + " MFT records had update-sequence mismatches (torn writes)", src.desc.sourceId);

    // ---- Path reconstruction --------------------------------------------------
    const std::string rootPrefix = !src.desc.letter.empty() ? src.desc.letter : "[" + src.desc.sourceId + "]";
    std::unordered_map<uint64_t, std::pair<std::string, bool>> pathCache;  // dir -> (path, orphaned)
    std::function<std::pair<std::string, bool>(uint64_t, uint16_t, int)> resolveDir =
        [&](uint64_t rec, uint16_t refSeq, int depth) -> std::pair<std::string, bool> {
        if (rec == kRootDirRecord) return {rootPrefix, false};
        if (depth > 256) return {rootPrefix + "\\$Orphan", true};
        auto it = dirs.find(rec);
        if (it == dirs.end()) return {rootPrefix + "\\$Orphan", true};
        const DirNode& d = it->second;
        // Sequence check: a live directory with a different sequence number
        // means the record was reused; NTFS bumps the sequence on deletion so
        // a deleted parent may be refSeq + 1.
        if (refSeq != 0 && d.seq != refSeq && !(!d.inUse && d.seq == static_cast<uint16_t>(refSeq + 1)))
            return {rootPrefix + "\\$Orphan", true};
        auto c = pathCache.find(rec);
        if (c != pathCache.end()) return c->second;
        auto parent = resolveDir(d.parent, d.parentSeq, depth + 1);
        std::pair<std::string, bool> res{parent.first + "\\" + d.name, parent.second};
        pathCache[rec] = res;
        return res;
    };
    auto dirPathOnly = [&](uint64_t rec) { return resolveDir(rec, 0, 0).first; };

    // ---- Folder filter ------------------------------------------------------
    uint64_t folderRec = kNpos;
    if (ctx.opts.scope == "folder") {
        folderRec = ResolveFolderRecord(ctx.opts.folderPath, src.desc, dirs, dirPathOnly);
        if (folderRec == kNpos) {
            src.status = "failed";
            src.error = "Folder not found in the MFT: " + ctx.opts.folderPath;
            LogError(src.error, src.desc.sourceId);
            return;
        }
        LogInfo("Folder filter: MFT record " + std::to_string(folderRec) + " (" + ctx.opts.folderPath + ")", src.desc.sourceId);
    }
    auto isUnderFolder = [&](uint64_t parent) {
        uint64_t cur = parent;
        for (int depth = 0; depth < 256; ++depth) {
            if (cur == folderRec) return true;
            if (cur == kRootDirRecord) return folderRec == kRootDirRecord;
            auto it = dirs.find(cur);
            if (it == dirs.end()) return false;
            cur = it->second.parent;
        }
        return false;
    };

    // ---- Build candidates ----------------------------------------------------
    std::vector<Candidate> local;
    for (auto& df : deleted) {
        if (ctx.ShouldStop()) break;
        uint64_t parent = df.name.parentRef & kRefMask;
        if (folderRec != kNpos && !isUnderFolder(parent)) continue;
        std::string ext = ExtensionOf(df.name.name);
        if (!ctx.TypeAllowed(ext)) continue;

        MftRecord& r = df.rec;
        DataAttr data = r.data;

        // Data attribute spread over extension records via $ATTRIBUTE_LIST.
        if ((!data.present || (!data.resident && data.realSize == 0)) && r.hasAttrList) {
            std::vector<uint8_t> list = r.attrListData;
            if (!r.attrListResident && r.attrListSize > 0 && r.attrListSize < 16 * MiB) {
                list.resize(static_cast<size_t>(r.attrListSize));
                vol.ReadVirtual(r.attrListRuns, 0, list.size(), list.data());
            }
            DataAttr merged;
            std::set<uint64_t> visited;
            for (size_t o = 0; o + 0x1A <= list.size();) {
                uint32_t type = LE32(list.data() + o);
                uint16_t elen = LE16(list.data() + o + 4);
                uint8_t enameLen = list[o + 6];
                uint64_t segRef = LE64(list.data() + o + 0x10) & kRefMask;
                if (elen < 0x1A) break;
                if (type == kAttrData && enameLen == 0 && !visited.count(segRef)) {
                    visited.insert(segRef);
                    std::vector<uint8_t> extRec;
                    MftRecord er;
                    if (vol.ReadRecord(segRef, extRec) && ParseMftRecord(extRec.data(), extRec.size(), vol.TotalClusters(), er) && er.data.present) {
                        if (er.data.resident) merged = er.data;
                        else {
                            merged.present = true;
                            merged.flags |= er.data.flags;
                            if (er.data.realSize) { merged.realSize = er.data.realSize; merged.initSize = er.data.initSize; }
                            merged.runs.insert(merged.runs.end(), er.data.runs.begin(), er.data.runs.end());
                            merged.runsValid = merged.runsValid && er.data.runsValid;
                        }
                    }
                }
                o += elen;
            }
            if (merged.present) {
                std::sort(merged.runs.begin(), merged.runs.end(), [](const NtfsRun& a, const NtfsRun& b) { return a.vcn < b.vcn; });
                data = merged;
            }
        }
        if (!data.present) continue;
        uint64_t size = data.resident ? data.residentData.size() : data.realSize;
        if (size == 0 && !ctx.opts.includeZeroLength) continue;

        Candidate c;
        c.id = idPrefix + "-mft-" + std::to_string(df.recNo);
        c.sourceId = idPrefix;
        c.sourceDevice = src.desc.devicePath;
        c.sourceKind = SourceKindName(src.desc.kind);
        c.method = "mft";
        c.fileName = df.name.name;
        auto parentPath = resolveDir(parent, static_cast<uint16_t>(df.name.parentRef >> 48), 0);
        c.originalPath = parentPath.first + "\\" + df.name.name;
        c.pathOrphaned = parentPath.second;
        c.extension = ext;
        c.mimeType = MimeForExtension(ext);
        c.sizeBytes = size;
        c.sectorSize = vol.BytesPerSector();
        c.hasMft = true;
        c.recordNumber = df.recNo;
        c.sequence = r.sequence;
        c.parentRecord = parent;
        c.recordFlags = r.flags;
        c.fileAttributes = r.siAttributes;
        c.compressed = (data.flags & 0x0001) || (r.siAttributes & 0x800);
        c.encrypted = (data.flags & 0x4000) || (r.siAttributes & 0x4000);
        c.sparseFile = (data.flags & 0x8000) || (r.siAttributes & 0x200);
        c.fixupError = !r.fixupOk;
        c.sizeSource = "metadata";
        if (r.hasStdInfo) {
            c.created = FileTimeToIso(r.siCreated);
            c.modified = FileTimeToIso(r.siModified);
            c.mftModified = FileTimeToIso(r.siMftModified);
            c.accessed = FileTimeToIso(r.siAccessed);
        } else {
            c.created = FileTimeToIso(df.name.created);
            c.modified = FileTimeToIso(df.name.modified);
            c.mftModified = FileTimeToIso(df.name.mftModified);
            c.accessed = FileTimeToIso(df.name.accessed);
        }

        if (data.resident) {
            c.residentDataB64 = Base64Encode(data.residentData.data(), data.residentData.size());
            c.notes.push_back("resident_data: payload stored inside the MFT record");
            c.allocatedRatio = 0.0;
        } else {
            if (!data.runsValid) c.notes.push_back("data_runs_partially_invalid");
            uint64_t remaining = size;
            for (const auto& run : data.runs) {
                if (remaining == 0) break;
                uint64_t bytes = std::min<uint64_t>(run.length * cs, remaining);
                Extent e;
                e.length = bytes;
                e.sparse = run.sparse;
                e.offset = run.sparse ? 0 : volOffset + run.lcn * cs;
                // Merge physically contiguous runs.
                if (!c.extents.empty() && !e.sparse && !c.extents.back().sparse &&
                    c.extents.back().offset + c.extents.back().length == e.offset)
                    c.extents.back().length += bytes;
                else
                    c.extents.push_back(e);
                remaining -= bytes;
            }
            if (remaining > 0) {
                c.truncated = true;
                c.notes.push_back("runlist_shorter_than_file_size");
            }
            if (c.extents.empty()) continue;
            c.allocatedRatio = vol.AllocatedRatio(data.runs, size);
        }
        if (c.compressed) c.notes.push_back("ntfs_compressed: clusters hold LZNT1-compressed data");
        if (c.encrypted) c.notes.push_back("efs_encrypted: content requires the original user's EFS key");
        if (c.pathOrphaned) c.notes.push_back("parent_directory_reused_or_missing");

        uint64_t firstOff = volOffset;
        for (const auto& e : c.extents) if (!e.sparse) { firstOff = e.offset; break; }
        c.lba = firstOff / c.sectorSize;
        c.absoluteLba = (partitionAbsOffset + (firstOff - volOffset)) / c.sectorSize;
        local.push_back(std::move(c));
    }

    VerifyAndCommit(dev, local, src, ctx);
}

}  // namespace

// =============================================================================
// 7. FAT32 Quick Scan
// =============================================================================
namespace {

class Fat32Volume {
public:
    Fat32Volume(RawDevice& dev, uint64_t volumeOffset) : dev_(dev), off_(volumeOffset) {}

    bool Init(std::string* error) {
        uint8_t bs[512];
        if (dev_.Read(off_, 512, bs) < 512 || DetectFs(bs) != FsType::FAT32) {
            if (error) *error = "FAT32 boot sector not found";
            return false;
        }
        bps_ = LE16(bs + 0x0B);
        spc_ = bs[0x0D];
        reserved_ = LE16(bs + 0x0E);
        numFats_ = bs[0x10];
        uint32_t tot16 = LE16(bs + 0x13);
        totalSectors_ = tot16 ? tot16 : LE32(bs + 0x20);
        fatSz_ = LE32(bs + 0x24);
        rootCluster_ = LE32(bs + 0x2C);
        if (spc_ == 0 || (spc_ & (spc_ - 1)) != 0 || numFats_ == 0 || fatSz_ == 0 || reserved_ == 0) {
            if (error) *error = "Invalid FAT32 BPB";
            return false;
        }
        clusterSize_ = static_cast<uint32_t>(bps_) * spc_;
        firstDataSector_ = reserved_ + static_cast<uint64_t>(numFats_) * fatSz_;
        if (totalSectors_ <= firstDataSector_) {
            if (error) *error = "FAT32 data region is empty";
            return false;
        }
        clusterCount_ = static_cast<uint32_t>((totalSectors_ - firstDataSector_) / spc_);
        return true;
    }

    bool LoadFat() {
        uint64_t bytes = std::min<uint64_t>(static_cast<uint64_t>(fatSz_) * bps_, (static_cast<uint64_t>(clusterCount_) + 2) * 4);
        if (bytes > 512 * MiB) return false;
        std::vector<uint8_t> raw(static_cast<size_t>(bytes));
        if (dev_.Read(off_ + static_cast<uint64_t>(reserved_) * bps_, raw.size(), raw.data()) < raw.size()) return false;
        fat_.resize(raw.size() / 4);
        for (size_t i = 0; i < fat_.size(); ++i) fat_[i] = LE32(raw.data() + i * 4) & 0x0FFFFFFF;
        return true;
    }

    bool FatLoaded() const { return !fat_.empty(); }
    bool ValidCluster(uint32_t c) const { return c >= 2 && c < clusterCount_ + 2; }
    uint32_t FatEntry(uint32_t c) const { return c < fat_.size() ? fat_[c] : 0; }
    uint64_t ClusterOffset(uint32_t c) const { return off_ + (firstDataSector_ + static_cast<uint64_t>(c - 2) * spc_) * bps_; }

    std::vector<uint32_t> Chain(uint32_t start, size_t limit) const {
        std::vector<uint32_t> out;
        std::unordered_set<uint32_t> seen;
        uint32_t c = start;
        while (ValidCluster(c) && out.size() < limit && !seen.count(c)) {
            out.push_back(c);
            seen.insert(c);
            if (fat_.empty()) break;
            uint32_t next = FatEntry(c);
            if (next >= 0x0FFFFFF8 || next == 0x0FFFFFF7 || next == 0) break;
            c = next;
        }
        return out;
    }

    uint32_t ClusterSize() const { return clusterSize_; }
    uint32_t BytesPerSector() const { return bps_; }
    uint32_t RootCluster() const { return rootCluster_; }
    uint32_t ClusterCount() const { return clusterCount_; }
    uint64_t VolumeBytes() const { return totalSectors_ * bps_; }

private:
    RawDevice& dev_;
    uint64_t off_;
    uint16_t bps_ = 512;
    uint8_t spc_ = 8;
    uint16_t reserved_ = 32;
    uint8_t numFats_ = 2;
    uint64_t totalSectors_ = 0;
    uint32_t fatSz_ = 0, rootCluster_ = 2, clusterSize_ = 4096, clusterCount_ = 0;
    uint64_t firstDataSector_ = 0;
    std::vector<uint32_t> fat_;
};

uint8_t LfnChecksum(const uint8_t* name11) {
    uint8_t sum = 0;
    for (int i = 0; i < 11; ++i) sum = static_cast<uint8_t>(((sum & 1) << 7) + (sum >> 1) + name11[i]);
    return sum;
}

struct LfnPart {
    uint8_t checksum = 0;
    std::vector<uint16_t> units;
};

void ScanFat32(RawDevice& dev, uint64_t volOffset, SourceRun& src, ScanContext& ctx, const std::string& idPrefix,
               uint64_t partitionAbsOffset) {
    Fat32Volume vol(dev, volOffset);
    std::string err;
    if (!vol.Init(&err)) {
        src.status = "failed";
        src.error = err;
        LogError(err, src.desc.sourceId);
        return;
    }
    src.filesystem = "FAT32";
    src.clusterSize = vol.ClusterSize();
    src.totalBytes = std::max<uint64_t>(src.totalBytes, vol.VolumeBytes());
    if (!vol.LoadFat()) LogWarn("FAT could not be loaded; overwrite analysis disabled and directory chains assumed contiguous", src.desc.sourceId);

    const uint32_t cs = vol.ClusterSize();
    const std::string rootPrefix = !src.desc.letter.empty() ? src.desc.letter : "[" + src.desc.sourceId + "]";
    std::string folderFilter;
    if (ctx.opts.scope == "folder") {
        folderFilter = NormalizeWinPath(ctx.opts.folderPath);
        if (folderFilter.size() >= 2 && folderFilter[1] == ':') folderFilter = folderFilter.substr(2);
    }

    struct DirJob {
        std::vector<uint32_t> clusters;
        std::string path;
        bool deleted = false;
        bool probe = false;  // deleted dir: follow contiguous free clusters
    };
    std::deque<DirJob> jobs;
    std::unordered_set<uint32_t> visited{vol.RootCluster()};
    jobs.push_back({vol.Chain(vol.RootCluster(), 1u << 20), rootPrefix, false, !vol.FatLoaded()});

    std::vector<Candidate> local;
    std::vector<uint8_t> buf(cs);
    uint64_t dirsDone = 0;
    ctx.BeginPhase();

    while (!jobs.empty() && !ctx.ShouldStop()) {
        DirJob job = std::move(jobs.front());
        jobs.pop_front();
        ++dirsDone;
        std::vector<LfnPart> lfn;
        bool end = false;
        for (size_t ci = 0; ci < job.clusters.size() && !end; ++ci) {
            uint32_t clus = job.clusters[ci];
            if (dev.Read(vol.ClusterOffset(clus), cs, buf.data()) < cs) break;
            src.bytesScanned += cs;
            if (ci > 0 && job.probe) {
                // Sanity-check a speculatively appended cluster of a deleted dir.
                uint8_t a = buf[11];
                bool plausible = buf[0] == 0x00 || buf[0] == 0xE5 || (buf[0] >= 0x20 && (a & 0xC0) == 0);
                if (!plausible) break;
            }
            for (uint32_t e = 0; e + 32 <= cs; e += 32) {
                const uint8_t* d = buf.data() + e;
                if (d[0] == 0x00) { end = true; break; }
                uint8_t attr = d[11];
                if (attr == 0x0F) {  // long-file-name fragment
                    LfnPart p;
                    p.checksum = d[13];
                    static const int offs[13] = {1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30};
                    for (int k : offs) {
                        uint16_t u = LE16(d + k);
                        if (u == 0x0000 || u == 0xFFFF) break;
                        p.units.push_back(u);
                    }
                    lfn.push_back(std::move(p));
                    continue;
                }
                bool del = d[0] == 0xE5;
                if ((attr & 0x08) && !(attr & 0x10)) { lfn.clear(); continue; }  // volume label
                if (d[0] == '.') { lfn.clear(); continue; }                      // "." and ".."

                // 8.3 short name
                uint8_t raw11[11];
                std::memcpy(raw11, d, 11);
                if (raw11[0] == 0x05) raw11[0] = 0xE5;  // KANJI lead-byte escape
                auto trim = [](std::string s) { while (!s.empty() && s.back() == ' ') s.pop_back(); return s; };
                std::string base = trim(std::string(reinterpret_cast<char*>(raw11), 8));
                std::string ex = trim(std::string(reinterpret_cast<char*>(raw11) + 8, 3));

                // Long name (fragments are stored last-part-first on disk).
                std::string longName;
                bool firstCharRecovered = false;
                if (!lfn.empty()) {
                    bool sameSum = std::all_of(lfn.begin(), lfn.end(), [&](const LfnPart& p) { return p.checksum == lfn[0].checksum; });
                    std::vector<uint16_t> units;
                    for (auto it = lfn.rbegin(); it != lfn.rend(); ++it) units.insert(units.end(), it->units.begin(), it->units.end());
                    bool ok = false;
                    if (sameSum && !units.empty()) {
                        if (!del) {
                            ok = LfnChecksum(raw11) == lfn[0].checksum;
                        } else {
                            // The first byte was overwritten with 0xE5; try the
                            // upper-cased first LFN character to recover it.
                            uint16_t u0 = units[0];
                            std::vector<uint8_t> tries;
                            if (u0 < 0x80) tries.push_back(static_cast<uint8_t>(std::toupper(u0)));
                            tries.push_back('_');
                            for (uint8_t t : tries) {
                                uint8_t probe[11];
                                std::memcpy(probe, raw11, 11);
                                probe[0] = t;
                                if (LfnChecksum(probe) == lfn[0].checksum) {
                                    raw11[0] = t;
                                    base = trim(std::string(reinterpret_cast<char*>(raw11), 8));
                                    firstCharRecovered = true;
                                    ok = true;
                                    break;
                                }
                            }
                            if (!ok) ok = true;  // deleted LFN with consistent checksums is still the best name we have
                        }
                    }
                    if (ok) longName = Utf16UnitsToUtf8(units.data(), units.size());
                }
                lfn.clear();

                std::string shortName = base;
                if (del && !firstCharRecovered && !shortName.empty()) shortName[0] = '_';
                if (!ex.empty()) shortName += "." + ex;
                std::string name = longName.empty() ? shortName : longName;
                if (name.empty()) continue;

                uint32_t firstCluster = (static_cast<uint32_t>(LE16(d + 20)) << 16) | LE16(d + 26);
                uint32_t size = LE32(d + 28);
                uint64_t entryOffset = vol.ClusterOffset(clus) + e;
                std::string fullPath = job.path + "\\" + name;

                if (attr & 0x10) {
                    if (vol.ValidCluster(firstCluster) && !visited.count(firstCluster)) {
                        visited.insert(firstCluster);
                        DirJob j;
                        j.path = fullPath;
                        j.deleted = del || job.deleted;
                        j.probe = del || !vol.FatLoaded();
                        j.clusters = (!del && vol.FatLoaded()) ? vol.Chain(firstCluster, 1u << 16) : std::vector<uint32_t>{firstCluster};
                        jobs.push_back(std::move(j));
                    }
                    continue;
                }
                if (!del) continue;

                ++src.deletedEntriesFound;
                std::string ext = ExtensionOf(name);
                if (!ctx.TypeAllowed(ext)) continue;
                if (!folderFilter.empty()) {
                    std::string p = NormalizeWinPath(fullPath);
                    std::string pNoDrive = p.find('\\') != std::string::npos ? p.substr(p.find('\\')) : p;
                    if (pNoDrive.compare(0, folderFilter.size() + 1, folderFilter + "\\") != 0) continue;
                }
                if (size == 0 && !ctx.opts.includeZeroLength) continue;
                if (!vol.ValidCluster(firstCluster)) continue;  // start cluster wiped: nothing to locate

                Candidate c;
                c.id = idPrefix + "-fat-" + HexU64(entryOffset);
                c.sourceId = idPrefix;
                c.sourceDevice = src.desc.devicePath;
                c.sourceKind = SourceKindName(src.desc.kind);
                c.method = "fat";
                c.fileName = name;
                c.originalPath = fullPath;
                c.extension = ext;
                c.mimeType = MimeForExtension(ext);
                c.sizeBytes = size;
                c.sectorSize = vol.BytesPerSector();
                c.sizeSource = "metadata";
                c.created = FatDateTimeToIso(LE16(d + 16), LE16(d + 14));
                c.modified = FatDateTimeToIso(LE16(d + 24), LE16(d + 22));
                c.accessed = FatDateTimeToIso(LE16(d + 18), 0);

                // FAT chains are zeroed on deletion: assume contiguous allocation.
                uint64_t need = (static_cast<uint64_t>(size) + cs - 1) / cs;
                uint64_t avail = static_cast<uint64_t>(vol.ClusterCount()) + 2 - firstCluster;
                if (need > avail) { need = avail; c.truncated = true; }
                Extent ext0;
                ext0.offset = vol.ClusterOffset(firstCluster);
                ext0.length = std::min<uint64_t>(size, need * cs);
                c.extents.push_back(ext0);
                if (need > 1) c.notes.push_back("fat_contiguity_assumed: FAT chain is cleared on delete; fragmented files will be corrupt");
                if (vol.FatLoaded()) {
                    uint64_t alloc = 0;
                    for (uint64_t k = 0; k < need; ++k) alloc += vol.FatEntry(static_cast<uint32_t>(firstCluster + k)) != 0 ? 1 : 0;
                    c.allocatedRatio = need ? static_cast<double>(alloc) / static_cast<double>(need) : 0.0;
                }
                if (LE16(d + 20) == 0 && vol.ClusterCount() > 0xFFFF)
                    c.notes.push_back("start_cluster_high_word_zero: location may be inaccurate on large volumes");
                if (del && !longName.empty() && !firstCharRecovered) c.notes.push_back("lfn_checksum_unverified");
                if (job.deleted) c.notes.push_back("parent_directory_deleted");
                c.lba = ext0.offset / c.sectorSize;
                c.absoluteLba = (partitionAbsOffset + (ext0.offset - volOffset)) / c.sectorSize;
                local.push_back(std::move(c));
            }
            // Deleted directories have no FAT chain left: probe the next
            // contiguous cluster while it is free.
            if (job.probe && !end && ci + 1 == job.clusters.size() && job.clusters.size() < 64) {
                uint32_t next = job.clusters.back() + 1;
                if (vol.ValidCluster(next) && (!vol.FatLoaded() || vol.FatEntry(next) == 0)) job.clusters.push_back(next);
            }
        }
        ctx.Progress(src, "fat_directories", dirsDone, dirsDone + jobs.size(), false, "directories");
    }
    ctx.Progress(src, "fat_directories", dirsDone, dirsDone, true, "directories");
    VerifyAndCommit(dev, local, src, ctx);
}

}  // namespace

// =============================================================================
// 8. Deep Scan: sector-by-sector signature carving
// =============================================================================
namespace {

// Optional allocation oracle used to (a) restrict carving to free clusters and
// (b) annotate carved hits with how much of their range is currently allocated.
struct AllocationMap {
    std::function<bool(uint64_t)> allocated;  // device byte offset -> allocated?
    uint64_t granularity = 4096;              // cluster size
    bool valid() const { return static_cast<bool>(allocated); }
};

AllocationMap BuildAllocationMap(RawDevice& dev, uint64_t volOffset, std::shared_ptr<void>& keepAlive) {
    AllocationMap map;
    uint8_t bs[512];
    if (dev.Read(volOffset, 512, bs) < 512) return map;
    FsType fs = DetectFs(bs);
    if (fs == FsType::NTFS) {
        auto vol = std::make_shared<NtfsVolume>(dev, volOffset);
        std::string err;
        if (!vol->Init(&err) || !vol->LoadBitmap()) return map;
        keepAlive = vol;
        uint64_t cs = vol->ClusterSize();
        map.granularity = cs;
        map.allocated = [vol, volOffset, cs](uint64_t off) {
            if (off < volOffset) return true;
            return vol->ClusterAllocated((off - volOffset) / cs);
        };
    } else if (fs == FsType::FAT32) {
        auto vol = std::make_shared<Fat32Volume>(dev, volOffset);
        std::string err;
        if (!vol->Init(&err) || !vol->LoadFat()) return map;
        keepAlive = vol;
        uint64_t cs = vol->ClusterSize();
        uint64_t dataStart = vol->ClusterOffset(2);
        map.granularity = cs;
        map.allocated = [vol, dataStart, cs](uint64_t off) {
            if (off < dataStart) return true;  // boot/FAT region
            uint64_t c = (off - dataStart) / cs + 2;
            if (c > 0xFFFFFFFFULL || !vol->ValidCluster(static_cast<uint32_t>(c))) return true;
            return vol->FatEntry(static_cast<uint32_t>(c)) != 0;
        };
    }
    return map;
}

std::string PartitionHintFor(const SourceRun& src, uint64_t deviceOffset) {
    if (!src.desc.letter.empty()) return src.desc.letter;
    for (const auto& p : src.partitions)
        if (deviceOffset >= p.offset && deviceOffset < p.offset + p.length)
            return "Partition " + std::to_string(p.number) + " (" + p.label + ")";
    return src.partitions.empty() ? "" : "Unpartitioned space";
}

void CarveRegion(RawDevice& dev, uint64_t start, uint64_t end, SourceRun& src, ScanContext& ctx, uint64_t deviceAbsBase,
                 const AllocationMap& alloc) {
    const uint32_t ss = dev.SectorSize();
    const uint64_t step = ctx.opts.sectorAligned ? ss : 1;
    const size_t chunkSize = 4 * MiB;
    AlignedBuffer buf(chunkSize);

    // First-byte dispatch table for offset-0 signatures; offset signatures
    // (e.g. ISO-BMFF "ftyp" at +4) are checked separately.
    std::array<std::vector<const SigDef*>, 256> dispatch;
    std::vector<const SigDef*> offsetSigs;
    for (const auto& s : Signatures()) {
        bool wanted = ctx.opts.typeFilter.empty();
        for (const auto& e : s.extensions) if (ctx.opts.typeFilter.count(e)) wanted = true;
        if (!wanted) continue;
        if (s.magicOffset == 0) dispatch[s.magic[0]].push_back(&s);
        else offsetSigs.push_back(&s);
    }

    const bool restrict = ctx.opts.unallocatedOnly && alloc.valid();
    if (ctx.opts.unallocatedOnly && !alloc.valid())
        LogWarn("Unallocated-only carving requested but no allocation bitmap is available; scanning all sectors.", src.desc.sourceId);

    uint64_t skipUntil = 0;
    uint8_t tmp[32];
    ctx.BeginPhase();
    for (uint64_t pos = start; pos < end; pos += chunkSize) {
        if (ctx.ShouldStop()) break;
        size_t want = static_cast<size_t>(std::min<uint64_t>(chunkSize, end - pos));
        size_t got = dev.ReadAligned(pos, want, buf.data());
        if (got == 0) break;
        for (size_t off = 0; off < got; off += static_cast<size_t>(step)) {
            uint64_t abs = pos + off;
            if (abs < skipUntil) {
                // Jump straight to the end of the file we just carved.
                uint64_t jump = skipUntil - abs;
                jump = ((jump + step - 1) / step) * step;
                if (off + jump >= got) break;
                off += static_cast<size_t>(jump) - static_cast<size_t>(step);
                continue;
            }
            if (restrict && alloc.allocated(abs)) {
                uint64_t g = alloc.granularity;
                uint64_t nextBoundary = ((abs / g) + 1) * g;
                uint64_t adv = nextBoundary - abs;
                if (adv > step) { off += static_cast<size_t>(adv - step); }
                continue;
            }
            const uint8_t* p = buf.data() + off;
            size_t availInBuf = got - off;
            const SigDef* hit = nullptr;
            auto matches = [&](const SigDef* s) {
                size_t need = s->magicOffset + s->magic.size();
                const uint8_t* q = p;
                if (need > availInBuf) {
                    if (dev.Read(abs, need, tmp) < need) return false;
                    q = tmp;
                }
                return std::memcmp(q + s->magicOffset, s->magic.data(), s->magic.size()) == 0;
            };
            for (const SigDef* s : dispatch[p[0]]) if (matches(s)) { hit = s; break; }
            if (!hit) for (const SigDef* s : offsetSigs) if (matches(s)) { hit = s; break; }
            if (!hit) continue;

            ByteSource source(dev, abs, end);
            CarveResult r;
            if (!hit->parse(source, hit->maxSize, r) || r.size < ctx.opts.minCarveSize) continue;
            std::string ext = r.ext.empty() ? hit->ext : r.ext;
            if (!ctx.TypeAllowed(ext)) continue;

            Candidate c;
            c.id = src.desc.sourceId + "-carve-" + HexU64(abs);
            c.sourceId = src.desc.sourceId;
            c.sourceDevice = src.desc.devicePath;
            c.sourceKind = SourceKindName(src.desc.kind);
            c.method = "carve";
            c.extension = ext;
            c.signature = hit->id;
            c.mimeType = r.mime.empty() ? hit->mime : r.mime;
            c.sizeBytes = r.size;
            c.sectorSize = ss;
            c.lba = abs / ss;
            c.absoluteLba = (deviceAbsBase + abs) / ss;
            c.extents.push_back({abs, r.size, false});
            c.headerCheck = "match";
            c.footerFound = r.footerFound;
            c.sizeEstimated = r.sizeEstimated;
            c.sizeSource = r.sizeSource;
            c.truncated = r.truncated;
            c.partitionHint = PartitionHintFor(src, abs);
            if (!r.note.empty()) c.notes.push_back(r.note);
            c.notes.push_back("carved: original file name and folder are not stored in file content");
            if (alloc.valid()) {
                uint64_t g = alloc.granularity, total = 0, used = 0;
                for (uint64_t o = abs; o < abs + r.size && total < (1u << 20); o += g, ++total) used += alloc.allocated(o) ? 1 : 0;
                c.allocatedRatio = total ? static_cast<double>(used) / static_cast<double>(total) : -1.0;
                if (c.allocatedRatio > 0.99) c.notes.push_back("in_allocated_space: probably an existing (not deleted) file");
            }
            if (ctx.samplingEnabled) {
                std::vector<uint8_t> sample(static_cast<size_t>(std::min<uint64_t>(r.size, ctx.opts.sampleBytes)));
                sample.resize(source.Read(0, sample.size(), sample.data()));
                MaybeWriteSample(ctx, c, sample.data(), sample.size());
            }
            ctx.Add(std::move(c), src);
            if (ctx.ShouldStop()) break;

            // Exact-length hits are skipped over (avoids re-detecting embedded
            // thumbnails / members); estimated hits only skip their header.
            uint64_t skip = (r.footerFound && !r.sizeEstimated) ? r.size : step;
            skipUntil = abs + ((skip + step - 1) / step) * step;
        }
        src.bytesScanned = std::min<uint64_t>(pos + got, end) - start;
        src.readErrors = dev.ReadErrors();
        ctx.Progress(src, "carve", src.bytesScanned, end - start);
    }
    ctx.Progress(src, "carve", src.bytesScanned, end - start, true);
}

// =============================================================================
// 9. candidates.json export (streamed, then atomically renamed)
// =============================================================================
std::string ScanStatus(const ScanContext& ctx) {
    if (CancelRequested()) return "cancelled";
    bool anyOk = false, anyFail = false;
    for (const auto& s : ctx.sources) {
        if (s.status == "completed") anyOk = true;
        if (s.status == "failed" || s.status == "unsupported") anyFail = true;
    }
    if (!anyOk) return "failed";
    if (anyFail || ctx.limitReached) return "partial";
    return "completed";
}

bool WriteCandidatesFile(const ScanContext& ctx, const std::string& startedAt, const std::string& finishedAt, bool elevated,
                         std::string* error) {
    const ScanOptions& o = ctx.opts;
    JsonWriter head(true);
    head.BeginObject().KV("schema_version", kSchemaVersion);
    head.Key("engine").BeginObject().KV("name", kEngineName).KV("version", kEngineVersion)
#ifdef _WIN32
        .KV("platform", "windows-x64")
#else
        .KV("platform", "posix-image-test")
#endif
        .EndObject();
    head.Key("scan").BeginObject()
        .KV("scan_id", o.scanId)
        .KV("mode", o.mode)
        .KV("target_scope", o.scope)
        .KVnullable("folder_path", o.folderPath)
        .KV("started_at", startedAt)
        .KV("finished_at", finishedAt)
        .KV("status", ScanStatus(ctx))
        .KV("elevated", elevated);
    head.Key("source_specs").BeginArray();
    for (const auto& s : o.sourceSpecs) head.String(s);
    head.EndArray();
    head.Key("type_filter").BeginArray();
    for (const auto& t : o.typeFilter) head.String(t);
    head.EndArray();
    head.Key("options").BeginObject()
        .KV("sector_aligned", o.sectorAligned)
        .KV("unallocated_only", o.unallocatedOnly)
        .KV("verify_headers", o.verifyHeaders)
        .KVnullable("sample_dir", ctx.samplingEnabled ? o.sampleDir : "")
        .KVu("sample_bytes", o.sampleBytes)
        .KVu("max_candidates", o.maxCandidates)
        .EndObject();
    head.EndObject();

    head.Key("sources").BeginArray();
    uint64_t readErrors = 0;
    for (const auto& s : ctx.sources) {
        readErrors += s.readErrors;
        head.BeginObject()
            .KV("source_id", s.desc.sourceId)
            .KV("device_path", s.desc.devicePath)
            .KV("kind", SourceKindName(s.desc.kind))
            .KV("display_name", s.desc.displayName)
            .KVnullable("letter", s.desc.letter)
            .KVnullable("volume_guid_path", s.desc.volumeGuidPath)
            .KV("filesystem", s.filesystem)
            .KV("media_type", s.desc.mediaType)
            .KVu("sector_size", s.sectorSize)
            .KVu("cluster_size", s.clusterSize)
            .KVu("total_bytes", s.totalBytes)
            .KVu("bytes_scanned", s.bytesScanned)
            .KVu("partition_offset_bytes", s.desc.partitionOffsetBytes)
            .KVu("volume_offset_in_device", s.desc.volumeOffsetInDevice)
            .KVi("physical_drive_index", s.desc.physicalDriveIndex)
            .KVu("mft_records_scanned", s.mftRecordsScanned)
            .KVu("deleted_entries_found", s.deletedEntriesFound)
            .KVu("candidates", s.candidates)
            .KVu("read_errors", s.readErrors)
            .KV("status", s.status)
            .KVnullable("error", s.error)
            .KVd("elapsed_seconds", s.elapsedSeconds, 2)
            .EndObject();
    }
    head.EndArray();

    std::map<std::string, uint64_t> byMethod, byExt;
    uint64_t totalBytes = 0;
    for (const auto& c : ctx.candidates) {
        ++byMethod[c.method];
        ++byExt[c.extension.empty() ? "(none)" : c.extension];
        totalBytes += c.sizeBytes;
    }
    head.Key("statistics").BeginObject()
        .KVu("total_candidates", ctx.candidates.size())
        .KVu("total_bytes", totalBytes)
        .KVu("read_errors", readErrors)
        .KVu("samples_bytes_written", ctx.sampleBytesUsed);
    head.Key("by_method").BeginObject();
    for (const auto& kv : byMethod) head.KVu(kv.first, kv.second);
    head.EndObject();
    head.Key("by_extension").BeginObject();
    for (const auto& kv : byExt) head.KVu(kv.first, kv.second);
    head.EndObject();
    head.EndObject();
    head.Key("candidates").BeginArray();  // the writer is left open; candidates are streamed below

    std::string headStr = head.take();
    std::string tmpPath = o.outFile + ".tmp";
    {
        OutFile f;
        if (!f.CreateAlways(tmpPath)) {
            if (error) *error = "Cannot create " + tmpPath;
            return false;
        }
        bool ok = f.Write(headStr);
        for (size_t i = 0; i < ctx.candidates.size() && ok; ++i) {
            ok = f.Write(i == 0 ? "\n    " : ",\n    ") && f.Write(CandidateJson(ctx.candidates[i]));
        }
        ok = ok && f.Write("\n  ]\n}\n");
        f.Close();
        if (!ok) {
            if (error) *error = "Write failed for " + tmpPath;
            return false;
        }
    }
    if (!AtomicReplace(tmpPath, o.outFile)) {
        if (error) *error = "Cannot move " + tmpPath + " to " + o.outFile;
        return false;
    }
    return true;
}

}  // namespace

// =============================================================================
// 10. Scan orchestration
// =============================================================================
int RunScan(const ScanOptions& optsIn, const DriveManager& dm) {
    ScanOptions opts = optsIn;
    if (opts.scanId.empty()) opts.scanId = "scan-" + std::to_string(static_cast<long long>(MonotonicSeconds() * 1000));
    const bool deep = opts.mode == "deep";
    if (opts.scope == "folder" && deep) opts.unallocatedOnly = true;  // carving restricted to the folder's partition free space
    const std::string startedAt = NowIso();
    const double t0 = MonotonicSeconds();

    // ---- Resolve sources -----------------------------------------------------
    std::vector<SourceDescriptor> descs;
    std::string err;
    if (opts.scope == "all") {
        descs = dm.AllSources(deep, opts.allowNonHdd);
        for (const auto& spec : opts.sourceSpecs) {  // explicit extras (e.g. images) are appended
            auto d = dm.Resolve(spec, &err);
            if (d) descs.push_back(*d);
        }
    } else if (opts.scope == "folder") {
        if (opts.folderPath.size() < 2) {
            EventSink::Instance().Error("USAGE", "--folder is required for folder scope", true);
            return kExitUsage;
        }
        std::string spec = opts.sourceSpecs.empty() ? opts.folderPath.substr(0, 2) : opts.sourceSpecs[0];
        auto d = dm.Resolve(spec, &err);
        if (!d) {
            EventSink::Instance().Error("SOURCE_NOT_FOUND", err, true);
            return kExitSourceError;
        }
        descs.push_back(*d);
    } else {
        for (const auto& spec : opts.sourceSpecs) {
            auto d = dm.Resolve(spec, &err);
            if (!d) {
                EventSink::Instance().Error("SOURCE_NOT_FOUND", err, true);
                return kExitSourceError;
            }
            descs.push_back(*d);
        }
    }
    if (descs.empty()) {
        EventSink::Instance().Error("NO_SOURCES", "No scannable sources were found for the requested scope.", true);
        return kExitSourceError;
    }

    // ---- HDD-only policy -----------------------------------------------------
    if (opts.scope != "all" && !opts.allowNonHdd) {
        for (const auto& d : descs) {
            if (d.mediaType == "SSD") {
                EventSink::Instance().Error("NON_HDD_SOURCE",
                                            d.displayName + " is on a solid-state drive. TRIM erases deleted blocks, so recovery is "
                                            "unreliable. Re-run with --allow-non-hdd to scan anyway.", true);
                return kExitSourceError;
            }
        }
    }

    // ---- Elevation -----------------------------------------------------------
    ElevationStatus elev = SecurityGuard::CheckElevation();
    bool needsRaw = std::any_of(descs.begin(), descs.end(), [](const SourceDescriptor& d) { return d.kind != SourceKind::Image; });
    if (needsRaw && !elev.elevated) {
        EventSink::Instance().Error("NOT_ELEVATED",
                                    "Administrator rights are required to open raw disk handles. Restart the backend from an "
                                    "elevated (Run as administrator) terminal.", true);
        return kExitNotElevated;
    }

    ScanContext ctx(opts);
    for (const auto& d : descs) {
        SourceRun r;
        r.desc = d;
        ctx.sources.push_back(r);
    }

    // ---- Output / sample location guard ---------------------------------------
    {
        std::string outDir = opts.outFile.substr(0, opts.outFile.find_last_of("\\/") == std::string::npos ? 0 : opts.outFile.find_last_of("\\/"));
        auto chk = SecurityGuard::ValidateDestination(outDir.empty() ? "." : outDir, descs, 0);
        if (!chk.safe) LogWarn("candidates.json is being written to a scanned volume; prefer a separate drive. " +
                               (chk.conflicts.empty() ? std::string() : chk.conflicts[0]));
    }
    if (!opts.sampleDir.empty()) {
        auto chk = SecurityGuard::ValidateDestination(opts.sampleDir, descs, 0);
        if (!chk.safe) {
            LogWarn("Preview samples disabled: " + (chk.conflicts.empty() ? chk.reasonCode : chk.conflicts[0]));
        } else if (!MakeDirs(opts.sampleDir)) {
            LogWarn("Preview samples disabled: cannot create " + opts.sampleDir);
        } else {
            ctx.samplingEnabled = true;
        }
    }

    {
        JsonWriter w;
        w.BeginObject().KV("event", "scan_started").KV("ts", startedAt).KV("scan_id", opts.scanId).KV("mode", opts.mode)
            .KV("target_scope", opts.scope).KV("elevated", elev.elevated).Key("sources").BeginArray();
        for (const auto& d : descs)
            w.BeginObject().KV("source_id", d.sourceId).KV("device_path", d.devicePath).KV("kind", SourceKindName(d.kind))
                .KV("display_name", d.displayName).KV("media_type", d.mediaType).EndObject();
        w.EndArray().EndObject();
        EventSink::Instance().Emit(w.str());
    }

    // ---- Per-source scan (sequential: parallel reads on one HDD thrash the heads)
    for (size_t i = 0; i < ctx.sources.size(); ++i) {
        if (ctx.ShouldStop()) break;
        ctx.currentSource = i;
        SourceRun& run = ctx.sources[i];
        const SourceDescriptor& d = run.desc;
        double s0 = MonotonicSeconds();
        RawDevice dev;
        std::string openErr;
        if (!dev.Open(d.devicePath, d.kind, &openErr)) {
            run.status = "failed";
            run.error = openErr;
            LogError(openErr, d.sourceId);
            continue;
        }
        run.sectorSize = dev.SectorSize();
        const uint64_t volOff = d.volumeOffsetInDevice;
        const uint64_t regionEnd = d.regionLengthBytes ? std::min(dev.Size(), volOff + d.regionLengthBytes) : dev.Size();
        run.totalBytes = regionEnd > volOff ? regionEnd - volOff : 0;
        const uint64_t deviceAbsBase = d.kind == SourceKind::Volume ? d.partitionOffsetBytes : 0;
        LogInfo("Scanning " + d.displayName + " (" + std::to_string(run.totalBytes) + " bytes, sector " +
                std::to_string(run.sectorSize) + " B)", d.sourceId);

        uint8_t bs[512] = {};
        dev.Read(volOff, 512, bs);
        FsType fs = DetectFs(bs);
        run.filesystem = FsName(fs);

        if (!deep) {
            auto scanFs = [&](FsType t, uint64_t off, const std::string& idPrefix) {
                if (t == FsType::NTFS) ScanNtfs(dev, off, run, ctx, idPrefix, deviceAbsBase + off);
                else if (t == FsType::FAT32) ScanFat32(dev, off, run, ctx, idPrefix, deviceAbsBase + off);
            };
            if (fs == FsType::NTFS || fs == FsType::FAT32) {
                scanFs(fs, volOff, d.sourceId);
            } else if (d.kind != SourceKind::Volume && volOff == 0) {
                run.partitions = ParsePartitionTable(dev);
                bool any = false;
                for (const auto& p : run.partitions) {
                    if (ctx.ShouldStop()) break;
                    uint8_t pbs[512] = {};
                    dev.Read(p.offset, 512, pbs);
                    FsType pfs = DetectFs(pbs);
                    if (pfs != FsType::NTFS && pfs != FsType::FAT32) {
                        LogInfo("Partition " + std::to_string(p.number) + " (" + p.label + "): " + FsName(pfs) + " - skipped by Quick Scan", d.sourceId);
                        continue;
                    }
                    any = true;
                    LogInfo("Partition " + std::to_string(p.number) + " at offset " + std::to_string(p.offset) + ": " + FsName(pfs), d.sourceId);
                    scanFs(pfs, p.offset, d.sourceId + "-p" + std::to_string(p.number));
                    run.filesystem = FsName(pfs);
                }
                if (!any) {
                    run.status = "unsupported";
                    run.error = "No NTFS or FAT32 file system found. Use Deep Scan (signature carving) for formatted or RAW media.";
                }
            } else {
                run.status = "unsupported";
                run.error = std::string("Quick Scan supports NTFS and FAT32; detected ") + FsName(fs) +
                            ". Use Deep Scan for formatted, RAW or corrupted partitions.";
            }
        } else {
            if (d.kind != SourceKind::Volume && volOff == 0) run.partitions = ParsePartitionTable(dev);
            std::shared_ptr<void> keep;
            AllocationMap alloc;
            if (fs == FsType::NTFS || fs == FsType::FAT32) {
                alloc = BuildAllocationMap(dev, volOff, keep);
                if (fs == FsType::NTFS) {
                    NtfsVolume nv(dev, volOff);
                    std::string e;
                    if (nv.Init(&e)) run.clusterSize = nv.ClusterSize();
                }
            }
            if (opts.scope == "folder")
                LogInfo("Folder scope + Deep Scan: carving is restricted to unallocated clusters of the partition containing " +
                        opts.folderPath + " (carved data carries no folder metadata).", d.sourceId);
            CarveRegion(dev, volOff, regionEnd, run, ctx, deviceAbsBase, alloc);
        }

        run.readErrors = dev.ReadErrors();
        run.elapsedSeconds = MonotonicSeconds() - s0;
        if (run.status == "pending") run.status = CancelRequested() ? "cancelled" : "completed";
        if (run.readErrors > 0)
            LogWarn(std::to_string(run.readErrors) + " unreadable sectors were zero-filled. The drive may be failing - image it before further use.", d.sourceId);
        JsonWriter w;
        w.BeginObject().KV("event", "source_done").KV("ts", NowIso()).KV("source_id", d.sourceId).KV("status", run.status)
            .KVnullable("error", run.error).KVu("candidates", run.candidates).KVu("read_errors", run.readErrors)
            .KVd("elapsed_seconds", run.elapsedSeconds, 2).EndObject();
        EventSink::Instance().Emit(w.str());
    }
    ctx.currentSource = ctx.sources.size();

    std::string writeErr;
    bool written = WriteCandidatesFile(ctx, startedAt, NowIso(), elev.elevated, &writeErr);
    if (!written) {
        EventSink::Instance().Error("OUTPUT_WRITE_FAILED", writeErr, true);
        return kExitInternal;
    }
    std::string status = ScanStatus(ctx);
    JsonWriter w;
    w.BeginObject().KV("event", "done").KV("ts", NowIso()).KV("command", "scan").KV("scan_id", opts.scanId)
        .KV("status", status).KV("candidates_file", opts.outFile).KVu("candidates", ctx.candidates.size())
        .KVd("elapsed_seconds", MonotonicSeconds() - t0, 2).EndObject();
    EventSink::Instance().Emit(w.str());
    if (status == "cancelled") return kExitCancelled;
    if (status == "failed") return kExitSourceError;
    return kExitOk;
}

// =============================================================================
// 11. Recovery: manifest-driven extraction to a guarded destination
// =============================================================================
int RunRecover(const std::string& manifestPath, const std::string& cancelFile, const DriveManager& dm) {
    JsonValue manifest;
    try {
        manifest = JsonParser(ReadWholeFile(manifestPath)).Parse();
    } catch (const std::exception& e) {
        EventSink::Instance().Error("BAD_MANIFEST", e.what(), true);
        return kExitUsage;
    }
    const std::string dest = manifest.str("destination");
    const bool preservePaths = manifest.boolean("preserve_paths", true);
    const JsonValue* items = manifest.get("items");
    if (dest.empty() || !items || items->type != JsonValue::Type::Array) {
        EventSink::Instance().Error("BAD_MANIFEST", "Manifest requires 'destination' and an 'items' array.", true);
        return kExitUsage;
    }

    // ---- Collect sources & guard the destination -----------------------------
    std::map<std::string, SourceDescriptor> sources;
    uint64_t required = 0;
    for (const auto& it : items->arr) {
        required += it.u64("size_bytes");
        std::string devPath = it.str("source_device");
        if (devPath.empty() || sources.count(devPath)) continue;
        std::string kind = it.str("source_kind", "volume");
        std::string err;
        std::optional<SourceDescriptor> sd = dm.Resolve(kind == "image" ? "image:" + devPath : devPath, &err);
        SourceDescriptor d;
        if (sd) d = *sd;
        else {
            d.devicePath = devPath;
            d.kind = kind == "image" ? SourceKind::Image : kind == "physical" ? SourceKind::Physical : SourceKind::Volume;
        }
        d.devicePath = devPath;  // always read exactly what the scan read
        sources[devPath] = d;
    }
    std::vector<SourceDescriptor> srcList;
    for (const auto& kv : sources) srcList.push_back(kv.second);

    bool needsRaw = std::any_of(srcList.begin(), srcList.end(), [](const SourceDescriptor& d) { return d.kind != SourceKind::Image; });
    if (needsRaw && !SecurityGuard::CheckElevation().elevated) {
        EventSink::Instance().Error("NOT_ELEVATED", "Administrator rights are required to read raw disk sectors.", true);
        return kExitNotElevated;
    }
    DestinationCheck chk = SecurityGuard::ValidateDestination(dest, srcList, required);
    EventSink::Instance().Emit("{\"event\":\"destination_check\",\"result\":" + SecurityGuard::DestinationJson(chk) + "}");
    if (!chk.safe) {
        EventSink::Instance().Error("UNSAFE_DESTINATION", chk.conflicts.empty() ? chk.reasonCode : chk.conflicts[0], true);
        return kExitUnsafeDestination;
    }
    if (!MakeDirs(dest)) {
        EventSink::Instance().Error("DESTINATION_CREATE_FAILED", "Cannot create destination directory " + dest, true);
        return kExitUnsafeDestination;
    }

    // ---- Extract ----------------------------------------------------------------
    std::map<std::string, std::unique_ptr<RawDevice>> devices;
    std::vector<uint8_t> buf(1 * MiB);
    uint64_t restored = 0, failed = 0, bytesDone = 0;
    double lastProgress = 0, lastCancelCheck = 0;
    bool cancelled = false;
    size_t index = 0;

    for (const auto& it : items->arr) {
        ++index;
        double now = MonotonicSeconds();
        if (!cancelFile.empty() && now - lastCancelCheck > 1.0) {
            lastCancelCheck = now;
            if (FileExists(cancelFile)) RequestCancel();
        }
        if (CancelRequested()) { cancelled = true; break; }

        const std::string id = it.str("id");
        const std::string devPath = it.str("source_device");
        const uint64_t size = it.u64("size_bytes");
        std::string ext = it.str("extension");
        std::string outName = SanitizeFileComponent(it.str("output_name", id + (ext.empty() ? "" : "." + ext)), id.empty() ? "recovered" : id);

        std::string relDir;
        if (preservePaths) {
            std::string rd = it.str("relative_dir");
            size_t s = 0;
            while (s <= rd.size()) {
                size_t e = rd.find_first_of("\\/", s);
                if (e == std::string::npos) e = rd.size();
                std::string comp = rd.substr(s, e - s);
                if (!comp.empty() && comp != "." && comp != "..") relDir = JoinPath(relDir, SanitizeFileComponent(comp, "_"));
                s = e + 1;
            }
        }
        std::string outDir = relDir.empty() ? dest : JoinPath(dest, relDir);

        auto report = [&](const std::string& status, const std::string& path, uint64_t written, uint64_t readErr, const std::string& error) {
            JsonWriter w;
            w.BeginObject().KV("event", "restore_item").KV("ts", NowIso()).KV("id", id).KV("status", status)
                .KVnullable("output_path", path).KVu("bytes_written", written).KVu("read_errors", readErr)
                .KVnullable("error", error).EndObject();
            EventSink::Instance().Emit(w.str());
        };

        if (!MakeDirs(outDir)) { ++failed; report("failed", "", 0, 0, "Cannot create directory " + outDir); continue; }

        // Unique output name: "name.ext", "name (1).ext", ...
        std::string outPath = JoinPath(outDir, outName);
        OutFile out;
        bool created = out.CreateNew(outPath);
        for (int n = 1; !created && n < 10000; ++n) {
            std::string stem = outName, e2 = ExtensionOf(outName);
            if (!e2.empty()) stem = outName.substr(0, outName.size() - e2.size() - 1);
            outPath = JoinPath(outDir, stem + " (" + std::to_string(n) + ")" + (e2.empty() ? "" : "." + e2));
            created = out.CreateNew(outPath);
        }
        if (!created) { ++failed; report("failed", "", 0, 0, "Cannot create output file in " + outDir); continue; }

        uint64_t written = 0, readErrBefore = 0, readErrAfter = 0;
        bool ok = true;
        std::string resident = it.str("resident_data_b64");
        if (!resident.empty()) {
            std::vector<uint8_t> data = Base64Decode(resident);
            if (data.size() > size) data.resize(static_cast<size_t>(size));
            ok = out.Write(data.data(), data.size());
            written = data.size();
            bytesDone += written;
        } else {
            auto& devPtr = devices[devPath];
            if (!devPtr) {
                devPtr = std::make_unique<RawDevice>();
                std::string err;
                if (!devPtr->Open(devPath, sources[devPath].kind, &err)) {
                    devPtr.reset();
                    devices.erase(devPath);
                    out.Close();
                    ++failed;
                    report("failed", outPath, 0, 0, err);
                    continue;
                }
            }
            RawDevice& dev = *devPtr;
            readErrBefore = dev.ReadErrors();
            const JsonValue* extents = it.get("extents");
            if (extents && extents->type == JsonValue::Type::Array) {
                for (const auto& e : extents->arr) {
                    if (!ok || written >= size) break;
                    uint64_t off = e.u64("offset"), len = std::min<uint64_t>(e.u64("length"), size - written);
                    bool sparse = e.boolean("sparse", false);
                    while (len > 0 && ok) {
                        size_t n = static_cast<size_t>(std::min<uint64_t>(len, buf.size()));
                        if (sparse) std::memset(buf.data(), 0, n);
                        else {
                            size_t got = dev.Read(off, n, buf.data());
                            if (got < n) std::memset(buf.data() + got, 0, n - got);
                        }
                        ok = out.Write(buf.data(), n);
                        written += n;
                        bytesDone += n;
                        off += n;
                        len -= n;
                        if (MonotonicSeconds() - lastProgress > 0.5) {
                            lastProgress = MonotonicSeconds();
                            JsonWriter w;
                            w.BeginObject().KV("event", "progress").KV("ts", NowIso()).KV("phase", "restore")
                                .KVu("bytes_done", bytesDone).KVu("bytes_total", required)
                                .KVd("percent", required ? 100.0 * static_cast<double>(bytesDone) / static_cast<double>(required) : 100.0, 2)
                                .KVu("items_done", index - 1).KVu("items_total", items->arr.size()).EndObject();
                            EventSink::Instance().Emit(w.str());
                        }
                    }
                }
            }
            readErrAfter = dev.ReadErrors();
        }
        out.Close();
        if (ok) {
            ++restored;
            report(written < size ? "partial" : "ok", outPath, written, readErrAfter - readErrBefore,
                   written < size ? "Extents shorter than file size" : "");
        } else {
            ++failed;
            report("failed", outPath, written, readErrAfter - readErrBefore, "Write error (destination full?)");
        }
    }

    JsonWriter w;
    w.BeginObject().KV("event", "done").KV("ts", NowIso()).KV("command", "recover")
        .KV("status", cancelled ? "cancelled" : (failed == 0 ? "completed" : (restored ? "partial" : "failed")))
        .KVu("restored", restored).KVu("failed", failed).KVu("bytes_written", bytesDone).KV("destination", dest).EndObject();
    EventSink::Instance().Emit(w.str());
    if (cancelled) return kExitCancelled;
    return (restored == 0 && failed > 0) ? kExitInternal : kExitOk;
}

}  // namespace re
