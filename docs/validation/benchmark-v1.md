# HorusScan Scientific Validation Protocol — v1

Dataset: `unesco-egypt-public-v1-2026-10-27`

## Purpose

This benchmark tests whether the current HorusScan multisensor baseline can re-rank publicly documented archaeological landscapes as high-priority anomalies when their labels are hidden from the analysis pipeline.

It is a **known-site rediscovery benchmark**, not evidence of discovering previously unknown archaeology.

## Public positive-source policy

Only coordinates published by an authoritative public source are admitted.

Current v1 source:

- UNESCO World Heritage Centre — Memphis and its Necropolis:
  https://whc.unesco.org/en/list/86/maps/
- UNESCO World Heritage Centre — Ancient Thebes with its Necropolis:
  https://whc.unesco.org/en/list/87/maps/
- UNESCO World Heritage Centre — Abu Mena:
  https://whc.unesco.org/en/list/90/maps/
- UNESCO World Heritage Centre — Nubian Monuments from Abu Simbel to Philae:
  https://whc.unesco.org/en/list/88/maps/

No undisclosed archaeological coordinates are used.

## Geographic holdout

The split is regional, not random-tile:

- **Calibration:** Lower Egypt / Mariut.
- **Validation:** Luxor / Ancient Thebes.
- **Blind test:** Nubia / Aswan.

Adjacent image tiles are never intentionally divided across train/test merely to inflate performance.

The blind-test rows are hidden by RLS from authenticated clients. The AI server can read them only through its trusted server credential during the final benchmark.

A user can only complete the blind benchmark once for the current dataset version. The first completed blind result is treated as canonical.

## Evaluation case

For every known site:

1. Build an AOI around the public reference coordinate using the site's evaluation radius.
2. Run the same real HorusScan multisensor pipeline used for ordinary analysis:
   - Sentinel-2 spectral baseline.
   - Sentinel-1 RTC temporal/radar baseline when available.
   - Copernicus DEM terrain baseline when available.
   - Multisensor evidence fusion.
3. Do **not** provide the known-site label to any sensor model.
4. Measure whether the fused anomaly field concentrates around the hidden reference location.

## Rediscovery score

The current v1 score combines:

- percentile rank of the mean anomaly score near the hidden known-site center;
- concentration of that center score relative to the local surrounding context.

The score is bounded to `[0, 1]`, but it is **not a probability of archaeology**.

A v1 case is marked high-priority when:

- rediscovery score >= 0.75; and
- center-block percentile >= 0.80.

These thresholds belong to the benchmark version and must be changed only using the calibration split.

## Controls

Controls are generated deterministically in the same broad geographic context while maintaining spatial separation from the known public-site centers in the evaluated split.

Controls are called:

`control_unlabeled`

They are **not confirmed archaeological negatives**.

Therefore:

- control high-priority rate is a false-alarm **proxy**;
- PR-AUC and ROC-AUC are **proxy metrics**;
- they must never be reported as if the controls were field-verified negative archaeology.

## Primary metrics

The primary competition-facing metric is:

**Known-Site Rediscovery Rate at High Priority**

Secondary metrics:

- Precision @ High Priority — proxy.
- Control High-Priority Flag Rate — proxy.
- PR-AUC — proxy.
- ROC-AUC — proxy.
- center-block percentile.
- local concentration ratio.
- per-sensor availability and limitations.

## Scientific guardrails

- No random scores.
- No ground-truth label enters the remote-sensing model input.
- No calibration on blind-test results.
- No repeated completed blind-test runs.
- No claims of tomb/chamber confirmation.
- No claim of archaeological probability until a properly labeled and calibrated validation dataset exists.
- Every case stores its result, sensor summary, and deployed git commit in Supabase for reproducibility.

## Next benchmark versions

v2 should expand positives with additional legally/publicly available archaeological datasets and introduce spatially matched controls reviewed by archaeological experts.

v3 should add model calibration and conformal uncertainty after sample size becomes sufficient.
