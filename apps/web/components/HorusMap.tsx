"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl, { Map as MapLibreMap } from "maplibre-gl";

type Position = [number, number];

export default function HorusMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [points, setPoints] = useState<Position[]>([]);
  const [drawing, setDrawing] = useState(false);

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

  const polygon = points.length >= 3
    ? { type: "Polygon", coordinates: [[...points, points[0]]] }
    : null;

  return (
    <>
      <div className="actions">
        <button className="primary" onClick={() => setDrawing((v) => !v)}>
          {drawing ? "Stop drawing" : "Draw AOI"}
        </button>
        <button onClick={() => setPoints([])}>Clear</button>
      </div>
      <div className="coords">
        {polygon ? JSON.stringify(polygon) : "Draw at least 3 points to define an AOI."}
      </div>
      <div ref={containerRef} className="map" aria-label="HorusScan analysis map" />
    </>
  );
}
