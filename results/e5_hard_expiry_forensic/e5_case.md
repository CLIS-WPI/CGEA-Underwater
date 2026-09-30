# CASE H2

LOW-RISK DENIAL, NON-COLLISION — at least one LOW_RISK action denied due to hard-expiry contraction; no collision-avoidance denial.

- N(B4 ∧ LOW_RISK ∧ HARD_EXPIRED/age>=900 ∧ DENY ∧ contraction) = 2352
- collision_avoidance at age>=900: 0
- collision_avoidance denied at age>=900: 0
- No production B4 collision_avoidance proposal occurred at authority_age >= 900.

Do not implement a policy fix in this audit.
