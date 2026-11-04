## Simulation Verification Record
| Item | Value |
|---|---|
| Model provenance | tool + version, netlist / script path, device models and their source |
| Anchor case (same model) | reference used, its published numbers, our result, agreement % |
| Convergence | `.log` clean? pwrlab `usable` / `evidence_eligible` |
| Timestep / tolerance check | halved tmax or tightened reltol: change in the key result |
| Energy / KCL closure | Pin − Pout − losses = ? (% of Pin) |
| Corners covered | every range end in project.toml [corners] (list them) |
| Reproduce | command line that regenerates the number |
| SIM-QC verdict (reviewer) | ACCEPTED / PROVISIONAL / REJECTED |
