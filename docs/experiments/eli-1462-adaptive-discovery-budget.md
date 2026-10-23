# ELI-1462 — Adaptive discovery budgets

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

The experiment tried both per-vendor budget tiers and an adaptive round loop for external discovery. Neither produced a reliable stopping signal: runtime observations could not establish that a vendor's off-site evidence had been exhausted.

The historical implementation therefore used one raised budget for every vendor and a single pass. This favored predictable resource limits over an unsupported claim of evidence exhaustion. The source reports no quantitative comparison, budget value or repeated-run estimate; none is inferred here.
