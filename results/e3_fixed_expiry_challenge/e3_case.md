# E3 case

CASE E3-DIFFERENTIATED

Supported claim:
CGEA and the selected fixed-expiry lease occupy different points on the useful-valid vs obsolete tradeoff; CGEA does not dominate the lease on the predeclared success criterion.

Unsupported:
CGEA multi-stage freshness is not shown to outperform a tuned fixed-expiry lease for this reassignment mechanism.

Selected TTL: 400
TEST CGEA useful_valid=0.2500 obsolete=0.0455
TEST FE useful_valid=0.8000 obsolete=0.3636
paired Δ useful_valid=-0.5500 [-0.5500, -0.5500]
paired Δ obsolete=-0.3182 [-0.3182, -0.3182]
n_test_runs=1890
