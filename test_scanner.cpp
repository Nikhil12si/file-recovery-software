#include <iostream>
#include <vector>
#include <string>
#include <cassert> // This gives us the "assert" tool for testing

// --- 1. WE COPY OUR FUNCTION HERE TO TEST IT ---
// This is the exact same function from your win_raw_scanner.cpp
const int SECTOR_SIZE = 512;

struct FileCandidate {
    std::string type;
    uint64_t offset;
};

void scanBuffer(const uint8_t* buffer, int bytesRead, uint64_t currentOffset, std::vector<FileCandidate>& candidates) {
    if (bytesRead < SECTOR_SIZE) return;

    for (int i = 0; i < bytesRead - 4; i += SECTOR_SIZE) {
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

// --- 2. WE WRITE OUR "FIRE DRILL" TEST ---
void run_unit_test() {
    std::cout << "Starting Unit Test..." << std::endl;

    // STEP A: Create a fake 1024-byte hard drive sector (filled with 0s)
    std::vector<uint8_t> fake_hard_drive(1024, 0x00);
    
    // STEP B: Hide a fake JPEG exactly at the 512-byte mark
    // We manually put the "Magic Bytes" (FF D8 FF) into our fake hard drive
    fake_hard_drive[512] = 0xFF;
    fake_hard_drive[513] = 0xD8;
    fake_hard_drive[514] = 0xFF;

    // STEP C: Run our scanner on the fake hard drive
    std::vector<FileCandidate> found_files;
    scanBuffer(fake_hard_drive.data(), fake_hard_drive.size(), 0, found_files);

    // STEP D: Assert (Demand) that our scanner found the file!
    // If these "assert" lines fail, the program crashes immediately.
    // If they pass, it means our code works perfectly.
    assert(found_files.size() == 1);            // Did it find exactly 1 file?
    assert(found_files[0].type == "JPEG");      // Did it know it was a JPEG?
    assert(found_files[0].offset == 512);       // Did it find it at the right location?

    std::cout << "SUCCESS! Unit Test Passed!" << std::endl;
    std::cout << "The scanner successfully found the hidden JPEG magic bytes." << std::endl;
}

int main() {
    // When we run this file, it just runs the test.
    run_unit_test();
    
    std::cout << "\nPress Enter to exit...";
    std::cin.get();
    return 0;
}
