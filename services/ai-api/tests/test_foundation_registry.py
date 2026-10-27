from app.foundation.registry import get_model_registry


def test_registry_contains_all_requested_models() -> None:
    keys = {row["key"] for row in get_model_registry()}
    expected = {
        "terramind",
        "prithvi_eo_2",
        "dinov3_satellite",
        "sam3_1",
        "spectralearth_fm",
        "terraflow",
        "olmoearth",
        "physics_sar",
        "thermal_inertia",
    }
    assert expected.issubset(keys)


def test_unconfigured_models_do_not_claim_availability(monkeypatch) -> None:
    monkeypatch.delenv("TERRAMIND_INFERENCE_URL", raising=False)
    row = next(item for item in get_model_registry() if item["key"] == "terramind")
    assert row["available"] is False
    assert row["reason"]
