# ADR-009: Suppress authoritative fields while uncalibrated

**Status:** Accepted

## Context

The contradiction threshold is 0.5 — the argmax boundary of a three-class softmax. It was
not fitted, tuned, or validated against labelled data, because no human-labelled corpus
exists. PRISM could publish its contradiction counts anyway, with a caveat in the
documentation.

## Decision

While `calibration_status` is `UNCALIBRATED_PENDING_HUMAN_VALIDATION`, the authoritative
`contradiction_count`, `contradiction_rate`, and `agreement_type` fields are suppressed —
`None`, `None`, and `UNCLEAR` — by a model validator that no code path can bypass.
Provisional values appear only under `experimental_` names.

## Consequences

A caller cannot accidentally consume an unvalidated number as a finding, and no integration
can opt out of the gate. A caveat in a README does not survive being read by a model
summarising the JSON, and a number in a field called `contradiction_rate` will be used as
one regardless of what the documentation says.

The cost is that PRISM's headline capability is switched off in the shipped build. That is
the correct trade: a wrong contradiction rate is worse than none, because it gets acted on.

## Revisit when

A locked, human-labelled corpus exists, the threshold is fitted on the calibration split,
and the sealed test split is scored exactly once. See
[`../../tests/seeds/README.md`](../../tests/seeds/README.md).
