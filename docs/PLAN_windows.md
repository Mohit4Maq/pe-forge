# pe-forge on Windows: assessment and plan

Status 2026-11-09: W1-W3 done and tested in a Windows 11 ARM64 VM (UTM): the full test suite runs **85 passed,
0 failed, 61 skipped** (skips need KiCad / LTspice, not yet installed there); macOS stays 146 / 146. Fixed on the way:
launchers (python / py, Windows venv), UTF-8 mode and output, LF writes, forward-slash paths, case-insensitive
project paths for hooks, module folders in state.json, byte-exact transaction snapshots, the status-line restore,
and a security issue: owner-only `forge cr approve` trusted a NUL stdin as a terminal on Windows. Next: W4 (KiCad,
LTspice in the VM), W5 (install.ps1), W7 (Windows CI).

W4 (2026-11-10): KiCad 10.0.8 (native ARM64 build) found automatically (`Program Files\KiCad\<version>`, its own
Python with pcbnew). With KiCad the suite runs **124 passed, 0 failed, 22 skipped** (ngspice not installed; the macOS
installer test). Found and fixed: schematic text checks crashed without PyMuPDF (now SKIP, advice only); pwrlab took
a Windows LTspice path through POSIX shell rules. **LTspice: ADI's installer refuses Windows on ARM64** ("not
supported on ARM64"); unpacked and run under x64 emulation it does not complete a batch run (as SYSTEM in session 0,
or as the user). pwrlab's Windows plumbing (native paths, timeout kill with taskkill) is verified; the LTspice run
itself must be verified on x64 Windows (W7 CI on windows-latest). PyMuPDF has no Windows ARM64 build either.

## 1. Where pe-forge stands

pe-forge is mostly portable already. The engine is standard-library Python and the gates, checks, V-model, review
forms, rule book, BoM / supply chain and learning loop are plain files plus `kicad-cli`, which ships on Windows. The
work is in the edges: install, launching, a handful of macOS-only calls, and testing on Windows.

| Area | Today (macOS / Linux) | On Windows | Effort |
|---|---|---|---|
| Claude Code | native | native; it needs Git for Windows and runs hooks and its Bash tool in Git Bash | none |
| `bin/forge` launcher (all 7 hooks call it) | `#!/bin/sh`, `python3`, `.venv/bin/python` | runs in Git Bash, but Windows has `python` / `py` and `.venv\Scripts\python.exe` | small |
| Installer | `install.sh` / `bootstrap.sh` with Homebrew, a `~/.local/bin` symlink | needs `install.ps1`: winget for Python, Git, KiCad 10, Obsidian, LTspice, ngspice, gh; Claude Code's own Windows installer; a `forge.cmd` shim on PATH | medium |
| KiCad discovery (`forge/kicad.py`) | macOS app and Linux paths | add `C:\Program Files\KiCad\<ver>\bin\kicad-cli.exe` and KiCad's bundled `python.exe` (pcbnew) | small |
| LTspice (pwrlab) | macOS: Windows LTspice in a Wine bottle; paths sent as `Z:` paths | **done 2026-11-09:** pwrlab picks the path style (native Windows path, WSL `wslpath -w` for the Windows LTspice.exe, Wine `Z:`), `PWRLAB_PATH_STYLE` / `PWRLAB_WORKROOT` overrides, and a Windows-safe timeout kill (`taskkill /T`). Still to do: a real run on Windows and on WSL | bench run left |
| Text output | UTF-8 terminals | the classic console code page cannot print `▸ ✓ ✗ ⚠ ▄█`; output can crash with UnicodeEncodeError | small: switch stdout / stderr to UTF-8 at start-up, `[ui] glyphs = "ascii"` fallback |
| Opening files / apps | `open`, `osascript`, `pgrep` (6 modules) | `os.startfile`, `tasklist` / `taskkill`; Obsidian quit and restart | small |
| Secrets | Mouser key in macOS Keychain or `~/.mouser_key` | Windows Credential Manager (or `MOUSER_API_KEY` / the key file), never printed | small |
| Caches and registries | `~/Library/Caches`, `~/Library/Application Support` | `%LOCALAPPDATA%` / `%APPDATA%` (the Obsidian registry path already handles Windows) | small |
| Status line chaining | runs the previous status line with `shell=True` | works through `cmd`; test with a PowerShell-based previous line | small |
| KiCad plugin / release scripts | `mc_action.sh`, `release.sh` (bash) | PowerShell twins or a Python port | medium |
| Line endings | LF | Git's autocrlf can rewrite KiCad files, scripts and test fixtures: add `.gitattributes` (`* text=auto eol=lf`, KiCad files binary-safe) | small |
| Demo kit | tmux + Terminal + FFmpeg avfoundation | not needed for customers; a Windows Terminal + `ffmpeg -f gdigrab` version later if wanted | optional |
| CI | the projects' GitHub Actions run on Ubuntu | add a `windows-latest` job running the 146 tests with KiCad installed | small, the biggest safeguard |

## 2. Options

1. **Native Windows (recommended).** pe-forge, KiCad, LTspice and Obsidian all native; Claude Code native with Git
   Bash. Best for customers: one installer, KiCad and LTspice are Windows-first tools, and no path translation.
2. **WSL2.** pe-forge in Linux, KiCad / LTspice / Obsidian on Windows. Every path crosses `/mnt/c` ↔ `C:\`, KiCad's
   pcbnew Python lives on the Windows side, and file watching across the boundary is slow. Possible for power users,
   not for a trial.
3. **Linux VM or remote machine.** Works today, but customers would not use their own KiCad.

## 3. Plan (native Windows)

| Step | What | Done when |
|---|---|---|
| W1 | `forge/platform.py`: one place for open-file, process check / quit, cache and config dirs, secret store, executable lookup; replace the 6 `open`, `osascript`, `pgrep`, Keychain and `~/Library` call sites | macOS tests unchanged |
| W2 | UTF-8 console at start-up (`sys.stdout.reconfigure(encoding="utf-8")`), automatic ASCII glyphs where the console cannot draw blocks | `forge status --banner` prints in cmd, PowerShell and Windows Terminal |
| W3 | Launcher: `bin/forge` finds `python` / `py` / `.venv\Scripts\python.exe` under Git Bash; add `bin/forge.cmd` for cmd / PowerShell; hooks keep calling `bin/forge` (Git Bash) | all 7 hooks fire in a Windows Claude Code session |
| W4 | KiCad 10 Windows paths for `kicad-cli` and KiCad's Python; pwrlab: native `LTspice.exe` with plain paths | ERC / DRC, `forge pcb …`, and a pwrlab buck benchmark run on Windows |
| W5 | `install.ps1`: winget installs (Python, Git, KiCad, Obsidian, LTspice, ngspice, gh, ffmpeg), Claude Code install, venv, PATH shim, Claude Code plugins, KiCad library registration, `forge doctor` at the end | a clean Windows 11 machine goes from nothing to `forge doctor` green in one command |
| W6 | `.gitattributes`; PowerShell or Python versions of `mc_action.sh` and `release.sh` | release outputs identical on both systems |
| W7 | CI: `windows-latest` job (KiCad via winget or choco) running the full test suite | green on every push |
| W8 | Pilot: build the Buck48to12 flow end to end on a Windows machine (schematic check, PCB chain, rule book, BoM / supply, panel) | same results as on macOS; differences filed with `forge issue` |

Order: W1-W3 make the core usable; W4 makes the hardware tools work; W5 makes it installable; W7 keeps it working.
W6 and the demo kit can follow.

## 4. Risks and open questions

- **KiCad's Python on Windows:** pe-forge drives pcbnew through KiCad's own interpreter. Its location and DLL search
  path differ by KiCad version; this needs testing on KiCad 10.
- **LTspice batch mode on Windows:** check `-Run -b` behaviour, the log file encoding (UTF-16) and that serial runs
  stay reliable, against the macOS anchors (pwrlab benchmarks).
- **Antivirus and SmartScreen:** unsigned scripts and a PATH shim can be blocked; signing or clear instructions.
- **Paths:** spaces in `C:\Program Files` and OneDrive-synced Documents folders (file locks, sync conflicts like the
  macOS "name 2.py" copies).
- **Corporate machines:** winget may be disabled; the installer needs a manual-download fallback list.
- **Who tests it:** a Windows 11 machine (or VM) is needed for W8 and for the first customer trial.

## 5. Decision for the owner

Support native Windows (option 1) for the customer trial, starting with W1-W4 plus CI (W7), then the installer
(W5). Confirm: target Windows 11 only, or Windows 10 too; and whether a Windows test machine or VM is available.
