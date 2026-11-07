# Third-party software

pe-forge bundles its own code only. These are installed separately (bootstrap.sh checks for them) and keep
their own licences:

| Component | Used for | Licence | Get it |
|---|---|---|---|
| KiCad 10 | schematic, PCB, ERC / DRC, outputs | GPL-3.0 (libraries CC-BY-SA 4.0 + exception) | kicad.org |
| LTspice | simulation engine behind pwrlab | Analog Devices freeware licence (not redistributable) | analog.com/ltspice |
| Wine (Linux) | runs LTspice on Linux | LGPL-2.1 | winehq.org |
| ngspice (optional) | fallback analog checks | BSD-3-Clause | ngspice.sourceforge.io |
| spicelib, numpy, pandas, matplotlib, mcp | pwrlab Python deps (pip) | see each package | PyPI |
| PyMuPDF (optional) | renders, text-placement checks | AGPL-3.0 / commercial | PyPI |
| SQLite ODBC driver | KiCad database library | BSD-style | ch-werner.de/sqliteodbc |
| Claude Code | runs the skills and agents | Anthropic terms | claude.com/claude-code |
| kicad-happy plugin (optional) | distributor, EMC, JLCPCB / PCBWay skills | see that plugin | Claude Code plugin marketplace |

Benchmarks in `vendor/pwrlab/benchmarks/` quote published specification numbers from public vendor reference
design documents (TI, ST) with their URLs; the documents themselves are not included.

## Design ideas

Design ideas adapted from copperhead (Apache-2.0), github.com/copperheadhq/copperhead: re-implemented in Python,
no code copied. See `docs/PLAN_copperhead_features.md`.
