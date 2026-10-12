// =============================================================================
// win_drive_manager.hpp  —  MODULE 1: Storage Source Manager (Drive & Target
// Enumerator)
// -----------------------------------------------------------------------------
// Responsibilities (high cohesion: *only* discovery & description of sources):
//   * Enumerate physical disks (\\.\PhysicalDriveN) with geometry, model, bus,
//     media type (HDD / SSD) and partition layout.
//   * Enumerate logical volumes (C:, D:, ...) with file system, cluster size,
//     volume GUID, NT device name and the physical disk extent they live on.
//   * Resolve a user-supplied source specification ("D:", "\\.\D:",
//     "PhysicalDrive1", "image:C:\disk.dd") into a SourceDescriptor that the
//     recovery core can open.
//
// It never reads file data and never writes anything.
// =============================================================================
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "engine_common.hpp"

namespace re {

enum class SourceKind { Volume, Physical, Image };

inline const char* SourceKindName(SourceKind k) {
    switch (k) {
        case SourceKind::Volume: return "volume";
        case SourceKind::Physical: return "physical";
        default: return "image";
    }
}

struct PartitionInfo {
    int number = 0;                 // 1-based partition number
    uint64_t offsetBytes = 0;       // starting offset on the physical disk
    uint64_t lengthBytes = 0;
    std::string style;              // "MBR" | "GPT"
    std::string typeLabel;          // e.g. "0x07 NTFS/exFAT", "Basic data"
    std::string mountedLetter;      // "D:" if a mounted volume maps here, else ""
};

struct PhysicalDriveInfo {
    int index = -1;
    std::string devicePath;         // \\.\PhysicalDrive1
    std::string model;
    std::string serial;
    std::string busType;            // SATA, USB, NVMe, SAS, ...
    std::string mediaType = "Unknown";  // "HDD" | "SSD" | "Unknown"
    bool removable = false;
    uint64_t sizeBytes = 0;
    uint32_t bytesPerSector = 512;          // logical sector size
    uint32_t physicalBytesPerSector = 512;  // 4096 on Advanced Format (512e/4Kn)
    uint64_t totalSectors = 0;
    std::string partitionStyle;     // MBR | GPT | RAW
    std::vector<PartitionInfo> partitions;
    bool accessible = false;
    std::string error;
};

struct VolumeInfo {
    std::string letter;             // "D:"
    std::string rootPath;           // "D:\"
    std::string devicePath;         // "\\.\D:"
    std::string volumeGuidPath;     // "\\?\Volume{...}\"
    std::string ntDeviceName;       // "\Device\HarddiskVolume3"
    std::string label;
    std::string filesystem;         // NTFS | FAT32 | exFAT | RAW | ...
    std::string driveType;          // fixed | removable | ...
    uint64_t totalBytes = 0;
    uint64_t freeBytes = 0;
    uint32_t bytesPerSector = 512;
    uint32_t clusterSize = 4096;
    uint64_t totalSectors = 0;
    int physicalDriveIndex = -1;    // -1 if spanned / unknown
    uint64_t partitionOffsetBytes = 0;
    bool isSystemVolume = false;
    bool accessible = false;
    std::string error;
};

// Everything the core engine needs to open & interpret a source.
struct SourceDescriptor {
    std::string sourceId;           // stable short id used in candidate ids: vol-D, pd1, pd1-p2, img0
    SourceKind kind = SourceKind::Volume;
    std::string devicePath;         // path passed to CreateFileW
    std::string displayName;        // "D:\ (Data, NTFS)"
    std::string letter;             // "D:" for volumes, "" otherwise
    std::string volumeGuidPath;
    std::string filesystemHint;     // from the OS; the core re-detects from the boot sector
    uint64_t partitionOffsetBytes = 0;  // offset of this volume on its physical disk (for absolute LBA)
    uint64_t volumeOffsetInDevice = 0;  // where the file system starts *inside devicePath* (non-zero for partitions opened via PhysicalDrive)
    uint64_t regionLengthBytes = 0;     // length of that region (0 => until end of device)
    int physicalDriveIndex = -1;
    std::string mediaType = "Unknown";
};

class DriveManager {
public:
    std::vector<PhysicalDriveInfo> EnumeratePhysicalDrives() const;
    std::vector<VolumeInfo> EnumerateVolumes() const;

    // Resolve a single user source spec. Returns nullopt + error on failure.
    std::optional<SourceDescriptor> Resolve(const std::string& spec, std::string* error) const;

    // Sources for "All Drives" mode.
    //   quick: every mounted NTFS/FAT32 volume + unmounted NTFS/FAT32 partitions on HDDs
    //   deep : every physical drive
    std::vector<SourceDescriptor> AllSources(bool deepMode, bool allowNonHdd) const;

    // Serialise the inventory as the "physical_drives" and "volumes" members of
    // an already-open JSON object (contract: contracts/schemas/drives.schema.json).
    void WriteInventory(JsonWriter& w) const;

    static std::string MakeVolumeSourceId(const std::string& letter);
};

}  // namespace re
