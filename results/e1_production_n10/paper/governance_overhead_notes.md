# Governance overhead (existing production; no retune)

Frozen sizes (not changed):
- AUTHORITY / capsule: 256 B
- DIGEST: 128 B
- PROVENANCE: 256 B
- RECONCILE: 128 B
- connected refresh: 60 s

Per-type send counts are not stored in production extras. Mission-level `governance_tx_bytes`, `mission_tx_bytes`, `total_tx_bytes`, and GCO are in `e1_production_n10_raw.csv` / `paper_results_table.csv`.

Communication energy **is** modeled as `energy_communication_j` on each run JSON (byte-based, not a separate acoustic energy PHY). It is not a first-principles modem energy estimate.

Do not retune message sizes or refresh from these numbers.
