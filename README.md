# HorusScan AI

**Space-Based Multimodal Archaeological Detection & Intelligence Platform**

> See patterns. Understand the land. Protect the past.

HorusScan is a scientific decision-support system for AI-assisted remote archaeological prospection. It detects and ranks geospatial anomalies from Earth-observation evidence; it does **not** claim excavation confirmation, exact subsurface geometry, or tomb depth from satellite imagery alone.

## Current implementation — Milestone 3

The `develop` branch now contains a real working-core bootstrap:

- Next.js 16 + React 19 + strict TypeScript.
- MapLibre map centered on Egypt.
- Interactive AOI polygon drawing.
- FastAPI geospatial service.
- Real Sentinel-2 L2A scene discovery through Copernicus Data Space STAC.
- Open-COG Sentinel-2 fallback for credential-free raster processing.
- Real B02/B04/B08/B11 raster reads for the requested AOI.
- Spectral features: NDVI, NDMI, Bare Soil Index.
- Scene Classification Layer masking when available.
- Isolation Forest unsupervised anomaly baseline.
- Real GeoJSON anomaly heatmap returned to the web client.
- Sentinel-1 RTC discovery and multi-date VV/VH temporal feature stack.
- Copernicus GLO-30 DEM terrain derivatives: elevation, slope proxy, curvature and roughness.
- Multisensor evidence fusion with explicit cross-sensor agreement.
- Supabase Auth-backed persistent scans: Project → AOI → Analysis Run → Scene provenance → Analysis Layers.
- Every persisted scan retains sensor source, scene IDs, dates, processing summary and analytical layer data.
- Explicit labeling that anomaly rank is **not** archaeological probability.
- Supabase/PostgreSQL backend with PostGIS, pgvector, RLS, spatial indexes, model/audit tables.
- GitHub Actions checks for web type/build validation and Python lint/tests.
- Scientific validation dataset from public UNESCO World Heritage coordinates.
- Regional geographic holdout: calibration / validation / locked blind-test.
- Known-site rediscovery benchmark using the same real multisensor pipeline.
- Deterministic unlabeled controls spatially separated from public known-site centers.
- Rediscovery Rate, Precision@High-Priority proxy, PR-AUC proxy and ROC-AUC proxy.
- Persisted per-case benchmark evidence and a one-completed-run blind-test lock.

## Repository layout

```text
apps/
  web/
services/
  ai-api/
    app/
      pipeline/
      providers/
    tests/
supabase/
docs/
.github/workflows/
```

## Run the web app

```bash
cd apps/web
npm install
npm run dev
```

Set:

```env
NEXT_PUBLIC_AI_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=
```

Never expose a Supabase service-role/secret key in the browser.

## Run the AI service

```bash
cd services/ai-api
python -m venv .venv
# activate the environment
pip install ".[dev]"
uvicorn app.main:app --reload
```

API:

- `GET /health`
- `POST /v1/scenes/sentinel-2`
- `POST /v1/analysis/sentinel-2-baseline`
- `POST /v1/analysis/multisensor-baseline`
- `POST /v1/validation/run`

## Scientific status

The current baseline is intentionally reproducible and now multisensor. It identifies unusual optical, radar and terrain patterns inside an AOI and measures evidence agreement. It is **not yet** the final calibrated Horus HyperFusion model.

Next research phases:

1. Spatial holdout validation on known archaeological sites and control areas.
2. Stronger Sentinel-1 preprocessing and orbit-aware temporal grouping.
3. TerraMind adapter.
4. Prithvi temporal adapter.
5. DINO satellite embeddings.
6. Geometry engine and historical persistence.
7. Cross-sensor learned HyperFusion.
8. Calibration, conformal uncertainty and explainability.
9. Scientific benchmark / blind demo.

## Non-negotiable scientific rules

- No fake scores.
- No fake progress.
- No claim that satellite data alone confirms a tomb/chamber.
- Every analysis must retain source/provenance/model metadata.
- Sensitive candidate coordinates must not become a public discovery map.
