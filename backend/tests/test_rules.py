import pytest


# This test will be skipped by the new script
def test_a_very_slow_and_complex_scenario():
    # ...
    assert True


@pytest.mark.fast
def test_rule_is_generated_correctly():
    # A quick check
    assert True


@pytest.mark.fast
def test_another_quick_check():
    # ...
    assert True
