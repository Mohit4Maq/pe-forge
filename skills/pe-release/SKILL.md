---
name: pe-release
description: Phase P8 of a pe-forge project - the manufacturing release - ERC / DRC gate, Gerbers, drill, BoM, pick-and-place, assembly drawings, STEP, fab and assembly notes, distributor / fab-house upload files, the design-verification test plan, and a tagged release. Use when the supervisor reaches P8, or the user asks for manufacturing files, Gerbers, a fab pack, JLCPCB / PCBWay files or a release.
---

# pe-release: manufacturing files (P8)

1. **Freeze**: every module passed G6, the board passed G7, no OPEN QC row, `forge consistency` = 0, BoM complete
   (`forge bom check`), BoM in the schematic = WIP BoM.
2. **Outputs**: in `08_PCB/<slug>/` run `./release.sh` (ERC + DRC + library checks + BoM gate, then the KiCad
   jobset: schematic PDF, Gerbers, drill, pick-and-place, STEP, assembly PDF, fab zip). Copy the result to
   `09_Release/<REV>/` with subfolders `gerbers/`, `drill/`, `bom/`, `assembly/`, `3d/`, `docs/`.
3. **Fab-house files** if asked: JLCPCB BoM + CPL (`kicad-happy:jlcpcb`, LCSC numbers), PCBWay turnkey BoM
   (`kicad-happy:pcbway`); distributor upload files from `bom.py --boards N`.
4. **Release notes** `09_Release/<REV>/Release_Notes.md`: board P/N and rev, stack-up and fab notes (material,
   finish, copper, impedance, special processes), assembly notes (polarised parts, hand-solder items, potting,
   torque), BoM cost at the build quantity, known issues and deviations, the gate records.
5. **Validation**: `pe-validation` writes or updates `13_Validation/DVP_and_R.md` (bring-up order with safe
   limits, every requirement → test procedure → pass criterion) and the bring-up checklist.
6. **Review** (T4: irreversible): `pe-reviewer` + `pe-validation` + `pe-compliance`. Then `forge gate pass G8`.
7. **Tag** `<slug>-rev<REV>` (annotated) after the owner approves. Ordering boards and parts is the owner's
   decision: hand them the pack and the quotes, never order.
