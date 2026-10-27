from __future__ import annotations

from math import cos, pi
from typing import Any

import numpy as np

from app.pipeline.reference_bank import REFERENCE_FINGERPRINTS, REFERENCE_SITES


COMPARABLE_BRANCHES = ("spectral", "sar", "terrain", "thermal", "olmoearth")


def _base_name(name: str) -> str:
    if name.startswith("olmoearth_"):
        return "olmoearth"
    return name


def _candidate_vector(branch_scores: dict[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for name, value in branch_scores.items():
        base = _base_name(name)
        if base in COMPARABLE_BRANCHES and np.isfinite(value):
            result[base] = float(np.clip(value, 0.0, 1.0))
    return result


def _similarity(a: dict[str, float], b: dict[str, float]) -> tuple[float, int]:
    shared = [key for key in COMPARABLE_BRANCHES if key in a and key in b]
    if len(shared) < 3:
        return 0.0, len(shared)
    errors = [abs(float(a[key]) - float(b[key])) for key in shared]
    score = 1.0 - float(np.mean(errors))
    return float(np.clip(score, 0.0, 1.0)), len(shared)


def _verdict(score: float) -> str:
    # Retrieval thresholds are provisional and deliberately do not change the
    # calibrated HyperFusion confidence until held-out calibration is done.
    if score >= 0.80:
        return "support"
    if score <= 0.55:
        return "challenge"
    return "neutral"


def review_candidate_with_archaeology_memory(
    branch_scores: dict[str, float],
    candidate_lat: float,
    candidate_lon: float,
) -> dict[str, Any]:
    candidate = _candidate_vector(branch_scores)
    matches: list[dict[str, Any]] = []

    for slug, fingerprint in REFERENCE_FINGERPRINTS.items():
        site = REFERENCE_SITES.get(slug)
        if not site:
            continue
        similarity, shared = _similarity(candidate, fingerprint)
        if shared < 3:
            continue

        # Distance is reported for context only. It never contributes to the
        # similarity score, avoiding "near known archaeology = archaeology".
        mean_lat = (candidate_lat + float(site["lat"])) / 2.0
        dy = (candidate_lat - float(site["lat"])) * 111_320.0
        dx = (
            (candidate_lon - float(site["lon"]))
            * 111_320.0
            * max(cos(mean_lat * pi / 180.0), 0.2)
        )
        distance_km = float(np.hypot(dx, dy) / 1000.0)

        matches.append(
            {
                "slug": slug,
                "name": site["name"],
                "archetype": site["archetype"],
                "site_type": site["site_type"],
                "similarity": round(similarity, 4),
                "shared_branch_count": shared,
                "distance_km_context_only": round(distance_km, 1),
                "public_source": site["source"],
            }
        )

    matches.sort(key=lambda row: row["similarity"], reverse=True)
    top = matches[:5]

    archetype_scores: dict[str, list[float]] = {}
    for row in matches:
        archetype_scores.setdefault(str(row["archetype"]), []).append(float(row["similarity"]))

    archetypes = [
        {
            "archetype": archetype,
            "similarity": round(float(np.mean(sorted(values, reverse=True)[:3])), 4),
            "reference_count": len(values),
        }
        for archetype, values in archetype_scores.items()
    ]
    archetypes.sort(key=lambda row: row["similarity"], reverse=True)

    memory_score = (
        float(np.mean([row["similarity"] for row in top[:3]]))
        if top
        else 0.0
    )

    necropolis_matches = [
        row
        for row in matches
        if bool(REFERENCE_SITES.get(str(row["slug"]), {}).get("necropolis"))
    ]
    necropolis_score = (
        float(np.mean([row["similarity"] for row in necropolis_matches[:3]]))
        if necropolis_matches
        else 0.0
    )

    return {
        "status": "ready" if top else "insufficient_reference_fingerprints",
        "fingerprint_count": len(REFERENCE_FINGERPRINTS),
        "candidate_comparable_branches": sorted(candidate),
        "memory_similarity_score": round(memory_score, 4),
        "memory_verdict": _verdict(memory_score) if top else "neutral",
        "necropolis_context_score": round(necropolis_score, 4),
        "necropolis_verdict": _verdict(necropolis_score) if necropolis_matches else "neutral",
        "top_reference_matches": top,
        "top_archetypes": archetypes[:5],
        "calibrated": False,
        "confidence_adjustment": 0.0,
        "distance_used_in_similarity": False,
        "interpretation": (
            "Reference retrieval is an adjudication-only review. Similarity is based on "
            "comparable sensor-evidence fingerprints; geographic distance is context only. "
            "It does not change calibrated HyperFusion confidence until separately validated."
        ),
    }


def center_mean_from_heatmap(
    heatmap: dict[str, Any],
    center_lat: float,
    center_lon: float,
    radius_m: int,
) -> float:
    values: list[float] = []
    nearest: list[tuple[float, float]] = []
    lat_scale = 111_320.0
    lon_scale = 111_320.0 * max(cos(center_lat * pi / 180.0), 0.2)

    for feature in heatmap.get("features", []):
        coords = feature.get("geometry", {}).get("coordinates")
        score = feature.get("properties", {}).get("anomaly_score")
        if not coords or score is None:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        dx = (lon - center_lon) * lon_scale
        dy = (lat - center_lat) * lat_scale
        distance = float(np.hypot(dx, dy))
        nearest.append((distance, float(score)))
        if distance <= radius_m:
            values.append(float(score))

    if not values and nearest:
        nearest.sort(key=lambda row: row[0])
        values = [score for _, score in nearest[:4]]
    if not values:
        raise ValueError("No numeric heatmap values around reference center.")
    return float(np.clip(np.mean(values), 0.0, 1.0))
