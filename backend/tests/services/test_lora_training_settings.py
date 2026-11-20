from backend.services.lora_training_settings import normalize_training_settings, DEFAULTS


def test_normalize_defaults_are_the_bases_recipe():
    from backend.services.media_model_registry import train_recipe

    out = normalize_training_settings({"base_model_id": "zimage-turbo"})
    recipe = train_recipe("zimage-turbo")
    for key in ("resolution", "rank", "alpha", "lora_targets", "base_storage"):
        assert out[key] == recipe[key]
    assert out["learning_rate"] == 1.0e-4
    assert out["steps"] is None


def test_a_base_without_a_recipe_uses_the_module_defaults():
    out = normalize_training_settings({"base_model_id": "sdxl-legacy"})
    for key in ("resolution", "rank", "alpha", "lora_targets", "base_storage"):
        assert out[key] == DEFAULTS[key]


def test_typed_values_win_over_the_recipe():
    out = normalize_training_settings(
        {"base_model_id": "zimage-turbo", "resolution": 512, "rank": 16, "lora_targets": "attention"})
    assert (out["resolution"], out["rank"], out["lora_targets"]) == (512, 16, "attention")
    assert out["base_storage"] == "float8"


def test_unknown_layer_and_storage_names_fall_back_to_the_recipe():
    out = normalize_training_settings(
        {"base_model_id": "zimage-turbo", "lora_targets": "everything", "base_storage": "int4"})
    assert (out["lora_targets"], out["base_storage"]) == ("attention_ffn", "float8")


def test_settings_stored_before_recipes_take_the_recipe():
    """The Cast form used to store its own defaults for every subject."""
    from types import SimpleNamespace
    from backend.services.lora_training_settings import settings_for_subject

    old_form = SimpleNamespace(training_settings_json={
        "resolution": 768, "rank": 16, "alpha": 16, "learning_rate": 1e-4,
        "steps": None, "base_model_id": "zimage-turbo"})
    assert settings_for_subject(old_form)["resolution"] == 1024
    assert settings_for_subject(old_form)["rank"] == 32

    chosen = SimpleNamespace(training_settings_json={
        "resolution": 640, "rank": 16, "alpha": 16, "base_model_id": "zimage-turbo"})
    assert settings_for_subject(chosen)["resolution"] == 640
    assert settings_for_subject(chosen)["rank"] == 16

    saved_since = SimpleNamespace(training_settings_json={
        "resolution": 512, "rank": 16, "alpha": 16, "base_model_id": "zimage-turbo",
        "lora_targets": "attention_ffn", "base_storage": "float8"})
    assert settings_for_subject(saved_since)["resolution"] == 512


def test_default_steps_follow_the_recipe():
    from backend.services.media_model_registry import default_train_steps

    assert default_train_steps("zimage-turbo", 4) == 400
    assert default_train_steps("zimage-turbo", 12) == 960
    assert default_train_steps("zimage-turbo", 31) == 1500
    assert default_train_steps("sdxl-legacy", 12) is None


def test_normalize_clamps_and_snaps():
    out = normalize_training_settings({
        "resolution": 900,
        "rank": 2,
        "alpha": 200,
        "learning_rate": 5e-5,
        "steps": 800,
    })
    assert out["resolution"] == 896  # snapped to /64
    assert out["rank"] == 4
    assert out["alpha"] == 128
    assert out["learning_rate"] == 5e-5
    assert out["steps"] == 800


def test_normalize_ignores_none_overrides():
    out = normalize_training_settings(
        {"steps": None, "resolution": None, "base_model_id": "sdxl-legacy"})
    assert out["resolution"] == DEFAULTS["resolution"]
    assert out["steps"] is None