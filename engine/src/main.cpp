// =============================================================================
// main.cpp  —  CLI entry point for win_recovery_engine.exe
// -----------------------------------------------------------------------------
// Contract: contracts/README.md ("Engine CLI contract").
//
//   win_recovery_engine list-drives
//   win_recovery_engine check-elevation
//   win_recovery_engine validate-dest --dest E:\Recovered --source D: [--source ...] [--required-bytes N]
//   win_recovery_engine scan --mode quick|deep --scope all|drive|folder
//                            [--source D: | --source \\.\PhysicalDrive1 | --source image:C:\disk.dd]...
//                            [--folder "D:\Users\me\Documents"] --out C:\work\candidates.json
//                            [--sample-dir E:\staging] [--sample-bytes N] [--sample-budget-mb N]
//                            [--types jpg,pdf,docx] [--max-candidates N] [--cancel-file PATH]
//                            [--byte-aligned] [--unallocated-only] [--allow-non-hdd]
//                            [--no-verify] [--include-empty] [--stream-candidates] [--scan-id ID]
//   win_recovery_engine recover --manifest manifest.json [--cancel-file PATH]
//   win_recovery_engine signatures
//   win_recovery_engine version
//
// stdout carries NDJSON events (scan/recover) or a single JSON document (the
// other commands). Human-readable help goes to stderr.
// =============================================================================
#include <cstdio>
#include <string>
#include <vector>

#include "engine_common.hpp"
#include "win_drive_manager.hpp"
#include "win_recovery_engine.hpp"
#include "win_security_guard.hpp"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#else
#include <csignal>
#endif

using namespace re;

namespace {

void PrintUsage() {
    std::fprintf(stderr,
                 "%s %s - Windows HDD file recovery engine (NTFS / FAT32)\n\n"
                 "Commands:\n"
                 "  list-drives                     Enumerate physical drives and volumes (JSON)\n"
                 "  check-elevation                 Report Administrator elevation (JSON)\n"
                 "  validate-dest --dest P --source S [--source S2] [--required-bytes N]\n"
                 "  scan --mode quick|deep --scope all|drive|folder [--source S]... [--folder P] --out FILE [options]\n"
                 "  recover --manifest FILE [--cancel-file F]\n"
                 "  signatures                      List supported carving signatures (JSON)\n"
                 "  version\n\n"
                 "Scan options:\n"
                 "  --sample-dir DIR  --sample-bytes N  --sample-budget-mb N  --types jpg,pdf\n"
                 "  --max-candidates N  --cancel-file F  --byte-aligned  --unallocated-only\n"
                 "  --allow-non-hdd  --no-verify  --include-empty  --stream-candidates  --scan-id ID\n",
                 kEngineName, kEngineVersion);
}

struct Args {
    std::string command;
    std::vector<std::pair<std::string, std::string>> kv;
    std::vector<std::string> flags;

    std::string get(const std::string& k, const std::string& def = "") const {
        for (auto it = kv.rbegin(); it != kv.rend(); ++it) if (it->first == k) return it->second;
        return def;
    }
    std::vector<std::string> all(const std::string& k) const {
        std::vector<std::string> v;
        for (const auto& p : kv) if (p.first == k) v.push_back(p.second);
        return v;
    }
    bool has(const std::string& f) const { return std::find(flags.begin(), flags.end(), f) != flags.end(); }
};

const std::vector<std::string> kValueOptions = {"--mode", "--scope", "--source", "--folder", "--out", "--sample-dir",
                                                "--sample-bytes", "--sample-budget-mb", "--types", "--max-candidates",
                                                "--cancel-file", "--scan-id", "--manifest", "--dest", "--required-bytes"};

bool ParseArgs(const std::vector<std::string>& argv, Args& out, std::string& err) {
    if (argv.size() < 2) { err = "missing command"; return false; }
    out.command = argv[1];
    for (size_t i = 2; i < argv.size(); ++i) {
        const std::string& a = argv[i];
        if (std::find(kValueOptions.begin(), kValueOptions.end(), a) != kValueOptions.end()) {
            if (i + 1 >= argv.size()) { err = "option " + a + " requires a value"; return false; }
            out.kv.emplace_back(a, argv[++i]);
        } else if (StartsWith(a, "--")) {
            out.flags.push_back(a);
        } else {
            err = "unexpected argument: " + a;
            return false;
        }
    }
    return true;
}

uint64_t ParseU64(const std::string& s, uint64_t def) {
    if (s.empty()) return def;
    char* end = nullptr;
    unsigned long long v = std::strtoull(s.c_str(), &end, 10);
    return (end && *end == '\0') ? static_cast<uint64_t>(v) : def;
}

void PrintJson(const std::string& s) {
    std::fwrite(s.data(), 1, s.size(), stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
}

#ifdef _WIN32
BOOL WINAPI CtrlHandler(DWORD type) {
    if (type == CTRL_C_EVENT || type == CTRL_BREAK_EVENT || type == CTRL_CLOSE_EVENT) {
        RequestCancel();
        return TRUE;
    }
    return FALSE;
}
#else
void SigHandler(int) { RequestCancel(); }
#endif

int Run(const std::vector<std::string>& argv) {
    Args args;
    std::string err;
    if (!ParseArgs(argv, args, err)) {
        std::fprintf(stderr, "error: %s\n\n", err.c_str());
        PrintUsage();
        return kExitUsage;
    }
    DriveManager dm;
    const std::string& cmd = args.command;

    if (cmd == "version") {
        JsonWriter w;
        w.BeginObject().KV("name", kEngineName).KV("version", kEngineVersion).KV("schema_version", kSchemaVersion).EndObject();
        PrintJson(w.str());
        return kExitOk;
    }
    if (cmd == "help" || cmd == "--help" || cmd == "-h") {
        PrintUsage();
        return kExitOk;
    }
    if (cmd == "signatures") {
        PrintJson(SignaturesJson());
        return kExitOk;
    }
    if (cmd == "check-elevation") {
        PrintJson(SecurityGuard::ElevationJson(SecurityGuard::CheckElevation()));
        return kExitOk;
    }
    if (cmd == "list-drives") {
        JsonWriter w(args.has("--pretty"));
        w.BeginObject()
            .KV("schema_version", kSchemaVersion)
            .KV("generated_at", NowIso())
            .KV("engine_version", kEngineVersion)
            .Key("elevation").Raw(SecurityGuard::ElevationJson(SecurityGuard::CheckElevation()));
        dm.WriteInventory(w);
        w.EndObject();
        PrintJson(w.str());
        return kExitOk;
    }
    if (cmd == "validate-dest") {
        std::vector<SourceDescriptor> sources;
        for (const auto& spec : args.all("--source")) {
            auto d = dm.Resolve(spec, &err);
            if (!d) {
                EventSink::Instance().Error("SOURCE_NOT_FOUND", err, true);
                return kExitSourceError;
            }
            sources.push_back(*d);
        }
        auto chk = SecurityGuard::ValidateDestination(args.get("--dest"), sources, ParseU64(args.get("--required-bytes"), 0));
        PrintJson(SecurityGuard::DestinationJson(chk));
        return chk.safe ? kExitOk : kExitUnsafeDestination;
    }
    if (cmd == "scan") {
        ScanOptions o;
        o.mode = ToLowerAscii(args.get("--mode", "quick"));
        o.scope = ToLowerAscii(args.get("--scope", "drive"));
        o.sourceSpecs = args.all("--source");
        o.folderPath = args.get("--folder");
        o.outFile = args.get("--out", "candidates.json");
        o.sampleDir = args.get("--sample-dir");
        o.sampleBytes = ParseU64(args.get("--sample-bytes"), o.sampleBytes);
        o.sampleBudgetBytes = ParseU64(args.get("--sample-budget-mb"), o.sampleBudgetBytes / (1024 * 1024)) * 1024 * 1024;
        o.maxCandidates = ParseU64(args.get("--max-candidates"), o.maxCandidates);
        o.cancelFile = args.get("--cancel-file");
        o.scanId = args.get("--scan-id");
        o.sectorAligned = !args.has("--byte-aligned");
        o.unallocatedOnly = args.has("--unallocated-only");
        o.allowNonHdd = args.has("--allow-non-hdd");
        o.verifyHeaders = !args.has("--no-verify");
        o.includeZeroLength = args.has("--include-empty");
        o.streamCandidates = args.has("--stream-candidates");
        std::string types = args.get("--types");
        size_t s = 0;
        while (s < types.size()) {
            size_t e = types.find(',', s);
            if (e == std::string::npos) e = types.size();
            std::string t = ToLowerAscii(types.substr(s, e - s));
            while (!t.empty() && t[0] == '.') t.erase(t.begin());
            if (!t.empty()) o.typeFilter.insert(t);
            s = e + 1;
        }
        if (o.mode != "quick" && o.mode != "deep") {
            EventSink::Instance().Error("USAGE", "--mode must be quick or deep", true);
            return kExitUsage;
        }
        if (o.scope != "all" && o.scope != "drive" && o.scope != "folder") {
            EventSink::Instance().Error("USAGE", "--scope must be all, drive or folder", true);
            return kExitUsage;
        }
        if (o.scope == "drive" && o.sourceSpecs.empty()) {
            EventSink::Instance().Error("USAGE", "--source is required for drive scope", true);
            return kExitUsage;
        }
        return RunScan(o, dm);
    }
    if (cmd == "recover") {
        std::string manifest = args.get("--manifest");
        if (manifest.empty()) {
            EventSink::Instance().Error("USAGE", "--manifest is required", true);
            return kExitUsage;
        }
        return RunRecover(manifest, args.get("--cancel-file"), dm);
    }
    std::fprintf(stderr, "error: unknown command '%s'\n\n", cmd.c_str());
    PrintUsage();
    return kExitUsage;
}

}  // namespace

#ifdef _WIN32
int wmain(int argc, wchar_t** wargv) {
    // NDJSON must not be mangled by CRLF translation.
    _setmode(_fileno(stdout), _O_BINARY);
    SetConsoleCtrlHandler(CtrlHandler, TRUE);
    std::vector<std::string> argv;
    for (int i = 0; i < argc; ++i) argv.push_back(WideToUtf8(wargv[i]));
    try {
        return Run(argv);
    } catch (const std::exception& e) {
        EventSink::Instance().Error("INTERNAL", e.what(), true);
        return kExitInternal;
    }
}
#else
int main(int argc, char** argv) {
    std::signal(SIGINT, SigHandler);
    std::signal(SIGTERM, SigHandler);
    std::vector<std::string> args(argv, argv + argc);
    try {
        return Run(args);
    } catch (const std::exception& e) {
        EventSink::Instance().Error("INTERNAL", e.what(), true);
        return kExitInternal;
    }
}
#endif
