# E3 DEV TTL selection

Chosen TTL: **400**

Rule: min obsolete s.t. useful_valid>=0.80; then max useful_valid; then longer TTL

| TTL | useful_valid | obsolete |
|---|---:|---:|
| 60 | 0.0000 | 0.0000 |
| 120 | 0.0000 | 0.0000 |
| 180 | 0.2500 | 0.0455 |
| 240 | 0.4500 | 0.1364 |
| 400 | 0.8000 | 0.3636 |
| 600 | 0.9000 | 0.5455 |
| 900 | 0.9500 | 0.7727 |
| infinity | 1.0000 | 1.0000 |
