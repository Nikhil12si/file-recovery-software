// =============================================================================
// win_drive_manager.cpp  —  MODULE 1: Storage Source Manager
// -----------------------------------------------------------------------------
// Discovers physical disks and logical volumes through Win32:
//   GetLogicalDriveStringsW, GetDriveTypeW, QueryDosDeviceW,
//   GetVolumeNameForVolumeMountPointW, GetVolumeInformationW,
//   GetDiskFreeSpace(Ex)W, DeviceIoControl(IOCTL_DISK_GET_DRIVE_GEOMETRY_EX,
//   IOCTL_DISK_GET_DRIVE_LAYOUT_EX, IOCTL_STORAGE_QUERY_PROPERTY,
//   IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS).
//
// Handles are opened with *zero* desired access, which is sufficient for these
// FILE_ANY_ACCESS IOCTLs and lets the inventory work even before elevation.
// =============================================================================
#include "win_drive_manager.hpp"

#include <cctype>
#include <iterator>
#include <set>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <winioctl.h>
#endif

namespace re {

std::string DriveManager::MakeVolumeSourceId(const std::string& letter) {
    std::string id = "vol-";
    for (char c : letter) if (std::isalnum(static_cast<unsigned char>(c))) id.push_back(static_cast<char>(std::toupper(c)));
    return id;
}

#ifdef _WIN32
// ---------------------------------------------------------------------------
// Win32 helpers
// ---------------------------------------------------------------------------
namespace {

// RAII wrapper for HANDLE.
class ScopedHandle {
public:
    explicit ScopedHandle(HANDLE h = INVALID_HANDLE_VALUE) : h_(h) {}
    ~ScopedHandle() { if (valid()) CloseHandle(h_); }
    ScopedHandle(const ScopedHandle&) = delete;
    ScopedHandle& operator=(const ScopedHandle&) = delete;
    bool valid() const { return h_ != INVALID_HANDLE_VALUE && h_ != nullptr; }
    HANDLE get() const { return h_; }
private:
    HANDLE h_;
};

std::string LastErrorString(DWORD code = GetLastError()) {
    wchar_t* msg = nullptr;
    DWORD n = FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                             nullptr, code, 0, reinterpret_cast<LPWSTR>(&msg), 0, nullptr);
    std::string s = "Win32 error " + std::to_string(code);
    if (n && msg) {
        std::wstring w(msg, n);
        while (!w.empty() && (w.back() == L'\r' || w.back() == L'\n' || w.back() == L' ')) w.pop_back();
        s += ": " + WideToUtf8(w);
    }
    if (msg) LocalFree(msg);
    return s;
}

HANDLE OpenForQuery(const std::wstring& path) {
    return CreateFileW(path.c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
}

std::string TrimCopy(std::string s) {
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back()))) s.pop_back();
    size_t i = 0;
    while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
    return s.substr(i);
}

const char* BusTypeName(STORAGE_BUS_TYPE t) {
    switch (t) {
        case BusTypeScsi: return "SCSI";
        case BusTypeAtapi: return "ATAPI";
        case BusTypeAta: return "ATA";
        case BusType1394: return "1394";
        case BusTypeSsa: return "SSA";
        case BusTypeFibre: return "Fibre";
        case BusTypeUsb: return "USB";
        case BusTypeRAID: return "RAID";
        case BusTypeiScsi: return "iSCSI";
        case BusTypeSas: return "SAS";
        case BusTypeSata: return "SATA";
        case BusTypeSd: return "SD";
        case BusTypeMmc: return "MMC";
        case BusTypeVirtual: return "Virtual";
        case BusTypeFileBackedVirtual: return "FileBackedVirtual";
        case BusTypeSpaces: return "StorageSpaces";
        case BusTypeNvme: return "NVMe";
        default: return "Unknown";
    }
}

std::string MbrTypeLabel(BYTE t) {
    char buf[48];
    const char* name = "Unknown";
    switch (t) {
        case 0x07: name = "NTFS/exFAT"; break;
        case 0x0B: name = "FAT32 (CHS)"; break;
        case 0x0C: name = "FAT32 (LBA)"; break;
        case 0x06: case 0x0E: name = "FAT16"; break;
        case 0x05: case 0x0F: name = "Extended"; break;
        case 0x27: name = "Windows RE"; break;
        case 0xEE: name = "GPT Protective"; break;
        case 0xEF: name = "EFI System"; break;
        default: break;
    }
    std::snprintf(buf, sizeof(buf), "0x%02X %s", t, name);
    return buf;
}

void FillStorageProperties(HANDLE h, PhysicalDriveInfo& info) {
    // --- Device descriptor: vendor / model / serial / bus / removable -------
    STORAGE_PROPERTY_QUERY q{};
    q.PropertyId = StorageDeviceProperty;
    q.QueryType = PropertyStandardQuery;
    std::vector<BYTE> out(4096);
    DWORD ret = 0;
    if (DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, &q, sizeof(q), out.data(), static_cast<DWORD>(out.size()), &ret,
                        nullptr) && ret >= sizeof(STORAGE_DEVICE_DESCRIPTOR)) {
        auto* d = reinterpret_cast<STORAGE_DEVICE_DESCRIPTOR*>(out.data());
        auto str = [&](DWORD off) -> std::string {
            if (off == 0 || off >= ret) return "";
            const char* p = reinterpret_cast<const char*>(out.data() + off);
            size_t maxLen = ret - off;
            return TrimCopy(std::string(p, strnlen(p, maxLen)));
        };
        std::string vendor = str(d->VendorIdOffset);
        std::string product = str(d->ProductIdOffset);
        info.model = TrimCopy(vendor.empty() ? product : vendor + " " + product);
        info.serial = str(d->SerialNumberOffset);
        info.busType = BusTypeName(d->BusType);
        info.removable = d->RemovableMedia != FALSE;
        if (d->BusType == BusTypeNvme) info.mediaType = "SSD";
    }

    // --- Seek penalty: rotational media incur seek penalty -> HDD ----------
    STORAGE_PROPERTY_QUERY sq{};
    sq.PropertyId = StorageDeviceSeekPenaltyProperty;
    sq.QueryType = PropertyStandardQuery;
    DEVICE_SEEK_PENALTY_DESCRIPTOR sp{};
    if (DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, &sq, sizeof(sq), &sp, sizeof(sp), &ret, nullptr) &&
        ret >= sizeof(sp)) {
        info.mediaType = sp.IncursSeekPenalty ? "HDD" : "SSD";
    }

    // --- Physical sector size (Advanced Format detection) -------------------
    STORAGE_PROPERTY_QUERY aq{};
    aq.PropertyId = StorageAccessAlignmentProperty;
    aq.QueryType = PropertyStandardQuery;
    STORAGE_ACCESS_ALIGNMENT_DESCRIPTOR al{};
    if (DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, &aq, sizeof(aq), &al, sizeof(al), &ret, nullptr) &&
        ret >= sizeof(al)) {
        info.physicalBytesPerSector = al.BytesPerPhysicalSector;
        if (al.BytesPerLogicalSector) info.bytesPerSector = al.BytesPerLogicalSector;
    }
}

void FillLayout(HANDLE h, PhysicalDriveInfo& info) {
    std::vector<BYTE> buf(sizeof(DRIVE_LAYOUT_INFORMATION_EX) + 128 * sizeof(PARTITION_INFORMATION_EX));
    DWORD ret = 0;
    if (!DeviceIoControl(h, IOCTL_DISK_GET_DRIVE_LAYOUT_EX, nullptr, 0, buf.data(), static_cast<DWORD>(buf.size()), &ret,
                         nullptr)) {
        info.partitionStyle = "UNKNOWN";
        return;
    }
    auto* layout = reinterpret_cast<DRIVE_LAYOUT_INFORMATION_EX*>(buf.data());
    switch (layout->PartitionStyle) {
        case PARTITION_STYLE_MBR: info.partitionStyle = "MBR"; break;
        case PARTITION_STYLE_GPT: info.partitionStyle = "GPT"; break;
        default: info.partitionStyle = "RAW"; break;
    }
    for (DWORD i = 0; i < layout->PartitionCount; ++i) {
        const PARTITION_INFORMATION_EX& p = layout->PartitionEntry[i];
        if (p.PartitionLength.QuadPart <= 0) continue;
        PartitionInfo pi;
        pi.number = static_cast<int>(p.PartitionNumber);
        pi.offsetBytes = static_cast<uint64_t>(p.StartingOffset.QuadPart);
        pi.lengthBytes = static_cast<uint64_t>(p.PartitionLength.QuadPart);
        if (p.PartitionStyle == PARTITION_STYLE_MBR) {
            if (p.Mbr.PartitionType == PARTITION_ENTRY_UNUSED) continue;
            pi.style = "MBR";
            pi.typeLabel = MbrTypeLabel(p.Mbr.PartitionType);
        } else if (p.PartitionStyle == PARTITION_STYLE_GPT) {
            pi.style = "GPT";
            pi.typeLabel = WideToUtf8(std::wstring(p.Gpt.Name, wcsnlen(p.Gpt.Name, 36)));
            if (pi.typeLabel.empty()) pi.typeLabel = "GPT partition";
        } else {
            pi.style = "RAW";
        }
        info.partitions.push_back(pi);
    }
}

}  // namespace

// ---------------------------------------------------------------------------
// Physical drives
// ---------------------------------------------------------------------------
std::vector<PhysicalDriveInfo> DriveManager::EnumeratePhysicalDrives() const {
    std::vector<PhysicalDriveInfo> drives;
    for (int idx = 0; idx < 64; ++idx) {
        std::wstring path = L"\\\\.\\PhysicalDrive" + std::to_wstring(idx);
        ScopedHandle h(OpenForQuery(path));
        if (!h.valid()) continue;  // index not present

        PhysicalDriveInfo info;
        info.index = idx;
        info.devicePath = WideToUtf8(path);

        std::vector<BYTE> gbuf(sizeof(DISK_GEOMETRY_EX) + 512);
        DWORD ret = 0;
        if (DeviceIoControl(h.get(), IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, nullptr, 0, gbuf.data(),
                            static_cast<DWORD>(gbuf.size()), &ret, nullptr)) {
            auto* g = reinterpret_cast<DISK_GEOMETRY_EX*>(gbuf.data());
            info.sizeBytes = static_cast<uint64_t>(g->DiskSize.QuadPart);
            info.bytesPerSector = g->Geometry.BytesPerSector ? g->Geometry.BytesPerSector : 512;
            info.accessible = true;
        } else {
            info.error = "IOCTL_DISK_GET_DRIVE_GEOMETRY_EX failed: " + LastErrorString();
        }
        FillStorageProperties(h.get(), info);
        FillLayout(h.get(), info);
        info.totalSectors = info.bytesPerSector ? info.sizeBytes / info.bytesPerSector : 0;
        drives.push_back(std::move(info));
    }

    // Annotate partitions with mounted letters.
    for (const VolumeInfo& v : EnumerateVolumes()) {
        for (auto& d : drives) {
            if (d.index != v.physicalDriveIndex) continue;
            for (auto& p : d.partitions)
                if (p.offsetBytes == v.partitionOffsetBytes) p.mountedLetter = v.letter;
        }
    }
    return drives;
}

// ---------------------------------------------------------------------------
// Logical volumes
// ---------------------------------------------------------------------------
std::vector<VolumeInfo> DriveManager::EnumerateVolumes() const {
    std::vector<VolumeInfo> vols;
    wchar_t buffer[1024] = {};
    DWORD n = GetLogicalDriveStringsW(static_cast<DWORD>(std::size(buffer) - 1), buffer);
    if (n == 0 || n >= std::size(buffer)) return vols;

    wchar_t sysDir[MAX_PATH] = {};
    GetSystemWindowsDirectoryW(sysDir, MAX_PATH);
    std::wstring sysLetter(sysDir, 2);

    for (wchar_t* p = buffer; *p; p += wcslen(p) + 1) {
        std::wstring root = p;  // "C:\"
        UINT type = GetDriveTypeW(root.c_str());
        if (type != DRIVE_FIXED && type != DRIVE_REMOVABLE) continue;  // skip CD-ROM, network, RAM disks

        VolumeInfo v;
        std::wstring letter = root.substr(0, 2);
        v.letter = WideToUtf8(letter);
        v.rootPath = WideToUtf8(root);
        v.devicePath = "\\\\.\\" + v.letter;
        v.driveType = (type == DRIVE_FIXED) ? "fixed" : "removable";
        v.isSystemVolume = _wcsicmp(letter.c_str(), sysLetter.c_str()) == 0;

        wchar_t target[1024] = {};
        if (QueryDosDeviceW(letter.c_str(), target, static_cast<DWORD>(std::size(target)))) v.ntDeviceName = WideToUtf8(target);

        wchar_t guid[MAX_PATH] = {};
        if (GetVolumeNameForVolumeMountPointW(root.c_str(), guid, MAX_PATH)) v.volumeGuidPath = WideToUtf8(guid);

        wchar_t label[MAX_PATH + 1] = {}, fs[MAX_PATH + 1] = {};
        DWORD serial = 0, maxComp = 0, flags = 0;
        if (GetVolumeInformationW(root.c_str(), label, MAX_PATH + 1, &serial, &maxComp, &flags, fs, MAX_PATH + 1)) {
            v.label = WideToUtf8(label);
            v.filesystem = WideToUtf8(fs);
            v.accessible = true;
        } else {
            // Unformatted / corrupted file system — Windows reports it as RAW.
            v.filesystem = "RAW";
            v.error = LastErrorString();
        }

        ULARGE_INTEGER freeAvail{}, total{}, totalFree{};
        if (GetDiskFreeSpaceExW(root.c_str(), &freeAvail, &total, &totalFree)) {
            v.totalBytes = total.QuadPart;
            v.freeBytes = totalFree.QuadPart;
        }
        DWORD spc = 0, bps = 0, nfc = 0, tnc = 0;
        if (GetDiskFreeSpaceW(root.c_str(), &spc, &bps, &nfc, &tnc)) {
            v.bytesPerSector = bps;
            v.clusterSize = spc * bps;
        }

        ScopedHandle vh(OpenForQuery(L"\\\\.\\" + letter));
        if (vh.valid()) {
            std::vector<BYTE> ebuf(sizeof(VOLUME_DISK_EXTENTS) + 16 * sizeof(DISK_EXTENT));
            DWORD ret = 0;
            if (DeviceIoControl(vh.get(), IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, nullptr, 0, ebuf.data(),
                                static_cast<DWORD>(ebuf.size()), &ret, nullptr)) {
                auto* ext = reinterpret_cast<VOLUME_DISK_EXTENTS*>(ebuf.data());
                if (ext->NumberOfDiskExtents >= 1) {
                    // Spanned/striped volumes (>1 extent) are reported against their first extent.
                    v.physicalDriveIndex = static_cast<int>(ext->Extents[0].DiskNumber);
                    v.partitionOffsetBytes = static_cast<uint64_t>(ext->Extents[0].StartingOffset.QuadPart);
                    if (ext->NumberOfDiskExtents == 1)
                        v.totalBytes = std::max<uint64_t>(v.totalBytes, static_cast<uint64_t>(ext->Extents[0].ExtentLength.QuadPart));
                }
            }
        }
        v.totalSectors = v.bytesPerSector ? v.totalBytes / v.bytesPerSector : 0;
        vols.push_back(std::move(v));
    }
    return vols;
}

// ---------------------------------------------------------------------------
// Source resolution
// ---------------------------------------------------------------------------
std::optional<SourceDescriptor> DriveManager::Resolve(const std::string& rawSpec, std::string* error) const {
    std::string spec = rawSpec;
    // Strip surrounding quotes/whitespace.
    while (!spec.empty() && (spec.front() == '"' || spec.front() == ' ')) spec.erase(spec.begin());
    while (!spec.empty() && (spec.back() == '"' || spec.back() == ' ')) spec.pop_back();

    if (StartsWith(ToLowerAscii(spec), "image:")) {
        SourceDescriptor d;
        d.kind = SourceKind::Image;
        d.devicePath = spec.substr(6);
        // Fail early with SOURCE_NOT_FOUND (as the contract and the POSIX build do) instead of a log line later.
        DWORD imgAttrs = GetFileAttributesW(Utf8ToWide(d.devicePath).c_str());
        if (imgAttrs == INVALID_FILE_ATTRIBUTES || (imgAttrs & FILE_ATTRIBUTE_DIRECTORY)) {
            if (error) *error = "Disk image not found: " + d.devicePath;
            return std::nullopt;
        }
        d.sourceId = "img-" + SanitizeFileComponent(d.devicePath.substr(d.devicePath.find_last_of("\\/") + 1), "image");
        d.displayName = "Disk image " + d.devicePath;
        d.mediaType = "Image";
        return d;
    }

    std::string s = spec;
    if (StartsWith(s, "\\\\.\\") || StartsWith(s, "\\\\?\\")) s = s.substr(4);

    // ---- Physical drive: PhysicalDriveN[@pK] -------------------------------
    std::string lower = ToLowerAscii(s);
    if (StartsWith(lower, "physicaldrive")) {
        std::string rest = s.substr(13);
        int partNo = -1;
        auto at = rest.find('@');
        if (at != std::string::npos) {
            std::string p = ToLowerAscii(rest.substr(at + 1));
            if (!p.empty() && p[0] == 'p') partNo = std::atoi(p.c_str() + 1);
            rest = rest.substr(0, at);
        }
        if (rest.empty() || !std::all_of(rest.begin(), rest.end(), ::isdigit)) {
            if (error) *error = "Invalid physical drive specification: " + rawSpec;
            return std::nullopt;
        }
        int idx = std::atoi(rest.c_str());
        for (const auto& d : EnumeratePhysicalDrives()) {
            if (d.index != idx) continue;
            SourceDescriptor sd;
            sd.kind = SourceKind::Physical;
            sd.devicePath = d.devicePath;
            sd.physicalDriveIndex = idx;
            sd.mediaType = d.mediaType;
            sd.sourceId = "pd" + std::to_string(idx);
            sd.displayName = "Disk " + std::to_string(idx) + " (" + (d.model.empty() ? "unknown model" : d.model) + ")";
            if (partNo > 0) {
                bool found = false;
                for (const auto& p : d.partitions) {
                    if (p.number != partNo) continue;
                    sd.volumeOffsetInDevice = p.offsetBytes;
                    sd.regionLengthBytes = p.lengthBytes;
                    sd.partitionOffsetBytes = p.offsetBytes;
                    sd.sourceId += "-p" + std::to_string(partNo);
                    sd.displayName += " partition " + std::to_string(partNo);
                    sd.letter = p.mountedLetter;
                    found = true;
                }
                if (!found) {
                    if (error) *error = "Partition " + std::to_string(partNo) + " not found on PhysicalDrive" + rest;
                    return std::nullopt;
                }
            }
            return sd;
        }
        if (error) *error = "Physical drive not found: " + rawSpec;
        return std::nullopt;
    }

    // ---- Volume letter: "D", "D:", "D:\" ------------------------------------
    if (!s.empty() && std::isalpha(static_cast<unsigned char>(s[0])) &&
        (s.size() == 1 || (s[1] == ':' && (s.size() == 2 || (s.size() == 3 && (s[2] == '\\' || s[2] == '/')))))) {
        std::string letter = std::string(1, static_cast<char>(std::toupper(s[0]))) + ":";
        for (const auto& v : EnumerateVolumes()) {
            if (!IEquals(v.letter, letter)) continue;
            SourceDescriptor sd;
            sd.kind = SourceKind::Volume;
            sd.devicePath = v.devicePath;
            sd.letter = v.letter;
            sd.volumeGuidPath = v.volumeGuidPath;
            sd.filesystemHint = v.filesystem;
            sd.partitionOffsetBytes = v.partitionOffsetBytes;
            sd.physicalDriveIndex = v.physicalDriveIndex;
            sd.sourceId = MakeVolumeSourceId(letter);
            sd.displayName = v.rootPath + " (" + (v.label.empty() ? "Local Disk" : v.label) + ", " + v.filesystem + ")";
            for (const auto& d : EnumeratePhysicalDrives())
                if (d.index == v.physicalDriveIndex) sd.mediaType = d.mediaType;
            return sd;
        }
        if (error) *error = "Volume not found or not a fixed/removable disk: " + rawSpec;
        return std::nullopt;
    }

    // ---- Anything else that exists as a file is treated as a disk image ----
    DWORD attrs = GetFileAttributesW(Utf8ToWide(spec).c_str());
    if (attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY)) return Resolve("image:" + spec, error);

    if (error) *error = "Unrecognised source specification: " + rawSpec;
    return std::nullopt;
}

std::vector<SourceDescriptor> DriveManager::AllSources(bool deepMode, bool allowNonHdd) const {
    std::vector<SourceDescriptor> out;
    auto physical = EnumeratePhysicalDrives();
    auto mediaOf = [&](int idx) -> std::string {
        for (const auto& d : physical) if (d.index == idx) return d.mediaType;
        return "Unknown";
    };

    if (deepMode) {
        for (const auto& d : physical) {
            if (!d.accessible || d.sizeBytes == 0) continue;
            if (d.mediaType == "SSD" && !allowNonHdd) {
                LogWarn("Skipping " + d.devicePath + " (" + d.model + "): SSD detected. TRIM makes carving unreliable; use --allow-non-hdd to include it.");
                continue;
            }
            std::string err;
            auto sd = Resolve(d.devicePath, &err);
            if (sd) out.push_back(*sd);
        }
        return out;
    }

    std::set<std::pair<int, uint64_t>> mounted;
    for (const auto& v : EnumerateVolumes()) {
        mounted.insert({v.physicalDriveIndex, v.partitionOffsetBytes});
        std::string fs = ToUpperAscii(v.filesystem);
        if (fs != "NTFS" && fs != "FAT32" && fs != "RAW") continue;
        if (mediaOf(v.physicalDriveIndex) == "SSD" && !allowNonHdd) {
            LogWarn("Skipping " + v.rootPath + ": volume resides on an SSD (use --allow-non-hdd to include).");
            continue;
        }
        std::string err;
        auto sd = Resolve(v.letter, &err);
        if (sd) out.push_back(*sd);
    }
    // Unmounted partitions (deleted drive letters, damaged volumes) on HDDs.
    for (const auto& d : physical) {
        if (d.mediaType == "SSD" && !allowNonHdd) continue;
        for (const auto& p : d.partitions) {
            if (mounted.count({d.index, p.offsetBytes})) continue;
            if (p.lengthBytes < 16ULL * 1024 * 1024) continue;  // skip MSR/tiny partitions
            std::string err;
            auto sd = Resolve(d.devicePath + "@p" + std::to_string(p.number), &err);
            if (sd) out.push_back(*sd);
        }
    }
    return out;
}

#else  // ------------------------------------------------------------ non-Windows
// The product targets Windows only. These stubs exist so that the parsing core
// can be compiled and unit-tested against disk *images* in CI on any host.
std::vector<PhysicalDriveInfo> DriveManager::EnumeratePhysicalDrives() const { return {}; }
std::vector<VolumeInfo> DriveManager::EnumerateVolumes() const { return {}; }

std::optional<SourceDescriptor> DriveManager::Resolve(const std::string& rawSpec, std::string* error) const {
    std::string spec = rawSpec;
    if (StartsWith(ToLowerAscii(spec), "image:")) spec = spec.substr(6);
    FILE* f = std::fopen(spec.c_str(), "rb");
    if (!f) {
        if (error) *error = "Only disk images are supported on non-Windows hosts; cannot open " + spec;
        return std::nullopt;
    }
    std::fclose(f);
    SourceDescriptor d;
    d.kind = SourceKind::Image;
    d.devicePath = spec;
    d.sourceId = "img-" + SanitizeFileComponent(spec.substr(spec.find_last_of("\\/") + 1), "image");
    d.displayName = "Disk image " + spec;
    d.mediaType = "Image";
    return d;
}

std::vector<SourceDescriptor> DriveManager::AllSources(bool, bool) const { return {}; }
#endif

// ---------------------------------------------------------------------------
// Serialisation (portable)
// ---------------------------------------------------------------------------
void DriveManager::WriteInventory(JsonWriter& w) const {
    w.Key("physical_drives").BeginArray();
    for (const auto& d : EnumeratePhysicalDrives()) {
        w.BeginObject()
            .KVi("index", d.index)
            .KV("device_path", d.devicePath)
            .KV("model", d.model)
            .KV("serial", d.serial)
            .KV("bus_type", d.busType)
            .KV("media_type", d.mediaType)
            .KV("removable", d.removable)
            .KVu("size_bytes", d.sizeBytes)
            .KVu("bytes_per_sector", d.bytesPerSector)
            .KVu("physical_bytes_per_sector", d.physicalBytesPerSector)
            .KVu("total_sectors", d.totalSectors)
            .KV("partition_style", d.partitionStyle)
            .KV("accessible", d.accessible)
            .KVnullable("error", d.error);
        w.Key("partitions").BeginArray();
        for (const auto& p : d.partitions) {
            w.BeginObject()
                .KVi("number", p.number)
                .KVu("offset_bytes", p.offsetBytes)
                .KVu("length_bytes", p.lengthBytes)
                .KV("style", p.style)
                .KV("type_label", p.typeLabel)
                .KVnullable("mounted_letter", p.mountedLetter)
                .KV("source_spec", d.devicePath + "@p" + std::to_string(p.number))
                .EndObject();
        }
        w.EndArray();
        w.EndObject();
    }
    w.EndArray();

    w.Key("volumes").BeginArray();
    for (const auto& v : EnumerateVolumes()) {
        w.BeginObject()
            .KV("letter", v.letter)
            .KV("root_path", v.rootPath)
            .KV("device_path", v.devicePath)
            .KV("volume_guid_path", v.volumeGuidPath)
            .KV("nt_device_name", v.ntDeviceName)
            .KV("label", v.label)
            .KV("filesystem", v.filesystem)
            .KV("drive_type", v.driveType)
            .KVu("total_bytes", v.totalBytes)
            .KVu("free_bytes", v.freeBytes)
            .KVu("bytes_per_sector", v.bytesPerSector)
            .KVu("cluster_size", v.clusterSize)
            .KVu("total_sectors", v.totalSectors)
            .KVi("physical_drive_index", v.physicalDriveIndex)
            .KVu("partition_offset_bytes", v.partitionOffsetBytes)
            .KV("is_system_volume", v.isSystemVolume)
            .KV("accessible", v.accessible)
            .KVnullable("error", v.error)
            .KV("source_spec", v.letter)
            .EndObject();
    }
    w.EndArray();
}

}  // namespace re
