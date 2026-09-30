# E4 TEST validation

**Parent SHA:** `a3665bfe38caa12bd45b6125db3899835fab232d`
**Primary runs:** 2520
**Local secondary runs:** 60
**TEST seeds:** [5, 6, 7, 8, 9]
**Case:** E4-INTERIOR

- forbid_trace_generation: true
- no DEV seeds 0–4
- one controlled proposal per cell
- local events not pooled into primary rates
- budgets frozen 40/60/60/300/600

## Local secondary allow rates

- B0: allow_rate=1.0000 reasons=['ALLOW_LOW_RISK']
- B1: allow_rate=1.0000 reasons=['ALLOW_LOW_RISK']
- B2: allow_rate=0.0000 reasons=['DENY_FORBIDDEN']
- B3: allow_rate=1.0000 reasons=['ALLOW_LOW_RISK']
