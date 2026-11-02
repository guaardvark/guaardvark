# Enable backend as a package for tests and expose Socket.IO instance
import os

# ONNX Runtime reports usage to Microsoft unless told not to: a device id, this
# interpreter's path, the CPU and OS, and the file name of each model it loads.
# It reads the switch when the library starts, and piper, docling and the intent
# model all pull it in, so it is set here for any process that did not come
# through start.sh, and for everything those processes launch.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

from .no_netrc import install as _install_no_netrc  # noqa: E402

# Before anything opens a connection: no request this process makes sends a
# ~/.netrc login (see backend/no_netrc.py).
_install_no_netrc()

from .socketio_events import socketio  # noqa: E402
