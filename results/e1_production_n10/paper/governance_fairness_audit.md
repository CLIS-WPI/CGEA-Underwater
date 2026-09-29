# Governance traffic fairness (why B3/B4/B5 share governance TX)

## Observation

In `e1_production_n10`, `governance_tx_bytes` are identical across B3, B4, and B5 variants for a given outage (paired Δ governance TX = 0). Mission/DATA TX is **not** identical (B4−B5_nominal ≈ +6075 B).

## Mechanism

Governance packets are sent in `src/cgea/experiments/runner.py` `mission_loop`, **not** inside baseline `decide()`:

- Connected refresh: if `gateway_reachable` and `now - last_auth > authority_refresh_s` (60 s), issue capsule and `PacketType.AUTHORITY` (`authority_capsule_bytes` = 256).
- After outage: per-AUV DIGEST / PROVENANCE / RECONCILE / AUTHORITY (`configs/governance/cgea.yaml` reconciliation sizes 128/256/128/256).

`src/cgea/network/__init__.py` `GOVERNANCE_TX_TYPES` counts those bytes as `governance_tx`. The same persisted GPU-PHY trace is replayed for every baseline (`ensure_trace` / `forbid_trace_generation`).

Baseline controllers only return ALLOW/DENY/DEFER. They do not emit governance packets. Reconciliation is not conditioned on whether a consequential action was allowed.

Therefore governance TX is **shared by design** (same runner transport + same trace + same refresh/recon schedule), not a post-hoc replay of another baseline’s byte log.

## Why DATA can still differ

DATA (telemetry / task_coord / anomaly_report) is also sent from the runner every tick, but `execute()` after ALLOW can change world/connectivity and thus later `gateway_reachable` / neighbor `dst`, which changes DATA volume. That path explains unequal `mission_tx_bytes` with equal `governance_tx_bytes` if refresh/recon still see the same reachability pattern for AUTHORITY/DIGEST.
