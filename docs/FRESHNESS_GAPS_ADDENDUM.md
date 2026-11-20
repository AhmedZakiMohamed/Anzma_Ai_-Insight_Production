# Data gap semantics — E4a approved addendum

**Status: APPROVED by the human, 2026-11-20.** These decisions govern E4a and supersede the earlier E4a prompt where it conflicts with E1.

1. **Opening:** Open a gap only when freshness reaches `MISSING`; `STALE` is a freshness state and never opens a gap. `gap_start` is the `observed_at` timestamp of the last valid observation. `detected_at` records when the evaluator detected the gap.
2. **Work schedule and eligibility:** Evaluation is not gated by work hours. `opened_in_work_hours` records whether `detected_at`, converted using the building's configured IANA timezone, falls within its configured work hours and working weekdays. It is informational only. Do not open a gap for a retired/disabled asset or a disabled source.
3. **Cause:** E4a writes `UNKNOWN` when it opens a gap. `NO_DATA` means a series is silent while its source is alive; `SOURCE_SUSPECTED` means the source is silent or many of its series are stale together. E4b classifies `NO_DATA` and `SOURCE_SUSPECTED`. Cause may be updated, with `cause_updated_at`, while a gap is open; it is immutable after closure. Cause is evidence only and never changes asset health or produces `OFFLINE`.
4. **Late data and boundaries:** A back-dated record inside an open or closed gap neither closes nor splits that gap. Boundaries are immutable. A gap closes only on a valid observation newer than the last valid observation recorded at opening; `BAD` quality data never closes a gap. A closed gap stays closed.

The existing E3 evaluator is implemented against the contract's UTC freshness thresholds and `v_telemetry_valid`; this addendum adds no work-hour freshness gating. `NOT_APPLICABLE` remains unpersisted under amendment A7.
