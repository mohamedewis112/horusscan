"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap } from "maplibre-gl";
import CouncilResultPanel, { type HorusCouncil } from "./CouncilResultPanel";

type Position = [number, number];
type MapMode = "streets" | "satellite" | "terrain";

type FeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Point"; coordinates: [number, number] };
    properties: { anomaly_score: number };
  }>;
};

type CouncilResponse = {
  engine: string;
  fusion_profile: string;
  branches_used: string[];
  fusion: {
    summary: { evidence_agreement?: string; p90?: number };
    heatmap: FeatureCollection;
  };
  council: HorusCouncil;
  limitations: Record<string, unknown>;
};

function isoDate(daysAgo: number) {
  return new Date(Date.now() - daysAgo * 86400000).toISOString().slice(0, 10);
}

function quickSquare(lon: number, lat: number, radiusM = 1200): Position[] {
  const dy = radiusM / 111320;
  const dx = radiusM / (111320 * Math.max(Math.cos(lat * Math.PI / 180), 0.2));
  return [
    [lon - dx, lat - dy],
    [lon + dx, lat - dy],
    [lon + dx, lat + dy],
    [lon - dx, lat + dy],
  ];
}

export default function HorusMapV2() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);

  const [points, setPoints] = useState<Position[]>([]);
  const [drawing, setDrawing] = useState(false);
  const [mode, setMode] = useState<MapMode>("streets");
  const [query, setQuery] = useState("");
  const [start, setStart] = useState(isoDate(730));
  const [end, setEnd] = useState(isoDate(1));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CouncilResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    maplibregl.setWorkerUrl(
      new URL("maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).toString(),
    );

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: [31.1, 29.2],
      zoom: 6.5,
    });

    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(
      new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: false,
        showUserLocation: true,
        fitBoundsOptions: { maxZoom: 16 },
      }),
      "top-right",
    );

    map.on("load", () => {
      const before = map.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;

      map.addSource("satellite", {
        type: "raster",
        tiles: [
          "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        ],
        tileSize: 256,
        attribution: "Esri, Maxar, Earthstar Geographics",
      });
      map.addLayer(
        {
          id: "satellite-layer",
          type: "raster",
          source: "satellite",
          layout: { visibility: "none" },
        },
        before,
      );

      map.addSource("terrain-view", {
        type: "raster",
        tiles: ["https://a.tile.opentopomap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "OpenTopoMap",
      });
      map.addLayer(
        {
          id: "terrain-layer",
          type: "raster",
          source: "terrain-view",
          layout: { visibility: "none" },
        },
        before,
      );
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;

    if (map.getLayer("satellite-layer")) {
      map.setLayoutProperty(
        "satellite-layer",
        "visibility",
        mode === "satellite" ? "visible" : "none",
      );
    }
    if (map.getLayer("terrain-layer")) {
      map.setLayoutProperty(
        "terrain-layer",
        "visibility",
        mode === "terrain" ? "visible" : "none",
      );
    }
  }, [mode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const ring = points.length >= 3 ? [...points, points[0]] : points;
    const data = {
      type: "FeatureCollection" as const,
      features: points.length
        ? [{
            type: "Feature" as const,
            properties: {},
            geometry:
              points.length >= 3
                ? { type: "Polygon" as const, coordinates: [ring] }
                : { type: "LineString" as const, coordinates: points },
          }]
        : [],
    };

    const sync = () => {
      const source = map.getSource("aoi") as maplibregl.GeoJSONSource | undefined;
      if (source) {
        source.setData(data);
        return;
      }

      map.addSource("aoi", { type: "geojson", data });
      map.addLayer({
        id: "aoi-fill",
        type: "fill",
        source: "aoi",
        paint: { "fill-color": "#e5bc61", "fill-opacity": 0.18 },
      });
      map.addLayer({
        id: "aoi-line",
        type: "line",
        source: "aoi",
        paint: { "line-color": "#f5cf72", "line-width": 3 },
      });
    };

    if (map.isStyleLoaded()) sync();
    else map.once("load", sync);
  }, [points]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !drawing) return;

    const click = (event: maplibregl.MapMouseEvent) => {
      setPoints((current) => [...current, [event.lngLat.lng, event.lngLat.lat]]);
    };

    map.getCanvas().style.cursor = "crosshair";
    map.on("click", click);

    return () => {
      map.off("click", click);
      map.getCanvas().style.cursor = "";
    };
  }, [drawing]);

  const bbox = useMemo(() => {
    if (points.length < 3) return null;

    const xs = points.map((point) => point[0]);
    const ys = points.map((point) => point[1]);

    return [
      Math.min(...xs),
      Math.min(...ys),
      Math.max(...xs),
      Math.max(...ys),
    ] as [number, number, number, number];
  }, [points]);

  async function searchPlace() {
    if (!query.trim()) return;

    setError(null);
    try {
      const response = await fetch(
        "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=eg&q=" +
          encodeURIComponent(query.trim()),
      );
      const rows = await response.json();
      if (!Array.isArray(rows) || !rows.length) throw new Error("المكان غير موجود.");

      const lon = Number(rows[0].lon);
      const lat = Number(rows[0].lat);
      mapRef.current?.flyTo({ center: [lon, lat], zoom: 15, essential: true });
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "تعذر البحث.");
    }
  }

  function selectQuick() {
    const center = mapRef.current?.getCenter();
    if (!center) return;

    setPoints(quickSquare(center.lng, center.lat));
    setDrawing(false);
  }

  async function runCouncil() {
    if (!bbox) {
      setError("حدد المنطقة أولًا.");
      return;
    }

    setBusy(true);
    setError(null);
    setResult(null);

    try {
      const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
      const response = await fetch(`${base}/v1/council/analyze`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          bbox,
          start: `${start}T00:00:00Z`,
          end: `${end}T23:59:59Z`,
          max_cloud: 20,
          fusion_profile: "calibration_v1",
          max_candidates: 5,
        }),
      });

      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "فشل التحليل.");

      setResult(payload as CouncilResponse);
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "فشل التحليل.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="horus-v2" dir="rtl">
      <div className="map-search-bar">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void searchPlace();
          }}
          placeholder="ابحث عن قرية أو مدينة"
        />
        <button onClick={() => void searchPlace()}>بحث</button>
        <button onClick={selectQuick}>تحديد سريع 1.2 كم</button>
      </div>

      <div className="map-toolbar v2-toolbar">
        <div className="map-modes">
          <button className={mode === "streets" ? "active" : ""} onClick={() => setMode("streets")}>
            خريطة
          </button>
          <button className={mode === "satellite" ? "active" : ""} onClick={() => setMode("satellite")}>
            قمر صناعي
          </button>
          <button className={mode === "terrain" ? "active" : ""} onClick={() => setMode("terrain")}>
            تضاريس
          </button>
        </div>

        <button className="primary" onClick={() => setDrawing((value) => !value)}>
          {drawing ? "إنهاء التحديد" : "ارسم المنطقة"}
        </button>
        <button onClick={() => { setPoints([]); setResult(null); }}>مسح</button>

        <label>
          من
          <input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
        </label>
        <label>
          إلى
          <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
        </label>

        <button className="primary scan-button" disabled={!bbox || busy} onClick={runCouncil}>
          {busy ? "الموديلات تحلل وتتبادل الأدلة…" : "تشغيل Horus Council"}
        </button>
      </div>

      <div className="live-mode-note">
        <b>تجربة مباشرة:</b> لا تحتاج حساب. زر تحديد الموقع موجود يمين الخريطة.
      </div>

      {error && <div className="result-card error">{error}</div>}

      {result && (
        <div className="council-run-summary">
          <strong>{result.branches_used.length} فروع أدلة شاركت في القرار</strong>
          <span>{result.branches_used.join(" · ")}</span>
          <small>
            اتفاق الأدلة: {result.fusion.summary.evidence_agreement ?? "غير حاسم"} · {result.fusion_profile}
          </small>
          {Object.keys(result.limitations).length > 0 && (
            <small>غير مشارك في هذه الجولة: {Object.keys(result.limitations).join(" · ")}</small>
          )}
        </div>
      )}

      <div ref={containerRef} className="map map-enhanced" aria-label="خريطة HorusScan" />

      {result?.council && <CouncilResultPanel council={result.council} />}
    </div>
  );
}
