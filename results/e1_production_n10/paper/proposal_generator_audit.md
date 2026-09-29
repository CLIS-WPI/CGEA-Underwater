# Proposal generator audit (frozen code/config; no retune)

## Verdict

The forbidden-heavy mix is **intentionally stress-injected** by a mission-conditioned planner plus a **predeclared disconnected-cohort challenge schedule**. It is not a random periodic draw and not a learned planner.

## Evidence

1. `src/cgea/agent/__init__.py` `MissionPlanner` (lines 28–34):
   - Comment: consequential proposals come from mission conditions (failed AUV, reserve pressure, hazardous segment, unresolved leak), **not periodic random injection**.
   - Priority order: reserve → freshness challenge → isolated abort → recover failed AUV → abandon hazardous → exclusion shortcut → low-risk scan.

2. `configs/mission/pipeline.yaml` `workload: stress`.

3. `src/cgea/mission/__init__.py` `build_pipeline_mission` (`workload=="stress"`):
   - failed AUV `auv_09` (far half), energy-shock `auv_08`, hazardous `seg_04`, exclusion around the shock AUV, abort flag on `auv_11`.
   - Comment: stressors placed so the mid-fleet partition isolates them from the gateway; hazardous kept gateway-side so abandon is not pre-empted.

4. `configs/mission/freshness_challenge.yaml`:
   - Cohort `disconnected_far_half` (`auv_06`…).
   - Times `[220, 400, 640, 1140]`.
   - Cycle: reassign → exclusion → abandon → reserve.
   - Comment: intended to exercise measured freshness bands. Header: predeclared before E1-v3 B4 inspection.

5. Challenge override (`MissionPlanner._freshness_challenge`): if the AUV is in the cohort, disconnected, and `t` is in `[t0, t0+sim_tick)`, it **replaces** the ordinary proposal with the cycled consequential class.

There is **no probability table**. Generation is deterministic given world state and the frozen schedule.

## Relation to governor-boundary exercise

The challenge file states it is a planning aid to hit freshness bands. The stress workload comments state it creates consequential **opportunities**. Together they are intended to exercise governor boundaries (forbidden / conditional / freshness). The high `DENY_FORBIDDEN` count follows from those classes being proposed often and statically forbidden under `paper_risk_bounded_v1_2026-09-28`.
