"""Unit tests for the step text and ids behind demonstration lessons."""
from backend.services.demonstration_lessons import (
    clean_target_description,
    describe_step,
    new_attempt_lesson_id,
    scroll_amount,
    steps_fingerprint,
)


class TestCleanTargetDescription:
    def test_leading_coordinates_are_dropped(self):
        text = 'The UI element at (104, 460) is a folder icon for "Pictures".'
        assert clean_target_description(text) == 'a folder icon for "Pictures".'

    def test_pixel_coordinates_wording(self):
        text = 'The UI element at pixel coordinates (104, 460) is a folder icon labeled "Pictures".'
        assert clean_target_description(text) == 'a folder icon labeled "Pictures".'

    def test_coordinates_inside_the_sentence(self):
        assert clean_target_description("The save button located at (12, 40) in the toolbar") == \
            "The save button in the toolbar"

    def test_the_element_is_lead_in_is_dropped(self):
        assert clean_target_description('The element is an icon labeled "Pictures".') == \
            'an icon labeled "Pictures".'

    def test_text_without_coordinates_is_kept(self):
        assert clean_target_description("the blue Save button") == "the blue Save button"

    def test_not_a_string(self):
        assert clean_target_description(None) == ""


class TestDescribeStep:
    def test_click(self):
        step = {"action_type": "click", "target_description": "The UI element at (1, 2) is the Save button."}
        assert describe_step(step) == "Click the Save button"

    def test_click_without_a_description(self):
        assert describe_step({"action_type": "click", "target_description": ""}) == "Click the recorded spot"

    def test_type(self):
        assert describe_step({"action_type": "type", "text": "hello"}) == 'Type "hello"'

    def test_hotkey(self):
        assert describe_step({"action_type": "hotkey", "keys": "ctrl+s"}) == "Press ctrl+s"

    def test_scroll_direction_comes_from_the_recorded_amount(self):
        assert describe_step({"action_type": "scroll", "text": "-5", "target_description": "the list"}) == \
            "Scroll down over the list"
        assert describe_step({"action_type": "scroll", "text": "3", "target_description": ""}) == "Scroll up"


def test_scroll_amount_reads_the_text_column():
    assert scroll_amount({"text": "-7"}) == -7
    assert scroll_amount({"scroll_amount": 4, "text": "-7"}) == 4
    assert scroll_amount({"text": None}) == -3


def test_attempt_lesson_id_fits_the_column_and_names_the_demonstration():
    lesson_id = new_attempt_lesson_id(123456)
    assert lesson_id.startswith("demo123456-")
    assert len(lesson_id) <= 36


def test_fingerprint_changes_with_a_step():
    steps = [{"action_type": "click", "target_description": "Save", "text": None, "keys": None}]
    changed = [{**steps[0], "target_description": "Cancel"}]
    assert steps_fingerprint(steps) == steps_fingerprint([dict(steps[0])])
    assert steps_fingerprint(steps) != steps_fingerprint(changed)
