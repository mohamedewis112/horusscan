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

type GridFeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Polygon"; coordinates: number[][][] };
    properties: { anomaly_score: number };
  }>;
};

function median(values: number[]) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function stableGridFromHeatmap(heatmap: FeatureCollection): GridFeatureCollection {
  const points = heatmap.features
    .map((feature) => ({
      lon: Number(feature.geometry.coordinates[0]),
      lat: Number(feature.geometry.coordinates[1]),
      score: Number(feature.properties.anomaly_score),
    }))
    .filter((row) => Number.isFinite(row.lon) && Number.isFinite(row.lat) && Number.isFinite(row.score));

  const uniqueLon = [...new Set(points.map((row) => row.lon))].sort((a, b) => a - b);
  const uniqueLat = [...new Set(points.map((row) => row.lat))].sort((a, b) => a - b);
  const lonSteps = uniqueLon.slice(1).map((value, index) => value - uniqueLon[index]).filter((value) => value > 0);
  const latSteps = uniqueLat.slice(1).map((value, index) => value - uniqueLat[index]).filter((value) => value > 0);
  const dx = median(lonSteps) || 0.0005;
  const dy = median(latSteps) || 0.0005;

  return {
    type: "FeatureCollection",
    features: points.map((point) => ({
      type: "Feature",
      properties: { anomaly_score: point.score },
      geometry: {
        type: "Polygon",
        coordinates: [[
          [point.lon - dx / 2, point.lat - dy / 2],
          [point.lon + dx / 2, point.lat - dy / 2],
          [point.lon + dx / 2, point.lat + dy / 2],
          [point.lon - dx / 2, point.lat + dy / 2],
          [point.lon - dx / 2, point.lat - dy / 2],
        ]],
      },
    })),
  };
}

type DeepReviewSummary = {
  completed_rounds: number;
  total_rounds: number;
  persistent_zones: Array<{
    key: string;
    appearances: number;
    average_confidence: number;
    max_confidence: number;
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
  const [reviewProgress, setReviewProgress] = useState("");
  const [deepReview, setDeepReview] = useState<DeepReviewSummary | null>(null);

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

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !result) return;

    const draw = () => {
      const stableGrid = stableGridFromHeatmap(result.fusion.heatmap);
      const heatSource = map.getSource("council-grid") as maplibregl.GeoJSONSource | undefined;
      if (heatSource) {
        heatSource.setData(stableGrid);
      } else {
        map.addSource("council-grid", { type: "geojson", data: stableGrid });
        map.addLayer({
          id: "council-grid-layer",
          type: "fill",
          source: "council-grid",
          paint: {
            "fill-color": [
              "interpolate",
              ["linear"],
              ["get", "anomaly_score"],
              0.00, "#2456ff",
              0.25, "#19bdf2",
              0.45, "#25c86f",
              0.62, "#d9d833",
              0.78, "#ff9d22",
              0.92, "#f04c2d",
              1.00, "#c82323",
            ],
            "fill-opacity": 0.56,
            "fill-outline-color": "rgba(255,255,255,0.08)",
          },
        });
      }

      const candidateData = {
        type: "FeatureCollection" as const,
        features: result.council.candidates.map((candidate) => ({
          type: "Feature" as const,
          properties: {
            candidate_id: candidate.candidate_id,
            confidence: candidate.evidence_confidence_index,
            priority: candidate.priority,
          },
          geometry: candidate.geometry,
        })),
      };

      const candidateSource = map.getSource("council-candidates") as maplibregl.GeoJSONSource | undefined;
      if (candidateSource) {
        candidateSource.setData(candidateData);
      } else {
        map.addSource("council-candidates", { type: "geojson", data: candidateData });
        map.addLayer({
          id: "council-candidate-fill",
          type: "fill",
          source: "council-candidates",
          paint: {
            "fill-color": "#e7bc60",
            "fill-opacity": 0.18,
          },
        });
        map.addLayer({
          id: "council-candidate-line",
          type: "line",
          source: "council-candidates",
          paint: {
            "line-color": "#ffe09a",
            "line-width": 3,
          },
        });
      }
    };

    if (map.isStyleLoaded()) draw();
    else map.once("load", draw);
  }, [result]);

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

  async function requestCouncil(roundStart: Date, roundEnd: Date) {
    if (!bbox) throw new Error("حدد المنطقة أولًا.");
    const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
    const response = await fetch(`${base}/v1/council/analyze`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        bbox,
        start: roundStart.toISOString(),
        end: roundEnd.toISOString(),
        max_cloud: 20,
        fusion_profile: "calibration_v1",
        max_candidates: 10,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail ?? "فشل التحليل.");
    return payload as CouncilResponse;
  }

  async function runCouncil() {
    if (!bbox) {
      setError("حدد المنطقة أولًا.");
      return;
    }

    setBusy(true);
    setError(null);
    setResult(null);
    setDeepReview(null);

    const startDate = new Date(`${start}T00:00:00Z`);
    const endDate = new Date(`${end}T23:59:59Z`);
    const midpoint = new Date((startDate.getTime() + endDate.getTime()) / 2);
    const rounds = [
      { name: "الفترة الكاملة", from: startDate, to: endDate },
      { name: "النصف الأقدم", from: startDate, to: midpoint },
      { name: "النصف الأحدث", from: midpoint, to: endDate },
    ];

    const completed: CouncilResponse[] = [];
    const failures: string[] = [];

    try {
      for (let index = 0; index < rounds.length; index += 1) {
        const round = rounds[index];
        setReviewProgress(`مراجعة ${index + 1}/${rounds.length}: ${round.name}`);
        try {
          const payload = await requestCouncil(round.from, round.to);
          completed.push(payload);
          if (index === 0) setResult(payload);
        } catch (roundError) {
          failures.push(
            `${round.name}: ${roundError instanceof Error ? roundError.message : "فشلت الجولة"}`,
          );
        }
      }

      if (!completed.length) {
        throw new Error(failures.join(" · ") || "فشلت جولات التحليل.");
      }

      if (!result && completed[0]) setResult(completed[0]);

      const zones = new Map<string, { appearances: number; confidences: number[] }>();
      for (const run of completed) {
        const seen = new Set<string>();
        for (const candidate of run.council.candidates) {
          const key = `${candidate.approximate_center.lat.toFixed(2)},${candidate.approximate_center.lon.toFixed(2)}`;
          if (seen.has(key)) continue;
          seen.add(key);
          const current = zones.get(key) ?? { appearances: 0, confidences: [] };
          current.appearances += 1;
          current.confidences.push(candidate.evidence_confidence_index);
          zones.set(key, current);
        }
      }

      const persistent = [...zones.entries()]
        .map(([key, value]) => ({
          key,
          appearances: value.appearances,
          average_confidence:
            value.confidences.reduce((sum, item) => sum + item, 0) / value.confidences.length,
          max_confidence: Math.max(...value.confidences),
        }))
        .filter((zone) => zone.appearances >= 2)
        .sort((a, b) =>
          b.appearances - a.appearances ||
          b.average_confidence - a.average_confidence
        )
        .slice(0, 10);

      setDeepReview({
        completed_rounds: completed.length,
        total_rounds: rounds.length,
        persistent_zones: persistent,
      });

      if (failures.length) {
        setError("اكتمل التحليل جزئيًا: " + failures.join(" · "));
      }
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "فشل التحليل.");
    } finally {
      setReviewProgress("");
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
          {busy ? (reviewProgress || "التحليل العميق يعمل…") : "تشغيل التحليل العميق"}
        </button>
      </div>

      <div className="live-mode-note">
        <b>تجربة مباشرة:</b> لا تحتاج حساب. زر تحديد الموقع موجود يمين الخريطة.
      </div>

      {error && <div className="result-card error">{error}</div>}

      {deepReview && (
        <div className="deep-review-summary">
          <strong>
            المراجعة الزمنية: {deepReview.completed_rounds}/{deepReview.total_rounds} جولات
          </strong>
          <span>
            مناطق تكررت في جولتين أو أكثر: {deepReview.persistent_zones.length}
          </span>
          {deepReview.persistent_zones.slice(0, 5).map((zone) => (
            <small key={zone.key}>
              ثبات زمني {zone.appearances}/{deepReview.completed_rounds} · متوسط مؤشر الأدلة {zone.average_confidence.toFixed(1)}%
            </small>
          ))}
        </div>
      )}

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

      <div className="evidence-legend">
        <span><i className="legend-blue" /> منخفض</span>
        <span><i className="legend-green" /> متوسط</span>
        <span><i className="legend-yellow" /> ملحوظ</span>
        <span><i className="legend-red" /> مرتفع</span>
        <small>ألوان ثابتة مع التكبير: اللون يعكس Score الخلية نفسها، وليس كثافة النقاط على الشاشة.</small>
      </div>

      <div ref={containerRef} className="map map-enhanced" aria-label="خريطة HorusScan" />

      {result?.council && <CouncilResultPanel council={result.council} />}
    </div>
  );
}
