# HorusScan AI

**Space-Based Multimodal Archaeological Detection & Intelligence Platform**

> See patterns. Understand the land. Protect the past.

HorusScan is a scientific decision-support system for AI-assisted remote archaeological prospection. It detects and ranks geospatial anomalies from Earth-observation evidence; it does **not** claim excavation confirmation, exact subsurface geometry, or tomb depth from satellite imagery alone.

## Current implementation — Milestone 1

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
- Explicit labeling that anomaly rank is **not** archaeological probability.
- Supabase/PostgreSQL backend with PostGIS, pgvector, RLS, spatial indexes, model/audit tables.
- GitHub Actions checks for web type/build validation and Python lint/tests.

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

## Scientific status

The current baseline is intentionally simple and reproducible. It identifies unusual multispectral patterns inside an AOI using real Sentinel-2 pixels. It is **not yet** the final Horus HyperFusion model.

Next research phases:

1. Persist AOIs, discovered scenes, runs, and layers in Supabase.
2. Sentinel-1 SAR preprocessing and temporal stacking.
3. Copernicus DEM / terrain morphology.
4. Spatial holdout validation on known archaeological sites and controls.
5. TerraMind adapter.
6. Prithvi temporal adapter.
7. DINO satellite embeddings.
8. Geometry engine and historical persistence.
9. Cross-sensor HyperFusion.
10. Calibration, conformal uncertainty, and explainability.

## Non-negotiable scientific rules

- No fake scores.
- No fake progress.
- No claim that satellite data alone confirms a tomb/chamber.
- Every analysis must retain source/provenance/model metadata.
- Sensitive candidate coordinates must not become a public discovery map.
