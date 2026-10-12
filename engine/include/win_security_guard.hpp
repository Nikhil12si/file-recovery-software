// =============================================================================
// win_security_guard.hpp  —  MODULE 2: Permission & Security / Destination Guard
// -----------------------------------------------------------------------------
// Responsibilities (high cohesion: *only* "is this operation allowed?"):
//   * Detect whether the process token is elevated (TokenElevation) so raw
//     \\.\X: and \\.\PhysicalDriveN handles can be opened.
//   * Validate that a recovery / sample destination can never overwrite the
//     data being recovered:
//        - destination volume GUID must differ from every source volume GUID
//        - destination must not live on a physical disk that is being scanned
//          as a whole (\\.\PhysicalDriveN source)
//        - same physical disk but different partition => warning only
//        - enough free space for the requested bytes
// =============================================================================
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "win_drive_manager.hpp"

namespace re {

struct ElevationStatus {
    bool elevated = false;
    std::string elevationType;  // "full" | "limited" | "default" | "unknown"
    bool inAdministratorsGroup = false;
    std::string error;
};

struct DestinationCheck {
    bool safe = false;
    std::string reasonCode;               // OK | SAME_VOLUME | SAME_PHYSICAL_DISK | INSUFFICIENT_SPACE | INVALID_PATH | NOT_FOUND
    std::string destination;
    std::string destinationVolumeRoot;    // "E:\"
    std::string destinationVolumeGuid;    // "\\?\Volume{...}\"
    std::vector<int> destinationPhysicalDrives;
    uint64_t freeBytes = 0;
    uint64_t requiredBytes = 0;
    std::vector<std::string> conflicts;   // human readable hard failures
    std::vector<std::string> warnings;    // soft warnings
};

class SecurityGuard {
public:
    static ElevationStatus CheckElevation();

    // `sources` are the descriptors that are (or will be) read from.
    static DestinationCheck ValidateDestination(const std::string& destinationPath,
                                                const std::vector<SourceDescriptor>& sources,
                                                uint64_t requiredBytes);

    static std::string ElevationJson(const ElevationStatus& s);
    static std::string DestinationJson(const DestinationCheck& c);
};

}  // namespace re
