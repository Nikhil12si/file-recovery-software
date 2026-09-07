#include <windows.h>
#include <iostream>
#include <fstream>
#include <vector>
#include <string>

// 512 bytes is the standard logical sector size for older HDDs, 
// though 4K is common now, NO_BUFFERING requires multiples of sector size.
const DWORD SECTOR_SIZE = 512;
// 10MB buffer aligned for FILE_FLAG_NO_BUFFERING
const DWORD BUFFER_SIZE = 1024 * 1024 * 10; 

struct FileCandidate {
    std::string type;
    uint64_t offset;
};

// Basic Deep Scan carver using magic bytes
void scanBuffer(const uint8_t* buffer, DWORD bytesRead, uint64_t currentOffset, std::vector<FileCandidate>& candidates) {
    // Ensure we don't read out of bounds by subtracting the max signature length
    if (bytesRead < SECTOR_SIZE) return;

    for (DWORD i = 0; i < bytesRead - 4; i += SECTOR_SIZE) {
        // JPEG signature: FF D8 FF
        if (buffer[i] == 0xFF && buffer[i + 1] == 0xD8 && buffer[i + 2] == 0xFF) {
            candidates.push_back({"JPEG", currentOffset + i});
        }
        // PDF signature: %PDF-
        else if (buffer[i] == '%' && buffer[i + 1] == 'P' && buffer[i + 2] == 'D' && buffer[i + 3] == 'F' && buffer[i + 4] == '-') {
            candidates.push_back({"PDF", currentOffset + i});
        }
    }
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <DrivePath, e.g., \\\\.\\C: or \\\\.\\PhysicalDrive0>" << std::endl;
        return 1;
    }

    // Convert char* to wstring for Windows API
    std::string drivePathStr = argv[1];
    std::wstring drivePath(drivePathStr.begin(), drivePathStr.end());
    
    // Strict read-only access bypassing cache
    HANDLE hDrive = CreateFileW(
        drivePath.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_NO_BUFFERING,
        NULL
    );

    if (hDrive == INVALID_HANDLE_VALUE) {
        std::cerr << "Failed to open drive. Error: " << GetLastError() << std::endl;
        return 1;
    }

    // VirtualAlloc is required to allocate memory strictly aligned to sector boundaries for NO_BUFFERING
    uint8_t* buffer = static_cast<uint8_t*>(VirtualAlloc(NULL, BUFFER_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!buffer) {
        std::cerr << "Failed to allocate aligned memory." << std::endl;
        CloseHandle(hDrive);
        return 1;
    }

    std::vector<FileCandidate> candidates;
    uint64_t offset = 0;
    DWORD bytesRead = 0;
    
    // Safety cap for demonstration (scan first 1GB only)
    const uint64_t MAX_SCAN_BYTES = 1024ULL * 1024 * 1024; 

    std::cout << "Starting Raw Sector Deep Scan on " << drivePathStr << "..." << std::endl;
    
    while (offset < MAX_SCAN_BYTES) {
        if (!ReadFile(hDrive, buffer, BUFFER_SIZE, &bytesRead, NULL)) {
            if (GetLastError() == ERROR_HANDLE_EOF) {
                break;
            }
            std::cerr << "Read error at offset " << offset << ". Error: " << GetLastError() << std::endl;
            break;
        }

        if (bytesRead == 0) break;

        scanBuffer(buffer, bytesRead, offset, candidates);
        offset += bytesRead;
    }

    // Strict Memory and Handle Cleanup
    VirtualFree(buffer, 0, MEM_RELEASE);
    CloseHandle(hDrive);

    // Export contract for Member 2
    std::ofstream jsonFile("candidates.json");
    if (jsonFile.is_open()) {
        jsonFile << "[\n";
        for (size_t i = 0; i < candidates.size(); ++i) {
            jsonFile << "  {\n";
            jsonFile << "    \"type\": \"" << candidates[i].type << "\",\n";
            jsonFile << "    \"offset\": " << candidates[i].offset << "\n";
            jsonFile << "  }" << (i < candidates.size() - 1 ? "," : "") << "\n";
        }
        jsonFile << "]\n";
        jsonFile.close();
        std::cout << "Scan complete. Found " << candidates.size() << " candidates. Exported to candidates.json." << std::endl;
    } else {
        std::cerr << "Failed to write candidates.json" << std::endl;
    }

    return 0;
}
