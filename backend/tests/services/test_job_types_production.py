from backend.services.job_types import JobKind


def test_production_kinds_exist():
    assert JobKind.PRODUCTION.value == "production"
    assert JobKind.LORA_TRAIN.value == "lora_train"


def test_production_kinds_distinct_from_existing():
    existing = {JobKind.TASK, JobKind.TRAINING, JobKind.VIDEO_RENDER}
    assert JobKind.PRODUCTION not in existing
    assert JobKind.LORA_TRAIN not in existing
