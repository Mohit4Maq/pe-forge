---
name: pe-architecture
description: Phase P3 of a pe-forge project - candidate circuits and topologies, a weighted trade study backed by literature, reference designs and quick pwrlab estimates, the owner's choice by multiple choice, and ADRs. Runs at system level and per module. Use when the supervisor reaches P3, or the user asks which topology / circuit / architecture to use.
---

# pe-architecture: plan the circuits and choose (P3)

1. **Candidates (3–5)** from the background study and reference designs. For each: circuit sketch (text or a
   Falstad link via `falstad-circuit` for intuition), device count and stress at the worst corner, magnetics
   count, control complexity, known reference designs and measured efficiency at similar rating.
2. **Quick numbers** with closed form first, then `pwrlab` (`analyze` / `losses` for supported topologies:
   buck, DAB, H-bridge inverter, T-type / NPC, totem-pole PFC) at both corner ends. These are estimates for the
   trade, labelled PROVISIONAL; P5 does the verified simulations.
3. **Trade study** in `04_Hardware/Architecture/Topology_Trade.md` (module: `modules/M-xx/Topology_Trade.md`)
   from `90_Templates/T_Topology_Trade.md`: weights agreed with the owner, every cell sourced.
4. **Owner's choice by multiple choice**: one question per open choice (topology, device technology, control
   method / MCU family, cooling concept), recommended option first with its score and the key number. Use the
   preview field for a small ASCII sketch of each topology when it helps.
5. A choice that is a big decision (architecture, irreversible, cost-moving, sole source) goes to `/council`
   first; the owner then decides with the council record in hand.
6. Record each decision: `01_Program/Decisions/ADR-nnnn_<topic>.md` (`T_ADR.md`), Master Plan §6 decided log,
   CLAUDE.md "Decided architecture" table, and supersede anything it rules out (Rule 4).
7. Gate G3 (`--module M-xx` at module level).
