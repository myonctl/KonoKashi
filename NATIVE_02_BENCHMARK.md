# NATIVE-02 benchmark record

Date: 2026-09-13
Decision: the native LRC parser clears the Stage 11 runtime and peak-allocation
gate on the measured workloads.

The committed harness runs the production native wrapper and retained Python
oracle in separate fresh child processes. It verifies semantic parity first,
then records the median of 11 parses. Python peak allocation is measured with
`tracemalloc` around a separate parse after constructing the input. Peak RSS is
the operating system high-water mark for that child, so it includes the shared
interpreter, imports, input, native allocations, and result.

Command:

```bash
.venv/bin/python scripts/benchmark_lrc_native.py --samples 11
```

Recorded host: Linux 6.18.50-2-lts x86_64; Intel Core i5-7600K (4 CPUs); GCC
16.2.1; Python 3.14.7; OpenSSL 3.6.4.

| Case | Python ms | Native ms | Runtime change | Python peak MiB | Native peak MiB | Process peak RSS Python/native MiB |
|---|---:|---:|---:|---:|---:|---:|
| plain | 2.959 | 1.845 | -37.6% | 0.342 | 0.314 | 43.984/43.984 |
| line | 7.266 | 4.261 | -41.4% | 0.562 | 0.330 | 43.984/43.984 |
| enhanced | 11.048 | 6.202 | -43.9% | 0.808 | 0.466 | 43.984/43.984 |
| malformed | 0.724 | 0.556 | -23.3% | 0.158 | 0.103 | 43.984/43.984 |
| maximum | 20.959 | 18.402 | -12.2% | 5.722 | 5.722 | 43.984/43.984 |

The cases contain 1,000 ordinary lines, 1,000 line-timed entries, 600
two-segment enhanced entries, 1,000 malformed timestamp-like lines, and exactly
2,000,000 non-ASCII characters respectively. Lower is better. No required case
regressed in median runtime, traced peak allocation, or process peak RSS. The
identical RSS values mean none of the parse workloads exceeded the interpreter's
earlier import high-water mark on this host; they are not a claim of zero native
allocation.

These figures qualify this implementation on the recorded host; they are not a
portable speed guarantee. The gate is reproducible with the committed harness,
and a future native change must rerun it rather than copying these values.
