// =============================================================================
// win_recovery_engine.hpp  —  MODULE 3: Dual Scan Recovery Core
// -----------------------------------------------------------------------------
// Public surface of the recovery core:
//   * AlignedBuffer : VirtualAlloc-backed, page-aligned I/O buffer
//   * RawDevice     : CreateFileW(GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_WRITE,
//                     FILE_FLAG_NO_BUFFERING|FILE_FLAG_WRITE_THROUGH) wrapper
//                     with sector-aligned reads and bad-sector tolerance
//   * RunScan       : Quick (NTFS $MFT / FAT32 directory) or Deep (signature
//                     carving) scan -> candidates.json + NDJSON progress events
//   * RunRecover    : extracts candidates listed in a recover manifest to a
//                     guarded destination
// =============================================================================
#pragma once

#include <atomic>
#include <cstdint>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "engine_common.hpp"
#include "win_drive_manager.hpp"

namespace re {

// ---------------------------------------------------------------------------
// Aligned memory for unbuffered I/O
// ---------------------------------------------------------------------------
class AlignedBuffer {
public:
    explicit AlignedBuffer(size_t size);
    ~AlignedBuffer();
    AlignedBuffer(const AlignedBuffer&) = delete;
    AlignedBuffer& operator=(const AlignedBuffer&) = delete;
    uint8_t* data() { return ptr_; }
    const uint8_t* data() const { return ptr_; }
    size_t size() const { return size_; }

private:
    uint8_t* ptr_ = nullptr;
    size_t size_ = 0;
};

// ---------------------------------------------------------------------------
// Read-only raw block device (volume, physical drive or image file)
// ---------------------------------------------------------------------------
class RawDevice {
public:
    RawDevice();
    ~RawDevice();
    RawDevice(const RawDevice&) = delete;
    RawDevice& operator=(const RawDevice&) = delete;

    // `kind` controls flags: devices use FILE_FLAG_NO_BUFFERING, images do not.
    bool Open(const std::string& path, SourceKind kind, std::string* error);
    void Close();
    bool IsOpen() const;

    // Read `len` bytes at absolute byte offset `offset` into `out` (no alignment
    // requirements). Unreadable sectors are zero-filled and counted. Returns
    // the number of bytes actually available (short at end of device).
    size_t Read(uint64_t offset, size_t len, uint8_t* out);

    // Fast path: offset and len must be multiples of SectorSize() and `alignedOut`
    // must be sector aligned (e.g. an AlignedBuffer). Returns bytes read.
    size_t ReadAligned(uint64_t offset, size_t len, uint8_t* alignedOut);

    uint64_t Size() const { return size_; }
    uint32_t SectorSize() const { return sectorSize_; }
    uint64_t ReadErrors() const { return readErrors_; }
    const std::string& Path() const { return path_; }

private:
    bool RawRead(uint64_t offset, size_t len, uint8_t* out, size_t* got);

#ifdef _WIN32
    void* handle_ = nullptr;
#else
    int fd_ = -1;
#endif
    std::string path_;
    uint64_t size_ = 0;
    uint32_t sectorSize_ = 512;
    uint64_t readErrors_ = 0;
    std::unique_ptr<AlignedBuffer> bounce_;
};

// ---------------------------------------------------------------------------
// Candidate model (serialised as contracts/schemas/candidates.schema.json)
// ---------------------------------------------------------------------------
struct Extent {
    uint64_t offset = 0;  // byte offset inside source_device
    uint64_t length = 0;  // bytes
    bool sparse = false;  // true => zero-filled run, nothing to read
};

struct Candidate {
    std::string id;
    std::string sourceId;
    std::string sourceDevice;
    std::string sourceKind;      // "volume" | "physical" | "image"
    std::string method;          // "mft" | "fat" | "carve"
    std::string fileName;        // empty => null
    std::string originalPath;    // empty => null
    std::string extension;
    std::string signature;       // signature id that matched (carve) / was verified
    std::string mimeType;
    uint64_t sizeBytes = 0;
    uint64_t lba = 0;            // first sector relative to source_device
    uint64_t absoluteLba = 0;    // first sector relative to the physical disk
    uint32_t sectorSize = 512;
    std::vector<Extent> extents;
    std::string residentDataB64;
    std::optional<std::string> created, modified, mftModified, accessed;
    // NTFS metadata
    bool hasMft = false;
    uint64_t recordNumber = 0;
    uint16_t sequence = 0;
    uint64_t parentRecord = 0;
    uint16_t recordFlags = 0;
    uint32_t fileAttributes = 0;
    bool compressed = false, encrypted = false, sparseFile = false;
    bool fixupError = false;
    // Integrity
    std::string headerCheck = "unknown";  // match | mismatch | unknown
    std::optional<bool> footerFound;
    bool sizeEstimated = false;
    std::string sizeSource = "metadata";  // metadata | footer | length_field | structure | estimated
    bool truncated = false;
    double allocatedRatio = -1.0;         // <0 => unknown
    bool pathOrphaned = false;
    std::string partitionHint;
    std::vector<std::string> notes;
    // Preview sample
    std::string samplePath;
    uint64_t sampleBytes = 0;
};

struct ScanOptions {
    std::string scanId;
    std::string mode = "quick";        // quick | deep
    std::string scope = "drive";       // all | drive | folder
    std::vector<std::string> sourceSpecs;
    std::string folderPath;            // folder scope
    std::string outFile = "candidates.json";
    std::string sampleDir;             // optional preview sample directory
    uint64_t sampleBytes = 2ULL * 1024 * 1024;
    uint64_t sampleBudgetBytes = 2048ULL * 1024 * 1024;
    uint64_t maxCandidates = 500000;
    std::set<std::string> typeFilter;  // empty => all
    std::string cancelFile;
    bool sectorAligned = true;         // carve only at sector boundaries
    bool unallocatedOnly = false;      // carve only clusters free in $Bitmap / FAT
    bool allowNonHdd = false;
    bool streamCandidates = false;
    bool verifyHeaders = true;
    bool includeZeroLength = false;
    uint64_t minCarveSize = 128;
};

// Signals
void RequestCancel();
bool CancelRequested();

int RunScan(const ScanOptions& opts, const DriveManager& dm);
int RunRecover(const std::string& manifestPath, const std::string& cancelFile, const DriveManager& dm);
std::string SignaturesJson();

}  // namespace re
