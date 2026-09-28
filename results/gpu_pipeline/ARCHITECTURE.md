# CPU/GPU channel pipeline (CGEA communication stack)

```
CPU process pool (Bellhop / aubellhop)
        |  path delays, complex coeffs, TL, metadata
        v
NPZ / in-memory CIR rows
        |  bucket by path count (8/16/32/64/…)
        v
Pinned tensors on CUDA:0  (host GPU selected by CUDA_VISIBLE_DEVICES)
        |  Sionna ChannelModel layout a, tau
        v
Batched OFDM PHY (QPSK, AWGN, ZF, Monte Carlo packets)
        |  BER / BLER / PER / rate / retries
        v
Compact ChannelTrace parquet  +  SNR LUT
        v
CPU SimPy  —  B1–B5 replay the SAME trace (no PHY in the event loop)
```

Governance thresholds, B5 variants, and action taxonomy are unchanged.
