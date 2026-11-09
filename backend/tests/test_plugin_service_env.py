"""service_env() is what every plugin service starts with."""
from backend.services.plugin_runner import service_env


def test_flask_cli_marker_is_not_passed_to_plugin_services():
    # Inherited from a backend started with `flask run`, it makes Flask skip the
    # service's own app.run() and the port never opens.
    env = service_env({"FLASK_RUN_FROM_CLI": "true", "PATH": "/usr/bin"})
    assert "FLASK_RUN_FROM_CLI" not in env
    assert env["PATH"] == "/usr/bin"


def test_a_chosen_gpu_device_is_kept():
    assert service_env({"CUDA_VISIBLE_DEVICES": "1"})["CUDA_VISIBLE_DEVICES"] == "1"
    assert "CUDA_VISIBLE_DEVICES" not in service_env({"CUDA_VISIBLE_DEVICES": ""})
