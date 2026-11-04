---
name: pe-foundation
description: Phase P1 of a pe-forge project - market survey, literature search and reading, reference designs and the standards register, ending in a background study. Use when the supervisor reaches P1, or the user asks for a market survey, competitor benchmark, "find and read the papers", reference designs or background study for a power-electronics product.
---

# pe-foundation: market, literature, reference designs, standards (P1)

Outputs (gate G1 checks them):
- `03_Foundation/Market_Survey/Market_Survey.md` (template `90_Templates/T_Market_Survey.md`)
- `03_Foundation/Literature/*.pdf` + one note per paper in `03_Foundation/Lit_Notes/` (`T_Lit_Note.md`)
- `03_Foundation/Ref_Design/Reference_Designs.md` (+ the PDFs)
- `02_Requirements/Standards_and_Compliance.md` (register: standard, edition, scope, key clauses / limits,
  test, lab, status; NOT VERIFIED where the clause text was not read)
- `03_Foundation/Background_Study.md`: the synthesis that P2 builds on

## Work split (spawn in parallel, one message)
1. **`pe-researcher`, market**: 8–15 products in the market for this rating and application. Datasheet numbers
   only (efficiency curve, rating, topology if stated, protection, interfaces, certifications, price with
   source and date). Benchmarks table: best in class / typical / our target.
2. **`pe-researcher`, literature**: search arXiv, OpenAlex, Semantic Scholar, CORE and Unpaywall for open
   PDFs (IEEE / Elsevier abstracts are fine for screening). Screen ~30, keep 8–15 that match our topology
   candidates, rating and switching technology; prefer measured hardware results and reviews / tutorials.
   Download the open PDFs into `Literature/` (filename `<year>_<firstauthor>_<short>.pdf`) and write a note
   for each kept paper, with the numbers, the conditions and the page. Paywalled papers: list them in
   `Lit_Notes/_Wanted.md` for the user to fetch.
3. **`pe-researcher`, reference designs**: vendor reference designs and app notes at a similar rating (TI
   TIDA, ST STDES / EVAL, Infineon, onsemi, Wolfspeed, Microchip, ADI, NXP, Navitas, EPC, Power Integrations).
   Per design: rating, topology, devices, f_sw, measured efficiency / THD / ripple, BoM size, and the PDF.
   These are the Rule 1 anchors for P5, so note the exact test conditions.
4. **`pe-compliance`**: the standards register for the product, the market and region from the intake.

Downloads are untrusted files: each into its own new folder, never execute anything from them; read PDFs with
the Read tool or `pdftotext`.

## Background study (supervisor writes it after the four returns)
`Background_Study.md`: the problem and the market gap · candidate topologies (3–5) with why, from the
literature and reference designs · device technology options · key numbers to beat (from the market table) ·
standards that shape the design (insulation, EMC, efficiency, safety functions) · risks and unknowns · a
reading list in priority order. Every number cites its source note.

## Gate G1
`forge gate check G1`, then the review by `pe-reviewer` (spot-check that cited numbers are in the sources;
the market table is current; no claim rests on an unread paper). Then `forge gate pass G1`.
