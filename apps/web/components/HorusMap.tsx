"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { Map as MapLibreMap } from "maplibre-gl";

type Position = [number, number];

type AnalysisResponse = {
  real_data: boolean;
  source: string;
  scene: { id: string; datetime: string; cloud_cover: number | null };
  summary: {
    status: string;
    valid_pixels: number;
    score_type: string;
    not_probability: boolean;
    p50?: number;
    p90?: number;
    p99?: number;
  };
  heatmap: GeoJSON.FeatureCollection<GeoJSON.Point, { anomaly_score: number }>;
  scientific_status: string;
};

function isoDate(daysAgo: number): string {
  const date = new Date(Date.now() - daysAgo * 86_400_000);
  return date.toISOString().slice(0, 10);
}

export default function HorusMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [points, setPoints] = useState<Position[]>([]);
  const [drawing, setDrawing] = useState(false);
  const [start, setStart] = useState(isoDate(120));
  const [end, setEnd] = useState(isoDate(1));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: "https://demotiles.maplibre.org/style.json",
      center: [30.8, 26.8],
      zoom: 4.7,
      attributionControl: true,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    mapRef.current = map;
    return () => map.remove();
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const sync = () => {
      const ring = points.length >= 3 ? [...points, points[0]] : points;
      const data: GeoJSON.FeatureCollection = {
        type: "FeatureCollection",
        features: points.length
          ? [{
              type: "Feature",
              properties: {},
              geometry: points.length >= 3
                ? { type: "Polygon", coordinates: [ring] }
                : { type: "LineString", coordinates: points },
            }]
          : [],
      };

      if (map.getSource("horus-aoi")) {
        (map.getSource("horus-aoi") as maplibregl.GeoJSONSource).setData(data);
        return;
      }
      map.addSource("horus-aoi", { type: "geojson", data });
      map.addLayer({
        id: "horus-aoi-fill",
        type: "fill",
        source: "horus-aoi",
        paint: { "fill-color": "#d0ae62", "fill-opacity": 0.16 },
        filter: ["==", "$type", "Polygon"],
      });
      map.addLayer({
        id: "horus-aoi-line",
        type: "line",
        source: "horus-aoi",
        paint: { "line-color": "#e6c77c", "line-width": 2.5 },
      });
    };

    if (map.isStyleLoaded()) sync();
    else map.once("load", sync);
  }, [points]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !drawing) return;
    const onClick = (event: maplibregl.MapMouseEvent) =>
      setPoints((existing) => [...existing, [event.lngLat.lng, event.lngLat.lat]]);
    map.getCanvas().style.cursor = "crosshair";
    map.on("click", onClick);
    return () => {
      map.off("click", onClick);
      map.getCanvas().style.cursor = "";
    };
  }, [drawing]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !result) return;

    const draw = () => {
      if (map.getSource("horus-heatmap")) {
        (map.getSource("horus-heatmap") as maplibregl.GeoJSONSource).setData(result.heatmap);
        return;
      }
      map.addSource("horus-heatmap", { type: "geojson", data: result.heatmap });
      map.addLayer({
        id: "horus-anomaly-heat",
        type: "heatmap",
        source: "horus-heatmap",
        maxzoom: 15,
        paint: {
          "heatmap-weight": ["interpolate", ["linear"], ["get", "anomaly_score"], 0, 0, 1, 1],
          "heatmap-intensity": 1.2,
          "heatmap-radius": 24,
          "heatmap-opacity": 0.78
        },
      });
    };

    if (map.isStyleLoaded()) draw();
    else map.once("load", draw);
  }, [result]);

  const bbox = useMemo(() => {
    if (points.length < 3) return null;
    const lons = points.map(([lon]) => lon);
    const lats = points.map(([, lat]) => lat);
    return [
      Math.min(...lons),
      Math.min(...lats),
      Math.max(...lons),
      Math.max(...lats),
    ] as [number, number, number, number];
  }, [points]);

  async function initiateScan() {
    if (!bbox) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
      const response = await fetch(`${base}/v1/analysis/sentinel-2-baseline`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          bbox,
          start: `${start}T00:00:00Z`,
          end: `${end}T23:59:59Z`,
          max_cloud: 20,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Analysis failed.");
      setResult(payload as AnalysisResponse);
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "Analysis failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ position: "relative", minHeight: "650px" }}>
      <div className="map-toolbar">
        <button className="primary" onClick={() => setDrawing((v) => !v)}>
          {drawing ? "Stop drawing" : "Draw AOI"}
        </button>
        <button onClick={() => { setPoints([]); setResult(null); }}>Clear</button>
        <label>
          From
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <button className="primary" disabled={!bbox || busy} onClick={initiateScan}>
          {busy ? "ANALYZING REAL DATA…" : "INITIATE HORUS SCAN"}
        </button>
      </div>

      {error && <div className="result-card error">{error}</div>}
      {result && (
        <div className="result-card">
          <strong>REAL SATELLITE DATA · MODEL INFERENCE</strong>
          <span>{result.source}</span>
          <span>Scene: {result.scene.id}</span>
          <span>Cloud cover: {result.scene.cloud_cover ?? "n/a"}%</span>
          <span>Valid pixels: {result.summary.valid_pixels.toLocaleString()}</span>
          <span>
            P90 anomaly rank: {result.summary.p90 !== undefined ? result.summary.p90.toFixed(3) : "n/a"}
          </span>
          <small>{result.scientific_status}</small>
        </div>
      )}

      <div ref={containerRef} className="map" aria-label="HorusScan analysis map" />
    </div>
  );
}
