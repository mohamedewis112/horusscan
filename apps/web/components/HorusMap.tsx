"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap } from "maplibre-gl";
import { supabase } from "../lib/supabase";

type Position = [number, number];

type HyperFusionResponse = {
  real_data: boolean;
  persisted: boolean;
  engine: string;
  branches_used: string[];
  branches: Record<string, SensorSummary>;
  fusion: {
    summary: SensorSummary & {
      sensors?: string[];
      evidence_agreement?: string;
      agreement_fraction?: number;
    };
    heatmap: FeatureCollection;
  };
  discovery: {
    sentinel2_count: number;
    sentinel1_count: number;
    dem_count: number;
    enmap_count: number;
    landsat_thermal_count: number;
    emit_count: number;
  };
  limitations: Record<string, unknown>;
  scientific_status: string;
};

type SensorSummary = {
  status: string;
  valid_pixels: number;
  score_type: string;
  not_probability: boolean;
  p50?: number;
  p90?: number;
  p99?: number;
  observations_used?: number;
  tiles_used?: number;
};

type FeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Point"; coordinates: [number, number] };
    properties: { anomaly_score: number };
  }>;
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
  const [result, setResult] = useState<HyperFusionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authStatus, setAuthStatus] = useState("Checking session…");
  const [signedIn, setSignedIn] = useState(false);
  const persistenceEnabled = process.env.NEXT_PUBLIC_PERSISTENCE_ENABLED === "true";

  useEffect(() => {
    if (!supabase) {
      setAuthStatus("Supabase environment is not configured.");
      return;
    }
    supabase.auth.getSession().then(({ data }) => {
      setSignedIn(Boolean(data.session));
      setAuthStatus(data.session ? data.session.user.email ?? "Signed in" : "Sign in to persist scans.");
    });
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setSignedIn(Boolean(session));
      setAuthStatus(session ? session.user.email ?? "Signed in" : "Sign in to persist scans.");
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: "https://demotiles.maplibre.org/style.json",
      center: [30.8, 26.8],
      zoom: 4.7,
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
      const data = {
        type: "FeatureCollection" as const,
        features: points.length
          ? [{
              type: "Feature" as const,
              properties: {},
              geometry: points.length >= 3
                ? { type: "Polygon" as const, coordinates: [ring] }
                : { type: "LineString" as const, coordinates: points },
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
        (map.getSource("horus-heatmap") as maplibregl.GeoJSONSource).setData(result.fusion.heatmap);
        return;
      }
      map.addSource("horus-heatmap", { type: "geojson", data: result.fusion.heatmap });
      map.addLayer({
        id: "horus-anomaly-heat",
        type: "heatmap",
        source: "horus-heatmap",
        maxzoom: 15,
        paint: {
          "heatmap-weight": ["interpolate", ["linear"], ["get", "anomaly_score"], 0, 0, 1, 1],
          "heatmap-intensity": 1.25,
          "heatmap-radius": 24,
          "heatmap-opacity": 0.8,
        },
      });
    };

    if (map.isStyleLoaded()) draw();
    else map.once("load", draw);
  }, [result]);

  const polygon = useMemo(() => {
    if (points.length < 3) return null;
    return {
      type: "Polygon" as const,
      coordinates: [[...points, points[0]]],
    };
  }, [points]);

  const bbox = useMemo(() => {
    if (!polygon) return null;
    const lons = points.map(([lon]) => lon);
    const lats = points.map(([, lat]) => lat);
    return [
      Math.min(...lons),
      Math.min(...lats),
      Math.max(...lons),
      Math.max(...lats),
    ] as [number, number, number, number];
  }, [points, polygon]);

  async function signIn() {
    if (!supabase) return;
    setError(null);
    const { error: authError } = await supabase.auth.signInWithPassword({ email, password });
    if (authError) setError(authError.message);
  }

  async function signUp() {
    if (!supabase) return;
    setError(null);
    const { error: authError } = await supabase.auth.signUp({ email, password });
    if (authError) setError(authError.message);
    else setAuthStatus("Account created. Check email if confirmation is enabled.");
  }

  async function ensureProject(userId: string): Promise<string> {
    if (!supabase) throw new Error("Supabase is not configured.");
    const existing = await supabase
      .from("projects")
      .select("id")
      .order("created_at", { ascending: true })
      .limit(1)
      .maybeSingle();
    if (existing.error) throw existing.error;
    if (existing.data?.id) return existing.data.id as string;

    const created = await supabase
      .from("projects")
      .insert({
        owner_id: userId,
        name: "HorusScan Research",
        description: "Default scientific workspace",
      })
      .select("id")
      .single();
    if (created.error) throw created.error;
    return created.data.id as string;
  }

  async function createPersistedRun() {
    if (!supabase || !polygon) throw new Error("Sign in and draw an AOI first.");
    const { data: sessionData, error: sessionError } = await supabase.auth.getSession();
    if (sessionError) throw sessionError;
    const session = sessionData.session;
    if (!session) throw new Error("Sign in before running a persistent scan.");

    const projectId = await ensureProject(session.user.id);

    const aoiResult = await supabase.rpc("create_area_of_interest", {
      p_project_id: projectId,
      p_name: `AOI ${new Date().toISOString()}`,
      p_geojson: polygon,
    });
    if (aoiResult.error) throw aoiResult.error;
    const aoi = Array.isArray(aoiResult.data) ? aoiResult.data[0] : aoiResult.data;
    if (!aoi?.id) throw new Error("AOI persistence failed.");

    const runResult = await supabase
      .from("analysis_runs")
      .insert({
        project_id: projectId,
        aoi_id: aoi.id,
        requested_by: session.user.id,
        status: "queued",
        progress_stage: "queued",
        parameters: {
          start,
          end,
          max_cloud: 20,
          sensors: ["sentinel-2", "sentinel-1-rtc", "copernicus-dem-glo-30", "enmap-l2a", "landsat-thermal", "emit-discovery"],
        },
      })
      .select("id")
      .single();
    if (runResult.error) throw runResult.error;

    return {
      token: session.access_token,
      projectId,
      aoiId: aoi.id as string,
      runId: runResult.data.id as string,
    };
  }

  async function initiateScan() {
    if (!bbox || !polygon) return;
    setBusy(true);
    setError(null);
    setResult(null);

    try {
      const persisted = persistenceEnabled ? await createPersistedRun() : null;
      const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
      const headers: Record<string, string> = { "content-type": "application/json" };
      if (persisted) headers.authorization = `Bearer ${persisted.token}`;

      const response = await fetch(`${base}/v1/analysis/hyperfusion-v0`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          bbox,
          start: `${start}T00:00:00Z`,
          end: `${end}T23:59:59Z`,
          max_cloud: 20,
          ...(persisted
            ? {
                project_id: persisted.projectId,
                aoi_id: persisted.aoiId,
                analysis_run_id: persisted.runId,
              }
            : {}),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Analysis failed.");
      setResult(payload as HyperFusionResponse);
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
        <button className="primary" disabled={!bbox || busy || (persistenceEnabled && !signedIn)} onClick={initiateScan}>
          {busy ? "HYPERFUSION ANALYSIS…" : persistenceEnabled ? "INITIATE HORUS SCAN" : "RUN LIVE HYPERFUSION"}
        </button>
      </div>

      <div className="auth-panel">
        <strong>{persistenceEnabled ? authStatus : "Live preview mode · scans are not persisted"}</strong>
        {!signedIn && (
          <>
            <input
              type="email"
              placeholder="Research email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button onClick={signIn}>Sign in</button>
            <button onClick={signUp}>Create account</button>
          </>
        )}
        {signedIn && supabase && (
          <button onClick={() => void supabase?.auth.signOut()}>Sign out</button>
        )}
      </div>

      {error && <div className="result-card error">{error}</div>}
      {result && (
        <div className="result-card">
          <strong>REAL HYPERFUSION DATA · PERSISTED {result.persisted ? "✓" : "—"}</strong>
          <span>Engine: {result.engine}</span>
          <span>Evidence branches: {result.branches_used.join(" · ")}</span>
          <span>
            Evidence agreement: {result.fusion.summary.evidence_agreement ?? "inconclusive"}
          </span>
          <span>
            Fusion P90: {result.fusion.summary.p90 !== undefined
              ? result.fusion.summary.p90.toFixed(3)
              : "n/a"}
          </span>
          <span>
            Advanced coverage: EnMAP {result.discovery.enmap_count} · Thermal {result.discovery.landsat_thermal_count} · EMIT {result.discovery.emit_count}
          </span>
          {Object.keys(result.limitations).length > 0 && (
            <small>Unavailable/limited branches: {Object.keys(result.limitations).join(", ")}</small>
          )}
          <small>{result.scientific_status}</small>
        </div>
      )}

      <div ref={containerRef} className="map" aria-label="HorusScan analysis map" />
    </div>
  );
}
