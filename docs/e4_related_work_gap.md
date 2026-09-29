# E4 related-work gap (pre-implementation)

This is a **literature-positioning note**, not a claim of novelty. Overlap with existing CGEA (capsules, age bands, conditional reassignment, E3 leases) is large. Action-dependent evidence is an **incremental** authorization predicate, not a new theory of underwater networking.

## 1. Time-bounded permissions / leases

**Solves:** revoke or expire a right after TTL without a live issuer.  
**CGEA overlap:** E3 `fixed_expiry_v1` (400 s locked) and CGEA aging/hard expiry are already leases on *the capsule* or *one action class*.  
**What evidence-authority would add:** TTL attached to **named evidence objects** (peer liveness vs assignment), not one clock on the whole grant.  
**Risk:** reviewers will read this as “another lease.” The paper must show *different evidence types, different budgets*, not a third global TTL.

## 2. Simplex / runtime assurance (Seto, Sha, et al.)

**Solves:** keep a high-assurance envelope around a high-performance controller.  
**CGEA overlap:** the four frozen hard-safety forbids are already a Simplex-like **static** envelope.  
**Add:** evidence budgets are *not* the safety envelope. They must not relax Simplex.  
**Do not claim:** E4 is Simplex. It is an authorization filter inside an already-assured forbid set.

## 3. Capability-based authorization

**Solves:** unforgeable tokens naming *what* may be done.  
**CGEA overlap:** `AuthorityCapsule` is already a capability (grants, geofence, energy ceiling).  
**Add:** capabilities become **conditional on local evidence records**, not only on token age.  
**Do not claim:** object-capability OS or crypto unforgeability. Capsules remain simulator objects.

## 4. Age of Information (AoI)

**Solves:** quantify staleness of a *status process* at a monitor.  
**CGEA overlap:** authority age is a single AoI of the capsule.  
**Add:** **per-type AoI** (`PEER_AVAILABILITY` vs `LOCAL_NAVIGATION`) with action-specific thresholds.  
**Do not claim:** optimal AoI scheduling or new AoI theory. Thresholds are declared engineering bounds.

## 5. Attribute / evidence-based access control (ABAC, XACML-like)

**Solves:** permit if attributes of subject, resource, and environment satisfy a policy.  
**CGEA overlap:** `conditional_ok` is a thin attribute check (failed target, mandatory, recruits).  
**Add:** attributes are **timestamped local records** with missing/stale behaviors, not live global attributes.  
**Do not claim:** a general ABAC engine or standards compliance.

## 6. Context-aware authorization

**Solves:** permissions change with location, role, or threat.  
**CGEA overlap:** connectivity classifier already changes *refresh opportunity*, not a context RBAC.  
**Add:** context enters only as **whether evidence can be refreshed**, not as a hidden permission table.  
**Do not claim:** situation calculus or military context policy languages.

## 7. Distributed mission ownership / consensus

**Solves:** one owner of a task under partitions (Paxos/Raft, CRDTs, locks).  
**CGEA overlap:** none implemented; E2 obsolete override is the *absence* of this.  
**Add:** **nothing** in E4. Explicit non-goal.  
**Do not claim:** exclusive ownership or conflict-free assignment.

## Positioning sentence (use only if E4 TEST later supports it)

CGEA already combines a static safety envelope with time-bounded capsules. The proposed extension is a **small ABAC/AoI layer**: each action names which local records must exist and how old they may be, while global truth stays in the oracle. That is a **gap vs a single capsule clock**, not a gap vs the entire authorization literature.

## Novelty-risk

High risk of “this is just ABAC + leases.” Mitigate by (1) keeping the matrix tiny and frozen, (2) keeping hard-safety separate, (3) reporting E2-F fresh-but-wrong as a limitation, (4) not claiming dominance over E3’s 400 s lease as a design goal.
