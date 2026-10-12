// =============================================================================
// win_security_guard.cpp  —  MODULE 2: Permission & Security / Destination Guard
// -----------------------------------------------------------------------------
//   * CheckElevation(): OpenProcessToken + GetTokenInformation(TokenElevation,
//     TokenElevationType) + CheckTokenMembership(BUILTIN\Administrators).
//   * ValidateDestination(): GetFullPathNameW -> GetVolumePathNameW ->
//     GetVolumeNameForVolumeMountPointW (volume GUID) ->
//     IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS (physical disk) and compares against
//     every source so recovered bytes can never land on the media being read.
// =============================================================================
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
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace re {

#ifdef _WIN32
namespace {
struct DestExtent {
    int disk;
    uint64_t offset;
};

std::vector<DestExtent> VolumeExtents(const std::wstring& volumeGuidPath) {
    std::vector<DestExtent> out;
    // "\\?\Volume{GUID}\" -> "\\?\Volume{GUID}" (no trailing slash opens the device)
    std::wstring dev = volumeGuidPath;
    if (!dev.empty() && dev.back() == L'\\') dev.pop_back();
    HANDLE h = CreateFileW(dev.c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return out;
    std::vector<BYTE> buf(sizeof(VOLUME_DISK_EXTENTS) + 16 * sizeof(DISK_EXTENT));
    DWORD ret = 0;
    if (DeviceIoControl(h, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, nullptr, 0, buf.data(), static_cast<DWORD>(buf.size()), &ret,
                        nullptr)) {
        auto* e = reinterpret_cast<VOLUME_DISK_EXTENTS*>(buf.data());
        for (DWORD i = 0; i < e->NumberOfDiskExtents; ++i)
            out.push_back({static_cast<int>(e->Extents[i].DiskNumber), static_cast<uint64_t>(e->Extents[i].StartingOffset.QuadPart)});
    }
    CloseHandle(h);
    return out;
}
}  // namespace

ElevationStatus SecurityGuard::CheckElevation() {
    ElevationStatus st;
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        st.error = "OpenProcessToken failed (" + std::to_string(GetLastError()) + ")";
        st.elevationType = "unknown";
        return st;
    }
    TOKEN_ELEVATION elevation{};
    DWORD size = 0;
    if (GetTokenInformation(token, TokenElevation, &elevation, sizeof(elevation), &size))
        st.elevated = elevation.TokenIsElevated != 0;
    else
        st.error = "GetTokenInformation(TokenElevation) failed (" + std::to_string(GetLastError()) + ")";

    TOKEN_ELEVATION_TYPE type = TokenElevationTypeDefault;
    if (GetTokenInformation(token, TokenElevationType, &type, sizeof(type), &size)) {
        switch (type) {
            case TokenElevationTypeFull: st.elevationType = "full"; break;
            case TokenElevationTypeLimited: st.elevationType = "limited"; break;
            default: st.elevationType = "default"; break;  // UAC disabled or built-in Administrator
        }
    } else {
        st.elevationType = "unknown";
    }
    CloseHandle(token);

    SID_IDENTIFIER_AUTHORITY ntAuthority = SECURITY_NT_AUTHORITY;
    PSID adminGroup = nullptr;
    if (AllocateAndInitializeSid(&ntAuthority, 2, SECURITY_BUILTIN_DOMAIN_RID, DOMAIN_ALIAS_RID_ADMINS, 0, 0, 0, 0, 0, 0,
                                 &adminGroup)) {
        BOOL member = FALSE;
        if (CheckTokenMembership(nullptr, adminGroup, &member)) st.inAdministratorsGroup = member != FALSE;
        FreeSid(adminGroup);
    }
    return st;
}

DestinationCheck SecurityGuard::ValidateDestination(const std::string& destinationPath,
                                                    const std::vector<SourceDescriptor>& sources, uint64_t requiredBytes) {
    DestinationCheck c;
    c.destination = destinationPath;
    c.requiredBytes = requiredBytes;
    c.reasonCode = "OK";

    if (destinationPath.empty()) {
        c.reasonCode = "INVALID_PATH";
        c.conflicts.push_back("Destination path is empty.");
        return c;
    }

    std::wstring dest = Utf8ToWide(destinationPath);
    std::vector<wchar_t> full(32768);
    DWORD n = GetFullPathNameW(dest.c_str(), static_cast<DWORD>(full.size()), full.data(), nullptr);
    if (n == 0 || n >= full.size()) {
        c.reasonCode = "INVALID_PATH";
        c.conflicts.push_back("Destination path could not be resolved.");
        return c;
    }
    std::wstring fullPath(full.data(), n);
    c.destination = WideToUtf8(fullPath);

    wchar_t volRoot[MAX_PATH] = {};
    if (!GetVolumePathNameW(fullPath.c_str(), volRoot, MAX_PATH)) {
        c.reasonCode = "NOT_FOUND";
        c.conflicts.push_back("Destination volume could not be determined (" + std::to_string(GetLastError()) + ").");
        return c;
    }
    c.destinationVolumeRoot = WideToUtf8(volRoot);

    wchar_t guid[MAX_PATH] = {};
    if (GetVolumeNameForVolumeMountPointW(volRoot, guid, MAX_PATH)) c.destinationVolumeGuid = WideToUtf8(guid);

    UINT driveType = GetDriveTypeW(volRoot);
    if (driveType == DRIVE_REMOTE) c.warnings.push_back("Destination is a network share; restore speed depends on the network.");
    if (driveType == DRIVE_CDROM) {
        c.reasonCode = "INVALID_PATH";
        c.conflicts.push_back("Destination is an optical drive.");
        return c;
    }

    ULARGE_INTEGER freeAvail{}, total{}, totalFree{};
    if (GetDiskFreeSpaceExW(volRoot, &freeAvail, &total, &totalFree)) c.freeBytes = freeAvail.QuadPart;

    std::vector<DestExtent> destExtents;
    if (!c.destinationVolumeGuid.empty()) destExtents = VolumeExtents(Utf8ToWide(c.destinationVolumeGuid));
    for (const auto& e : destExtents) c.destinationPhysicalDrives.push_back(e.disk);

    std::string destRootLetter = ToUpperAscii(c.destinationVolumeRoot.substr(0, 2));

    for (const auto& s : sources) {
        switch (s.kind) {
            case SourceKind::Volume: {
                bool sameGuid = !s.volumeGuidPath.empty() && !c.destinationVolumeGuid.empty() &&
                                IEquals(s.volumeGuidPath, c.destinationVolumeGuid);
                bool sameLetter = !s.letter.empty() && IEquals(s.letter, destRootLetter);
                if (sameGuid || sameLetter) {
                    c.reasonCode = "SAME_VOLUME";
                    c.conflicts.push_back("Destination " + c.destinationVolumeRoot + " is the same volume as source " +
                                          s.displayName + ". Writing there would overwrite the deleted data you are recovering.");
                } else {
                    for (const auto& e : destExtents)
                        if (e.disk == s.physicalDriveIndex && s.physicalDriveIndex >= 0)
                            c.warnings.push_back("Destination is a different partition on the same physical disk as " + s.displayName +
                                                 ". This is safe for the source partition but a physically separate drive is recommended.");
                }
                break;
            }
            case SourceKind::Physical: {
                for (const auto& e : destExtents) {
                    if (e.disk != s.physicalDriveIndex) continue;
                    if (s.volumeOffsetInDevice == 0) {
                        c.reasonCode = "SAME_PHYSICAL_DISK";
                        c.conflicts.push_back("Destination lives on PhysicalDrive" + std::to_string(s.physicalDriveIndex) +
                                              ", which is being scanned sector-by-sector. Choose a different physical drive.");
                    } else if (e.offset == s.volumeOffsetInDevice) {
                        c.reasonCode = "SAME_VOLUME";
                        c.conflicts.push_back("Destination is the partition being recovered (" + s.displayName + ").");
                    } else {
                        c.warnings.push_back("Destination shares a physical disk with " + s.displayName + ".");
                    }
                }
                break;
            }
            case SourceKind::Image: {
                // An image file is allocated space, so writing elsewhere cannot overwrite it; only refuse the image
                // path itself as the destination (same rule as the POSIX build).
                std::vector<wchar_t> img(32768);
                DWORD m = GetFullPathNameW(Utf8ToWide(s.devicePath).c_str(), static_cast<DWORD>(img.size()), img.data(), nullptr);
                if (m > 0 && m < img.size() && IEquals(WideToUtf8(std::wstring(img.data(), m)), c.destination)) {
                    c.reasonCode = "SAME_VOLUME";
                    c.conflicts.push_back("Destination equals the source image path.");
                }
                break;
            }
        }
    }

    if (c.conflicts.empty() && requiredBytes > 0 && c.freeBytes > 0 && c.freeBytes < requiredBytes) {
        c.reasonCode = "INSUFFICIENT_SPACE";
        c.conflicts.push_back("Destination has " + std::to_string(c.freeBytes) + " bytes free but " +
                              std::to_string(requiredBytes) + " bytes are required.");
    }
    c.safe = c.conflicts.empty();
    if (c.safe) c.reasonCode = "OK";
    return c;
}

#else  // ---------------------------------------------------- non-Windows test stubs
ElevationStatus SecurityGuard::CheckElevation() {
    ElevationStatus st;
    st.elevated = (geteuid() == 0);
    st.elevationType = st.elevated ? "full" : "limited";
    st.inAdministratorsGroup = st.elevated;
    return st;
}

DestinationCheck SecurityGuard::ValidateDestination(const std::string& destinationPath,
                                                    const std::vector<SourceDescriptor>& sources, uint64_t requiredBytes) {
    DestinationCheck c;
    c.destination = destinationPath;
    c.requiredBytes = requiredBytes;
    c.reasonCode = "OK";
    if (destinationPath.empty()) {
        c.reasonCode = "INVALID_PATH";
        c.conflicts.push_back("Destination path is empty.");
        return c;
    }
    for (const auto& s : sources) {
        // Refuse to write *into* an image file path itself.
        if (s.devicePath == destinationPath) {
            c.reasonCode = "SAME_VOLUME";
            c.conflicts.push_back("Destination equals the source image path.");
        }
    }
    c.safe = c.conflicts.empty();
    return c;
}
#endif

std::string SecurityGuard::ElevationJson(const ElevationStatus& s) {
    JsonWriter w;
    w.BeginObject()
        .KV("elevated", s.elevated)
        .KV("elevation_type", s.elevationType)
        .KV("in_administrators_group", s.inAdministratorsGroup)
        .KVnullable("error", s.error)
        .EndObject();
    return w.take();
}

std::string SecurityGuard::DestinationJson(const DestinationCheck& c) {
    JsonWriter w;
    w.BeginObject()
        .KV("safe", c.safe)
        .KV("reason_code", c.reasonCode)
        .KV("destination", c.destination)
        .KVnullable("destination_volume_root", c.destinationVolumeRoot)
        .KVnullable("destination_volume_guid", c.destinationVolumeGuid);
    w.Key("destination_physical_drives").BeginArray();
    for (int d : c.destinationPhysicalDrives) w.Int(d);
    w.EndArray();
    w.KVu("free_bytes", c.freeBytes).KVu("required_bytes", c.requiredBytes);
    w.Key("conflicts").BeginArray();
    for (const auto& s : c.conflicts) w.String(s);
    w.EndArray();
    w.Key("warnings").BeginArray();
    for (const auto& s : c.warnings) w.String(s);
    w.EndArray();
    w.EndObject();
    return w.take();
}

}  // namespace re
