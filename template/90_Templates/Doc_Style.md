---
type: style-guide
rev: 0.1
status: live
---
# How a project document is written (read by the owner, a reviewer, a customer)

`forge docs lint` measures these rules (D1 to D8) and the reviewer grades them. A document is a record someone
must understand in one reading, not a log of everything the author knew.

1. **Open with a summary** (D1): under 90 words after the title: what this is, the three to five key numbers with
   units, the status (draft / reviewed / superseded) and what is still open. Sources, revisions and abbreviations
   come *after* the summary, in their own tables.
2. **One idea per paragraph, under 150 words** (D2). A reader should be able to stop after any paragraph.
3. **Tables fit the page** (D3): at most 7 columns, no row over 140 characters. A cell holds one value or one short
   phrase; the reasoning that does not fit goes in a note under the table or in an appendix.
4. **Show it** (D4): every circuit is a schemdraw figure (`forge fig new <name>`, `forge fig render`, embedded as
   SVG); every scan, sweep or corner comparison is a plot (the script that computes it saves the PNG beside the
   document in `figures/`). A table of 50 rows is data for the appendix; the figure is what the reader sees.
5. **Cite once, by number** (D5): a sources table near the end (`| # | Source | Revision / page |`), then `[3]` or
   "(source 3, p. 6)" in the text at most twice per 100 words. Never a private key soup ("R5 p.6, TT §4, DC §8.1").
6. **Stay within the budget** (D6): booklet 2000 words, calculations 4000, trade study 3000, simulation result
   3000, sheet brief 1500 (a `full` profile doubles these). Derivations live in the script and its output file;
   history lives in the revision table; what a reviewer needs to *check* lives in an appendix.
7. **Prose, not code** (D7): a netlist written in backticks is not a circuit description. Draw it, then say in words
   what each part does and why its value is what it is.
8. **Subheadings every 600 words** (D8), numbered, so a finding can point at §3.2.
9. **Units and anchors on every number**: "0.897 mA (window edge, source 2 p. 4, MATCH)". A number without a unit
   or an anchor is a Rule 1 finding, not a style one.
10. **Expand every abbreviation at first use** and keep a glossary table when there are more than five.
11. **Obsidian is the reader**: wiki links to IDs (`[[D-07]]`), callouts for warnings (`> [!warning]`), figures as
    relative paths; nothing that only renders on GitHub.

The template files in `90_Templates/` already follow this shape: keep their section order and add figures.
