"""Journey bench: long, mixed user journeys against live hubs.

Plan: docs/internal/journey-bench-plan.md. Every step is followed by a
frame check (did anything change that the step was not allowed to
change?), and checkpoints compare the hub itself.

Run from ``scripts/hub-bench`` so ``ha_bench`` and ``bench_compare`` import
as top-level modules::

    python -m journeys measure --hub x1s
    python -m journeys run p0_smoke --hub x1s

The pure modules (normalize, frame, model) never import ``ha_bench``, so
their tests run without an HA instance.
"""
