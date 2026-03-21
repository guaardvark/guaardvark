#!/usr/bin/env python3
"""
PipeWire Screen Capture — VNC-style framebuffer access for Wayland.

Opens a PipeWire screen capture stream via the XDG Desktop Portal,
then grabs frames on demand. No flash, no sound, no screenshots —
just reading the video signal like VNC does.

Usage:
    capture = PipeWireCapture()
    capture.start()           # One-time: opens stream (user grants permission once)
    frame = capture.grab()    # Grab current frame as PIL Image (silent, instant)
    capture.stop()            # Clean up
"""

import logging
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# These imports may fail in test/headless environments
try:
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst, GLib
    from PIL import Image
    PIPEWIRE_AVAILABLE = True
except (ImportError, ValueError) as e:
    logger.warning(f"PipeWire capture unavailable: {e}")
    PIPEWIRE_AVAILABLE = False


class PipeWireCapture:
    """
    Captures screen frames via PipeWire ScreenCast portal.

    This is the same mechanism VNC (wayvnc), OBS, and Chrome screen sharing
    use on Wayland. Opens a continuous video stream of the screen and lets
    you grab individual frames silently.
    """

    def __init__(self):
        self._pipeline = None
        self._sink = None
        self._latest_frame: Optional[Image.Image] = None
        self._frame_lock = threading.Lock()
        self._running = False
        self._loop_thread = None
        self._loop = None
        self._pw_fd = None
        self._pw_node_id = None
        self._session_handle = None
        self._cursor_pos: Tuple[int, int] = (0, 0)

    @property
    def available(self) -> bool:
        return PIPEWIRE_AVAILABLE

    def start(self, show_cursor: bool = True) -> bool:
        """
        Start the screen capture stream.

        First call triggers a one-time GNOME permission dialog.
        Subsequent starts reuse the session if possible.

        Args:
            show_cursor: Whether to include cursor in the capture

        Returns:
            True if stream started successfully
        """
        if not PIPEWIRE_AVAILABLE:
            logger.error("PipeWire capture not available (missing dbus/gi/Gst)")
            return False

        if self._running:
            return True

        try:
            # Initialize GStreamer
            Gst.init(None)

            # Set up D-Bus main loop
            DBusGMainLoop(set_as_default=True)

            # Get the ScreenCast portal
            bus = dbus.SessionBus()
            portal = bus.get_object(
                'org.freedesktop.portal.Desktop',
                '/org/freedesktop/portal/desktop'
            )
            screencast = dbus.Interface(portal, 'org.freedesktop.portal.ScreenCast')

            # Create session
            session_opts = dbus.Dictionary({
                'handle_token': dbus.String('guaardvark_agent'),
                'session_handle_token': dbus.String('guaardvark_session'),
            }, signature='sv')
            self._request_screencast(bus, screencast, session_opts, show_cursor)
            return self._running

        except Exception as e:
            logger.error(f"Failed to start PipeWire capture: {e}", exc_info=True)
            return False

    def _request_screencast(self, bus, screencast, session_opts, show_cursor):
        """Handle the async D-Bus portal flow for screen capture."""
        import random
        token = f"guaardvark_{random.randint(1000, 9999)}"

        # We need to handle the portal's async response signals
        # Use a synchronous approach with GLib main loop

        result = {}
        error = {}
        done_event = threading.Event()

        def on_response(response_code, response_body):
            if response_code == 0:
                result.update(response_body)
            else:
                error['code'] = response_code
            done_event.set()

        # Step 1: CreateSession
        request_path = screencast.CreateSession(session_opts)
        bus.add_signal_receiver(
            on_response,
            signal_name='Response',
            dbus_interface='org.freedesktop.portal.Request',
            path=request_path
        )

        # Run GLib loop briefly to get the response
        loop = GLib.MainLoop()
        def check_done():
            if done_event.is_set():
                loop.quit()
                return False
            return True
        GLib.timeout_add(50, check_done)
        GLib.timeout_add(10000, lambda: (loop.quit(), False)[1])  # 10s timeout
        loop.run()

        if error or not result:
            logger.error(f"CreateSession failed: {error}")
            return

        session_handle = result.get('session_handle', '')
        self._session_handle = session_handle
        logger.info(f"ScreenCast session: {session_handle}")

        # Step 2: SelectSources (monitor)
        done_event.clear()
        result.clear()
        error.clear()

        cursor_mode = 2 if show_cursor else 1  # 2=embedded, 1=hidden
        source_opts = dbus.Dictionary({
            'handle_token': dbus.String(f'{token}_src'),
            'types': dbus.UInt32(1),  # 1=monitor
            'cursor_mode': dbus.UInt32(cursor_mode),
            'persist_mode': dbus.UInt32(2),  # 2=persistent until revoked
        }, signature='sv')

        request_path = screencast.SelectSources(
            dbus.ObjectPath(session_handle), source_opts
        )
        bus.add_signal_receiver(
            on_response,
            signal_name='Response',
            dbus_interface='org.freedesktop.portal.Request',
            path=request_path
        )

        loop = GLib.MainLoop()
        GLib.timeout_add(50, check_done)
        GLib.timeout_add(30000, lambda: (loop.quit(), False)[1])  # 30s for user interaction
        loop.run()

        if error:
            logger.error(f"SelectSources failed: {error}")
            return

        # Step 3: Start (get PipeWire node)
        done_event.clear()
        result.clear()
        error.clear()

        start_opts = dbus.Dictionary({
            'handle_token': dbus.String(f'{token}_start'),
        }, signature='sv')

        request_path = screencast.Start(
            dbus.ObjectPath(session_handle), '', start_opts
        )
        bus.add_signal_receiver(
            on_response,
            signal_name='Response',
            dbus_interface='org.freedesktop.portal.Request',
            path=request_path
        )

        loop = GLib.MainLoop()
        GLib.timeout_add(50, check_done)
        GLib.timeout_add(30000, lambda: (loop.quit(), False)[1])
        loop.run()

        if error or not result:
            logger.error(f"Start failed: {error}")
            return

        # Extract PipeWire stream info
        streams = result.get('streams', [])
        if not streams:
            logger.error("No streams returned from portal")
            return

        node_id = streams[0][0]  # PipeWire node ID
        self._pw_node_id = node_id
        logger.info(f"PipeWire node: {node_id}")

        # Get the PipeWire fd
        self._pw_fd = screencast.OpenPipeWireRemote(
            dbus.ObjectPath(session_handle),
            dbus.Dictionary({}, signature='sv')
        )
        fd_num = self._pw_fd.take()
        logger.info(f"PipeWire fd: {fd_num}")

        # Step 4: Create GStreamer pipeline to read frames
        pipeline_str = (
            f'pipewiresrc fd={fd_num} path={node_id} do-timestamp=true keepalive-time=1000 '
            f'! video/x-raw,max-framerate=2/1 '
            f'! videoconvert '
            f'! video/x-raw,format=RGB '
            f'! appsink name=sink emit-signals=true max-buffers=1 drop=true'
        )
        logger.info(f"GStreamer pipeline: {pipeline_str}")

        self._pipeline = Gst.parse_launch(pipeline_str)
        self._sink = self._pipeline.get_by_name('sink')
        self._sink.connect('new-sample', self._on_new_sample)

        self._pipeline.set_state(Gst.State.PLAYING)
        self._running = True
        logger.info("PipeWire screen capture started")

    def _on_new_sample(self, sink):
        """Called by GStreamer when a new frame is available."""
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        struct = caps.get_structure(0)
        width = struct.get_int('width')[1]
        height = struct.get_int('height')[1]

        success, mapinfo = buf.map(Gst.MapFlags.READ)
        if success:
            try:
                frame = Image.frombytes('RGB', (width, height), mapinfo.data)
                with self._frame_lock:
                    self._latest_frame = frame
            finally:
                buf.unmap(mapinfo)

        return Gst.FlowReturn.OK

    def grab(self) -> Optional[Image.Image]:
        """
        Grab the latest frame from the screen capture stream.

        Returns:
            PIL Image of current screen, or None if no frame available
        """
        if not self._running:
            return None

        # Pump the GLib main context to process pending events
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

        with self._frame_lock:
            return self._latest_frame.copy() if self._latest_frame else None

    def stop(self):
        """Stop the screen capture stream and clean up."""
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._sink = None
        self._latest_frame = None
        self._running = False
        self._pw_fd = None
        logger.info("PipeWire screen capture stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def has_frame(self) -> bool:
        with self._frame_lock:
            return self._latest_frame is not None


# Singleton
_capture_instance = None

def get_pipewire_capture() -> PipeWireCapture:
    global _capture_instance
    if _capture_instance is None:
        _capture_instance = PipeWireCapture()
    return _capture_instance
