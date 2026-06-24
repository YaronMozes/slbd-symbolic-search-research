# SLBD Experiments

This branch keeps landmarks conservative: they only guide symbolic
bidirectional direction selection. Do not change `slbd` defaults unless an
external benchmark run shows a clear win.

## Smoke Check

Run the bundled smoke check from WSL:

```bash
./fast-downward.py misc/tests/benchmarks/gripper/domain.pddl misc/tests/benchmarks/gripper/prob01.pddl --search "slbd(lm_factory=lm_rhw(),lm_guidance_score=weighted)"
python3 misc/tests/compare-slbd.py --config-preset smoke --timeout 60 --repeat 1
```

The bundled suite is only a regression check. It is too small to support a
performance-improvement claim.

## Tuning Run

Keep IPC-style benchmarks outside git and pass their root directory with
`--benchmark-dir`:

```bash
python3 misc/tests/compare-slbd.py \
  --benchmark-dir /path/to/benchmarks \
  --config-preset tuning \
  --tasks-per-domain 5 \
  --timeout 60 \
  --repeat 1
```

Use `slbd-results/summary.txt` to identify a conservative candidate, if any.

## Confirmation Run

Confirm the best candidate on a larger slice:

```bash
python3 misc/tests/compare-slbd.py \
  --benchmark-dir /path/to/benchmarks \
  --config-preset confirm \
  --confirm-candidate 'slbd(lm_factory=lm_rhw(),lm_guidance_score=weighted)' \
  --tasks-per-domain 10 \
  --timeout 120 \
  --repeat 3
```

## Acceptance Rule

Before changing any default, the candidate must:

- Preserve plan cost on every commonly solved task.
- Avoid solved-count regression against `sbd`, current `slbd`, and
  `slbd-no-guidance`.
- Improve common-solved geometric-mean actual search time by at least 10%.

If these conditions are not met, keep the current defaults and report that the
measurement improved but does not justify a default algorithm change.
