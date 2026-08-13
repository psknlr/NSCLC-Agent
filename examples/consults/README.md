# Consultation starters

Case files here are **deliberately incomplete**. They cannot be staged and
cannot be routed, which is exactly the point: they are inputs for
`nsclc_agent consult`, not for `run` or `batch`.

Running one through `run` fails with `STAGE_UNRESOLVED`, correctly — nothing in
the file establishes T, N or M. Feeding the same file to `consult` starts an
interview that asks for the blocking descriptors first, explains why each one
matters, and reports every gap it could not close.

```bash
# Fails, on purpose — there is nothing to stage
python -m nsclc_agent run --case examples/consults/incomplete_referral.json

# Ask for what is missing instead
python -m nsclc_agent consult --lang en \
    --case examples/consults/incomplete_referral.json

# Non-interactive, to see the whole loop at once
python -m nsclc_agent consult --lang en --no-interactive \
    --case examples/consults/incomplete_referral.json \
    --answers "Adenocarcinoma on repeat biopsy. 4.5 cm RLL mass, no pleural invasion, so T2b." \
              "EBUS sampled 4R and 7 — both positive, so multi-station N2b. PET-CT and brain MRI negative." \
              "ECOG 1, EGFR negative, ALK negative, PD-L1 TPS 60%. MDT says unresectable."
```

`examples/cases/` holds the opposite: complete cases, one per stage band, that
every run of `batch` is expected to stage and route successfully.
