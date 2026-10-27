# HorusScan AI

**Space-Based Multimodal Archaeological Detection & Intelligence Platform**

> See patterns. Understand the land. Protect the past.

HorusScan is a scientific decision-support system for AI-assisted remote archaeological prospection. It detects and ranks geospatial anomalies from Earth-observation evidence; it does **not** claim excavation confirmation, exact subsurface geometry, or tomb depth from satellite imagery alone.

## Current implementation — Milestone 4 · HyperFusion + Model Tournament

The `develop` branch now includes a real, layered architecture that keeps the original reproducible baseline while adding advanced sensors and foundation-model competition.

### Real EO branches implemented now

- Sentinel-2 L2A discovery and real COG raster reads.
- Sentinel-1 RTC discovery, VV/VH multi-date temporal stack, ratio, variance and texture features.
- Copernicus GLO-30 DEM terrain derivatives.
- EnMAP L2A STAC discovery through DLR EOC Geoservice.
- Real hyperspectral multiband anomaly engine using robust normalization + PCA + Isolation Forest.
- NASA EMIT L2A discovery through CMR; download/inference remains gated by Earthdata-authorized access.
- Landsat Collection 2 Level-2 thermal discovery through Planetary Computer.
- Real thermal-persistence anomaly branch from surface-temperature observations.
- Thermal persistence is explicitly **not** mislabeled as thermal inertia.

### Horus HyperFusion v0

HyperFusion now accepts any available evidence branches:

```text
Sentinel-2 spectral
+ Sentinel-1 SAR
+ Copernicus DEM
+ EnMAP hyperspectral
+ thermal persistence
+ future foundation-model heatmaps
↓
reliability-weighted geometric evidence
+ cross-sensor high-rank agreement
+ sensor-coverage gating
↓
Horus HyperFusion v0
```

The output remains an evidence rank, not calibrated archaeological probability.

### Foundation-model architecture added

Registered and measurable adapters:

- TerraMind — multimodal EO encoder.
- Prithvi-EO-2.0 — spatiotemporal encoder.
- DINOv3 Satellite — dense geometry/visual representation.
- SAM 3.1 — geometry post-processing / segmentation only.
- SpectralEarth-FM — hyperspectral/multimodal representation.
- TerraFlow — multimodal temporal representation.
- OlmoEarth — global multimodal/spatiotemporal representation.
- Physics-SAR — true physics-informed moisture / roughness inversion adapter.
- Thermal Inertia — true day/night thermal-inertia adapter.

**Critical rule:** an adapter is not considered available until a real inference endpoint is configured. HorusScan rejects model responses that do not return `real_inference=true`.

### GPU foundation worker

A separate service exists under:

```text
services/foundation-worker/
```

This prevents large PyTorch/foundation models from destabilizing the core FastAPI service.

DINOv3 Satellite has a built-in real local adapter using the official satellite checkpoint:

```text
facebook/dinov3-vitl16-pretrain-sat493m
```

It discovers real Sentinel-2 imagery, extracts dense DINOv3 patch embeddings and generates a spatial anomaly heatmap from embedding distance to local context.

Other models use strict native-plugin adapters until their runtime/checkpoints are installed. Missing models return unavailable — never synthetic inference.

### SAM 3.1 geometry stage

`POST /v1/foundation/segment-candidates`

SAM 3.1 is used only after HyperFusion has selected high-ranking regions. If SAM is unavailable, HorusScan keeps the heatmap and does **not** invent polygons.

### Model Tournament

`POST /v1/tournament/run`

The system can compare:

- local multisensor baseline
- TerraMind
- Prithvi-EO-2.0
- DINOv3 Satellite
- SpectralEarth-FM
- TerraFlow
- OlmoEarth
- Physics-SAR
- Thermal Inertia

on the **same geographic validation cases**.

Tournament metrics:

- Known-site Rediscovery Rate
- Precision @ High Priority proxy
- PR-AUC proxy
- ROC-AUC proxy
- Control high-priority flag rate

Composite tournament ranking is a validation heuristic only, never archaeological probability.

### Scientific validation protocol

Dataset: public UNESCO World Heritage component coordinates only.

Geographic split:

- **Calibration:** Lower Egypt / Alexandria.
- **Validation:** Luxor / Thebes.
- **Blind Test:** Aswan / Nubia.

Controls are deterministic **unlabeled controls**, not asserted archaeological negatives, and are kept away from every public known-site center in the dataset.

Blind test protections:

- hidden from client reads before execution,
- one-shot database lock,
- no repeated canonical run for the same account/dataset,
- aggregate-only API response,
- per-case details withheld during blind evaluation.

### Persistence and reproducibility

Supabase/PostgreSQL stores:

- Projects
- AOIs as PostGIS polygons
- Satellite scenes
- Analysis runs
- Scene/run links
- Analysis layers
- Foundation features
- Validation sites/runs/results
- Model tournament runs/entries
- Model versions
- Audit logs

Every persisted analysis can retain:

- source/provider
- collection/item IDs
- acquisition time when meaningful
- Git commit
- branch/layer statistics
- model metadata
- processing provenance

Temporary signed asset tokens are stripped before persistence.

## Repository layout

```text
apps/
  web/
services/
  ai-api/
    app/
      foundation/
      pipeline/
      providers/
      routes/
      validation/
    tests/
  foundation-worker/
    worker/
.github/workflows/
```

## Core API

- `GET /health`
- `POST /v1/scenes/sentinel-2`
- `POST /v1/analysis/sentinel-2-baseline`
- `POST /v1/analysis/multisensor-baseline`
- `POST /v1/analysis/hyperfusion-v0`
- `POST /v1/validation/run`
- `GET /v1/foundation/models`
- `POST /v1/foundation/segment-candidates`
- `POST /v1/tournament/run`

## What is real now vs configured architecture

### Real local execution now

- Sentinel-2 spectral baseline
- Sentinel-1 RTC baseline
- DEM terrain baseline
- EnMAP hyperspectral baseline when AOI coverage/data access is available
- Landsat thermal-persistence baseline
- HyperFusion v0
- Geographic benchmark
- Model Tournament framework
- DINOv3 Satellite worker when GPU/model download is available

### Requires model/runtime/credential configuration

- TerraMind native inference
- Prithvi-EO-2.0 native inference
- SpectralEarth-FM native inference
- TerraFlow native inference
- OlmoEarth native inference
- Physics-informed SAR inversion model
- True day/night thermal inertia
- SAM 3.1 segmentation
- EMIT reflectance download/inference through Earthdata-authenticated access

These branches are deliberately marked unavailable until real inference is connected.

## Non-negotiable scientific rules

- No fake scores.
- No fake progress.
- No fake model readiness.
- No claim that satellite data alone confirms a tomb/chamber.
- No exact subsurface-depth claim from EO-only evidence.
- No calling thermal persistence “thermal inertia”.
- Every analysis must retain source/provenance/model metadata.
- Sensitive candidate coordinates must not become a public discovery map.

## Next research priorities

1. Run calibration + validation benchmark with the expanded HyperFusion baseline.
2. Deploy DINOv3 Satellite worker and compare it against the baseline.
3. Add native TerraMind and Prithvi worker plugins.
4. Add SpectralEarth-FM and OlmoEarth plugins.
5. Add TerraFlow temporal branch.
6. Connect real physics-SAR inversion.
7. Connect ECOSTRESS/paired day-night thermal workflow for true thermal inertia.
8. Learn fusion weights from calibration only.
9. Add conformal uncertainty/calibration.
10. Execute the locked final blind test.
