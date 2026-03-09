import os
import stat
import tempfile

from backend.tools import security_self_check as ssc


def test_debug_detection():
    env = {"FLASK_DEBUG": "true"}
    assert ssc.check_debug_mode(env) is not None
    env = {"FLASK_DEBUG": "0"}
    assert ssc.check_debug_mode(env) is None


def test_weak_password_detection():
    env = {"POSTGRES_PASSWORD": "password", "SOME_PASS": "1234"}
    warnings = ssc.check_weak_passwords(env)
    assert any("POSTGRES_PASSWORD" in w for w in warnings)
    assert any("SOME_PASS" in w for w in warnings)


def test_world_writable_detection(tmp_path):
    test_file = tmp_path / "secret.txt"
    test_file.write_text("secret")
    test_file.chmod(0o666)
    warnings = ssc.check_world_writable([str(test_file)])
    assert warnings
