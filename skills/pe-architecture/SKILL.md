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
   from `90_Templates/T_Topology_Trade.md`: weights agreed with the owner, every cell sourced. **Rule 16:**
   efficiency and cost carry the largest weights; every option's efficiency (%) and BoM cost (USD) cells hold a
   Rule-1-anchored number (`forge calc` losses, reference-design data, a priced part list).
4. **Owner's choice by multiple choice**: one question per open choice (topology, device technology, control
   method / MCU family, cooling concept), recommended option first with its score and the key number. The
   recommended option is the most efficient at the lowest cost among those that meet the requirements; when you
   recommend anything else, say which axis it loses, by how much, and why it is better for the product, and open
   the deviation for the owner (`forge objectives deviate "<what>" --gate G3 --axis ...`). Write the choice as
   `**Owner's choice:** <option column>` in the trade; `forge gate check G3` reads it. Use the
   preview field for a small ASCII sketch of each topology when it helps.
5. A choice that is a big decision (architecture, irreversible, cost-moving, sole source) goes to `/council`
   first; the owner then decides with the council record in hand.
6. Record each decision: `01_Program/Decisions/ADR-nnnn_<topic>.md` (`T_ADR.md`), Master Plan §6 decided log,
   CLAUDE.md "Decided architecture" table, and supersede anything it rules out (Rule 4).
7. Gate G3 (`--module M-xx` at module level).
