from __future__ import annotations

from math import cos, pi
from typing import Any

import numpy as np


INDEPENDENCE_GROUPS = {
    "spectral": "optical_physics",
    "sar": "radar_physics",
    "terrain": "terrain_physics",
    "thermal": "thermal_physics",
    "olmoearth": "foundation_multimodal",
    "terramind": "foundation_multimodal",
    "prithvi_eo_2": "temporal_foundation",
    "terraflow": "temporal_foundation",
    "dinov3_satellite": "geometry_foundation",
    "spectralearth_fm": "spectral_foundation",
    "physics_sar": "radar_physics",
    "thermal_inertia": "thermal_physics",
}


def _base_name(name: str) -> str:
    if name.startswith("olmoearth_"):
        return "olmoearth"
    return name


def _group(name: str) -> str:
    base = _base_name(name)
    return INDEPENDENCE_GROUPS.get(base, f"model:{base}")


def _priority(confidence: float) -> str:
    if confidence >= 82:
        return "very_high"
    if confidence >= 68:
        return "high"
    if confidence >= 52:
        return "medium"
    return "low"


def _position_for_cell(
    row: int,
    col: int,
    shape: tuple[int, int],
    bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    height, width = shape
    lon = min_lon + ((col + 0.5) / width) * (max_lon - min_lon)
    lat = max_lat - ((row + 0.5) / height) * (max_lat - min_lat)
    return float(lon), float(lat)


def _cell_size_m(
    lat: float,
    shape: tuple[int, int],
    bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    height, width = shape
    dy = abs(max_lat - min_lat) * 111_320.0 / max(height, 1)
    dx = (
        abs(max_lon - min_lon)
        * 111_320.0
        * max(cos(lat * pi / 180.0), 0.2)
        / max(width, 1)
    )
    return float(dx), float(dy)


def _candidate_polygon(
    lon: float,
    lat: float,
    uncertainty_m: float,
) -> dict[str, Any]:
    lat_delta = uncertainty_m / 111_320.0
    lon_delta = uncertainty_m / (
        111_320.0 * max(cos(lat * pi / 180.0), 0.2)
    )
    ring = [
        [lon - lon_delta, lat - lat_delta],
        [lon + lon_delta, lat - lat_delta],
        [lon + lon_delta, lat + lat_delta],
        [lon - lon_delta, lat + lat_delta],
        [lon - lon_delta, lat - lat_delta],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _local_mean(score: np.ndarray, row: int, col: int, radius: int = 1) -> float:
    r0 = max(0, row - radius)
    r1 = min(score.shape[0], row + radius + 1)
    c0 = max(0, col - radius)
    c1 = min(score.shape[1], col + radius + 1)
    values = score[r0:r1, c0:c1]
    values = values[np.isfinite(values)]
    return float(np.mean(values)) if values.size else 0.0


def _find_candidates(
    fused: np.ndarray,
    limit: int = 5,
) -> list[tuple[int, int, float]]:
    valid = fused[np.isfinite(fused)]
    if not valid.size:
        return []

    threshold = float(np.quantile(valid, 0.90))
    cells: list[tuple[int, int, float]] = []
    height, width = fused.shape

    for row in range(height):
        for col in range(width):
            value = float(fused[row, col]) if np.isfinite(fused[row, col]) else -1.0
            if value < threshold:
                continue
            r0, r1 = max(0, row - 2), min(height, row + 3)
            c0, c1 = max(0, col - 2), min(width, col + 3)
            neighborhood = fused[r0:r1, c0:c1]
            local_max = np.nanmax(neighborhood)
            if value + 1e-8 < local_max:
                continue
            cells.append((row, col, value))

    cells.sort(key=lambda item: item[2], reverse=True)
    selected: list[tuple[int, int, float]] = []
    for candidate in cells:
        row, col, _ = candidate
        if any((row-r) ** 2 + (col-c) ** 2 < 36 for r, c, _ in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break

    if not selected and cells:
        selected = cells[:1]
    return selected


def _alternative_explanations(branch_scores: dict[str, float]) -> list[str]:
    def score(name: str) -> float:
        for key, value in branch_scores.items():
            if _base_name(key) == name:
                return value
        return 0.0

    alternatives: list[str] = []
    spectral = score("spectral")
    sar = score("sar")
    terrain = score("terrain")
    thermal = score("thermal")
    olmo = score("olmoearth")

    if spectral >= 0.68 and sar < 0.48 and thermal < 0.48:
        alternatives.append("surface_or_vegetation_change")
    if thermal >= 0.68 and sar < 0.48 and terrain < 0.48:
        alternatives.append("surface_temperature_or_moisture_effect")
    if terrain >= 0.68 and spectral < 0.5 and sar < 0.5:
        alternatives.append("natural_geomorphology")
    if sar >= 0.68 and spectral < 0.48 and thermal < 0.48:
        alternatives.append("surface_roughness_or_radar_scattering")
    if olmo >= 0.68 and max(spectral, sar, terrain, thermal) < 0.55:
        alternatives.append("foundation_representation_anomaly_without_physical_agreement")
    return alternatives


def build_council_report(
    evidence: dict[str, np.ndarray],
    fused: np.ndarray,
    bbox: tuple[float, float, float, float],
    fusion_profile: str,
    limitations: dict[str, str],
    max_candidates: int = 5,
) -> dict[str, Any]:
    candidates = _find_candidates(fused, max_candidates)
    reports: list[dict[str, Any]] = []

    for index, (row, col, fused_peak) in enumerate(candidates, start=1):
        branch_scores = {
            name: _local_mean(score, row, col, radius=1)
            for name, score in evidence.items()
            if score.shape == fused.shape
        }

        verdicts: dict[str, str] = {}
        supporters: list[str] = []
        challengers: list[str] = []
        neutrals: list[str] = []
        groups_supporting: set[str] = set()
        groups_present: set[str] = set()

        for name, score in branch_scores.items():
            groups_present.add(_group(name))
            if score >= 0.66:
                verdicts[name] = "support"
                supporters.append(name)
                groups_supporting.add(_group(name))
            elif score < 0.36:
                verdicts[name] = "challenge"
                challengers.append(name)
            else:
                verdicts[name] = "neutral"
                neutrals.append(name)

        independent_ratio = (
            len(groups_supporting) / len(groups_present)
            if groups_present
            else 0.0
        )
        mean_support = float(np.mean(list(branch_scores.values()))) if branch_scores else 0.0
        dispersion = float(np.std(list(branch_scores.values()))) if branch_scores else 0.5
        contradiction_ratio = (
            len(challengers) / len(branch_scores)
            if branch_scores
            else 1.0
        )

        alternatives = _alternative_explanations(branch_scores)
        alternative_penalty = min(0.16, 0.05 * len(alternatives))

        raw_index = (
            0.48 * fused_peak
            + 0.27 * mean_support
            + 0.25 * independent_ratio
            - 0.12 * contradiction_ratio
            - alternative_penalty
        )
        evidence_confidence = float(np.clip(raw_index, 0.0, 1.0) * 100.0)

        uncertainty_points = float(
            np.clip(
                7.0
                + 25.0 * dispersion
                + 10.0 * contradiction_ratio
                + 5.0 * len(alternatives),
                7.0,
                28.0,
            )
        )

        lon, lat = _position_for_cell(row, col, fused.shape, bbox)
        cell_dx, cell_dy = _cell_size_m(lat, fused.shape, bbox)
        # Public research output is deliberately coarse. Exact subsurface-target
        # coordinates/polygons are not emitted for unknown candidates.
        spatial_uncertainty_m = float(
            max(500.0, min(1500.0, 6.0 * max(cell_dx, cell_dy)))
        )

        reports.append(
            {
                "candidate_id": f"H-{index:03d}",
                "approximate_center": {
                    "lon": round(lon, 2),
                    "lat": round(lat, 2),
                    "precision": "coarse_research_zone",
                },
                "geometry": _candidate_polygon(lon, lat, spatial_uncertainty_m),
                "geometry_source": "coarse_research_zone_not_excavation_target",
                "sam_segmentation_status": (
                    "pending_real_endpoint"
                    if "sam3_1" in limitations
                    else "not_requested"
                ),
                "fusion_peak": fused_peak,
                "evidence_confidence_index": round(evidence_confidence, 1),
                "confidence_kind": "calibrated_rank_index_not_probability",
                "uncertainty_band": {
                    "low": round(max(0.0, evidence_confidence - uncertainty_points), 1),
                    "high": round(min(100.0, evidence_confidence + uncertainty_points), 1),
                },
                "spatial_uncertainty_m": round(spatial_uncertainty_m, 1),
                "priority": _priority(evidence_confidence),
                "independent_support_groups": sorted(groups_supporting),
                "independent_support_count": len(groups_supporting),
                "independent_groups_present": len(groups_present),
                "supporters": supporters,
                "challengers": challengers,
                "neutral": neutrals,
                "branch_scores": {k: round(v, 4) for k, v in branch_scores.items()},
                "consensus_matrix": [
                    {
                        "member": name,
                        "independence_group": _group(name),
                        "verdict": verdicts[name],
                        "evidence_score": round(score, 4),
                    }
                    for name, score in branch_scores.items()
                ],
                "critic": {
                    "alternative_natural_explanations": alternatives,
                    "contradiction_ratio": round(contradiction_ratio, 4),
                    "branch_dispersion": round(dispersion, 4),
                    "verdict": (
                        "supported_with_independent_evidence"
                        if len(groups_supporting) >= 3 and contradiction_ratio <= 0.25
                        else "promising_but_contested"
                        if len(groups_supporting) >= 2
                        else "weak_or_explainable"
                    ),
                },
                "council_summary": {
                    "support_count": len(supporters),
                    "challenge_count": len(challengers),
                    "neutral_count": len(neutrals),
                    "model_agreement": (
                        "strong"
                        if len(groups_supporting) >= 4 and contradiction_ratio <= 0.2
                        else "moderate"
                        if len(groups_supporting) >= 2
                        else "weak"
                    ),
                },
            }
        )

    return {
        "version": "horus-council-v1",
        "fusion_profile": fusion_profile,
        "candidate_count": len(reports),
        "candidates": reports,
        "method": {
            "deliberation": "structured_evidence_adjudication_not_freeform_chat",
            "evidence_independence": "correlated models are grouped before consensus",
            "confidence": "calibrated ranking index with disagreement-derived uncertainty; not probability of archaeology",
            "candidate_generation": "local maxima summarized into coarse research zones; exact target coordinates withheld",
        },
    }
