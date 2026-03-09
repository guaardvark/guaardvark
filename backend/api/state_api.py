
import json
import logging
import os

from flask import Blueprint, current_app, jsonify, request

try:
    from backend.config import GUAARDVARK_ROOT
except ImportError:
    import sys

    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    from config import GUAARDVARK_ROOT

state_bp = Blueprint("state_api", __name__, url_prefix="/api/state")

from backend.config import STORAGE_DIR
LAYOUT_FILE = os.path.join(STORAGE_DIR, "dashboard_layout.json")
DASHBOARD_STATE_FILE = os.path.join(STORAGE_DIR, "dashboard_state.json")
FOLDER_STATE_FILE = os.path.join(STORAGE_DIR, "folder_state.json")
CODE_EDITOR_STATE_FILE = os.path.join(STORAGE_DIR, "code_editor_state.json")
CODE_EDITOR_SESSION_FILE = os.path.join(STORAGE_DIR, "code_editor_session.json")
DOCUMENTS_WINDOWS_STATE_FILE = os.path.join(STORAGE_DIR, "documents_windows_v2_state.json")
IMAGES_WINDOWS_STATE_FILE = os.path.join(STORAGE_DIR, "images_windows_state.json")
LAYOUT_DIR = os.path.dirname(LAYOUT_FILE)


@state_bp.route("/layout", methods=["GET"])
def get_layout():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received GET /api/state/layout request")
    try:
        if os.path.exists(LAYOUT_FILE):
            with open(LAYOUT_FILE, "r", encoding="utf-8") as f:
                layout = json.load(f)
            logger.info(f"API: Found layout file, returning {len(layout)} items.")
            return jsonify(layout), 200
        else:
            logger.warning(
                f"API: Layout file not found at {LAYOUT_FILE}. Returning 404."
            )
            return jsonify([]), 200
    except json.JSONDecodeError as e:
        logger.error(
            f"API Error (GET /layout): Error decoding JSON from {LAYOUT_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify({"error": "Failed to decode layout file.", "details": str(e)}),
            500,
        )
    except Exception as e:
        logger.error(
            f"API Error (GET /layout): Error reading layout file {LAYOUT_FILE}: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Failed to read layout file.", "details": str(e)}), 500


@state_bp.route("/layout", methods=["POST"])
def save_layout():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/state/layout request")

    if not request.is_json:
        logger.warning("API Error (POST /layout): Request body not JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        layout_data = request.get_json().get("layout")
        if not isinstance(layout_data, list):
            logger.warning(
                f"API Error (POST /layout): Invalid layout format. Expected list, got {type(layout_data)}."
            )
            return (
                jsonify(
                    {
                        "error": "Invalid layout format. 'layout' key must contain a list."
                    }
                ),
                400,
            )

        try:
            os.makedirs(LAYOUT_DIR, exist_ok=True)
        except OSError as e:
            logger.error(
                f"API Error (POST /layout): Could not create directory {LAYOUT_DIR}: {e}",
                exc_info=True,
            )
            return (
                jsonify(
                    {
                        "error": "Could not ensure layout directory exists.",
                        "details": str(e),
                    }
                ),
                500,
            )

        with open(LAYOUT_FILE, "w", encoding="utf-8") as f:
            json.dump(layout_data, f, indent=2)

        logger.info(f"API: Layout saved successfully to {LAYOUT_FILE}.")
        return jsonify({"message": "Layout saved."}), 200
    except Exception as e:
        logger.error(
            f"API Error (POST /layout): Error saving layout file {LAYOUT_FILE}: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Failed to save layout file.", "details": str(e)}), 500


@state_bp.route("/dashboard", methods=["GET"])
def get_dashboard_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received GET /api/state/dashboard request")
    try:
        if os.path.exists(DASHBOARD_STATE_FILE):
            with open(DASHBOARD_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            logger.info(f"API: Found dashboard state file, returning state.")
            return jsonify(state), 200
        else:
            logger.warning(
                f"API: Dashboard state file not found at {DASHBOARD_STATE_FILE}. Returning 404."
            )
            return jsonify({"error": "Dashboard state not found."}), 404
    except json.JSONDecodeError as e:
        logger.error(
            f"API Error (GET /dashboard): Error decoding JSON from {DASHBOARD_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to decode dashboard state file.", "details": str(e)}
            ),
            500,
        )
    except Exception as e:
        logger.error(
            f"API Error (GET /dashboard): Error reading dashboard state file {DASHBOARD_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to read dashboard state file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/dashboard", methods=["POST"])
def save_dashboard_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/state/dashboard request")

    if not request.is_json:
        logger.warning("API Error (POST /dashboard): Request body not JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        dashboard_state = request.get_json()
        if not isinstance(dashboard_state, dict):
            logger.warning(
                f"API Error (POST /dashboard): Invalid state format. Expected dict, got {type(dashboard_state)}."
            )
            return (
                jsonify(
                    {
                        "error": "Invalid state format. Request body must be a JSON object."
                    }
                ),
                400,
            )

        required_fields = ["layout", "cardColors"]
        for field in required_fields:
            if field not in dashboard_state:
                logger.warning(
                    f"API Error (POST /dashboard): Missing required field '{field}'."
                )
                return (
                    jsonify({"error": f"Missing required field: {field}"}),
                    400,
                )

        try:
            os.makedirs(LAYOUT_DIR, exist_ok=True)
        except OSError as e:
            logger.error(
                f"API Error (POST /dashboard): Could not create directory {LAYOUT_DIR}: {e}",
                exc_info=True,
            )
            return (
                jsonify(
                    {
                        "error": "Could not ensure state directory exists.",
                        "details": str(e),
                    }
                ),
                500,
            )

        with open(DASHBOARD_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(dashboard_state, f, indent=2)

        logger.info(
            f"API: Dashboard state saved successfully to {DASHBOARD_STATE_FILE}."
        )
        return jsonify({"message": "Dashboard state saved."}), 200
    except Exception as e:
        logger.error(
            f"API Error (POST /dashboard): Error saving dashboard state file {DASHBOARD_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to save dashboard state file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/folders", methods=["GET"])
def get_folder_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received GET /api/state/folders request")
    try:
        if os.path.exists(FOLDER_STATE_FILE):
            with open(FOLDER_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            logger.info(f"API: Found folder state file, returning state.")
            return jsonify(state), 200
        else:
            logger.warning(
                f"API: Folder state file not found at {FOLDER_STATE_FILE}. Returning 404."
            )
            return jsonify({"error": "Folder state not found."}), 404
    except json.JSONDecodeError as e:
        logger.error(
            f"API Error (GET /folders): Error decoding JSON from {FOLDER_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to decode folder state file.", "details": str(e)}
            ),
            500,
        )
    except Exception as e:
        logger.error(
            f"API Error (GET /folders): Error reading folder state file {FOLDER_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to read folder state file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/folders", methods=["POST"])
def save_folder_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/state/folders request")

    if not request.is_json:
        logger.warning("API Error (POST /folders): Request body not JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        folder_state = request.get_json()
        if not isinstance(folder_state, dict):
            logger.warning(
                f"API Error (POST /folders): Invalid state format. Expected dict, got {type(folder_state)}."
            )
            return (
                jsonify(
                    {
                        "error": "Invalid state format. Request body must be a JSON object."
                    }
                ),
                400,
            )

        required_fields = ["folderLayouts", "folderColors"]
        for field in required_fields:
            if field not in folder_state:
                logger.warning(
                    f"API Error (POST /folders): Missing required field '{field}'."
                )
                return (
                    jsonify({"error": f"Missing required field: {field}"}),
                    400,
                )

        try:
            os.makedirs(LAYOUT_DIR, exist_ok=True)
        except OSError as e:
            logger.error(
                f"API Error (POST /folders): Could not create directory {LAYOUT_DIR}: {e}",
                exc_info=True,
            )
            return (
                jsonify(
                    {
                        "error": "Could not ensure state directory exists.",
                        "details": str(e),
                    }
                ),
                500,
            )

        with open(FOLDER_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(folder_state, f, indent=2)

        logger.info(
            f"API: Folder state saved successfully to {FOLDER_STATE_FILE}."
        )
        return jsonify({"message": "Folder state saved."}), 200
    except Exception as e:
        logger.error(
            f"API Error (POST /folders): Error saving folder state file {FOLDER_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to save folder state file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/documents-windows-v2", methods=["GET"])
def get_documents_windows_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received GET /api/state/documents-windows-v2 request")
    try:
        if os.path.exists(DOCUMENTS_WINDOWS_STATE_FILE):
            with open(DOCUMENTS_WINDOWS_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            logger.info("API: Found documents windows state file, returning state.")
            return jsonify(state), 200
        else:
            logger.warning(
                f"API: Documents windows state file not found at {DOCUMENTS_WINDOWS_STATE_FILE}. Returning 404."
            )
            return jsonify({"error": "Documents windows state not found."}), 404
    except json.JSONDecodeError as e:
        logger.error(
            f"API Error (GET /documents-windows-v2): Error decoding JSON from {DOCUMENTS_WINDOWS_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to decode documents windows state file.", "details": str(e)}
            ),
            500,
        )
    except Exception as e:
        logger.error(
            f"API Error (GET /documents-windows-v2): Error reading documents windows state file {DOCUMENTS_WINDOWS_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to read documents windows state file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/documents-windows-v2", methods=["POST"])
def save_documents_windows_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/state/documents-windows-v2 request")

    if not request.is_json:
        logger.warning("API Error (POST /documents-windows-v2): Request body not JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        state_data = request.get_json()
        if not isinstance(state_data, dict):
            logger.warning(
                f"API Error (POST /documents-windows-v2): Invalid state format. Expected dict, got {type(state_data)}."
            )
            return (
                jsonify(
                    {
                        "error": "Invalid state format. Request body must be a JSON object."
                    }
                ),
                400,
            )

        try:
            os.makedirs(LAYOUT_DIR, exist_ok=True)
        except OSError as e:
            logger.error(
                f"API Error (POST /documents-windows-v2): Could not create directory {LAYOUT_DIR}: {e}",
                exc_info=True,
            )
            return (
                jsonify(
                    {
                        "error": "Could not ensure state directory exists.",
                        "details": str(e),
                    }
                ),
                500,
            )

        with open(DOCUMENTS_WINDOWS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)

        logger.info(
            f"API: Documents windows state saved successfully to {DOCUMENTS_WINDOWS_STATE_FILE}."
        )
        return jsonify({"message": "Documents windows state saved."}), 200
    except Exception as e:
        logger.error(
            f"API Error (POST /documents-windows-v2): Error saving documents windows state file {DOCUMENTS_WINDOWS_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to save documents windows state file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/images-windows", methods=["GET"])
def get_images_windows_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received GET /api/state/images-windows request")
    try:
        if os.path.exists(IMAGES_WINDOWS_STATE_FILE):
            with open(IMAGES_WINDOWS_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            return jsonify(state), 200
        else:
            return jsonify({"error": "Images windows state not found."}), 404
    except json.JSONDecodeError as e:
        logger.error(
            f"API Error (GET /images-windows): Error decoding JSON: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Failed to decode images windows state.", "details": str(e)}), 500
    except Exception as e:
        logger.error(
            f"API Error (GET /images-windows): Error reading state file: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Failed to read images windows state.", "details": str(e)}), 500


@state_bp.route("/images-windows", methods=["POST"])
def save_images_windows_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/state/images-windows request")

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        state_data = request.get_json()
        if not isinstance(state_data, dict):
            return jsonify({"error": "Invalid state format. Request body must be a JSON object."}), 400

        os.makedirs(LAYOUT_DIR, exist_ok=True)

        with open(IMAGES_WINDOWS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)

        return jsonify({"message": "Images windows state saved."}), 200
    except Exception as e:
        logger.error(
            f"API Error (POST /images-windows): Error saving state: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Failed to save images windows state.", "details": str(e)}), 500


@state_bp.route("/code-editor", methods=["GET"])
def get_code_editor_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received GET /api/state/code-editor request")
    try:
        if os.path.exists(CODE_EDITOR_STATE_FILE):
            with open(CODE_EDITOR_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            logger.info(f"API: Found code editor state file, returning state.")
            return jsonify(state), 200
        else:
            logger.warning(
                f"API: Code editor state file not found at {CODE_EDITOR_STATE_FILE}. Returning 404."
            )
            return jsonify({"error": "Code editor state not found"}), 404

    except json.JSONDecodeError as e:
        logger.error(
            f"API Error (GET /code-editor): Invalid JSON in code editor state file: {e}",
            exc_info=True,
        )
        return (
            jsonify({"error": "Invalid JSON in code editor state file", "details": str(e)}),
            500,
        )
    except Exception as e:
        logger.error(
            f"API Error (GET /code-editor): Error reading code editor state file {CODE_EDITOR_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to read code editor state file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/code-editor", methods=["POST"])
def save_code_editor_state():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/state/code-editor request")

    if not request.is_json:
        logger.warning("API Error (POST /code-editor): Request body not JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        state_data = request.get_json()
        logger.info(f"API: Saving code editor state with keys: {list(state_data.keys())}")

        os.makedirs(LAYOUT_DIR, exist_ok=True)

        with open(CODE_EDITOR_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2, ensure_ascii=False)

        logger.info(f"API: Successfully saved code editor state to {CODE_EDITOR_STATE_FILE}")
        return jsonify({"success": True, "message": "Code editor state saved successfully"}), 200

    except Exception as e:
        logger.error(
            f"API Error (POST /code-editor): Error saving code editor state file {CODE_EDITOR_STATE_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to save code editor state file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/code-editor/session", methods=["GET"])
def get_code_editor_session():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received GET /api/state/code-editor/session request")
    try:
        if os.path.exists(CODE_EDITOR_SESSION_FILE):
            with open(CODE_EDITOR_SESSION_FILE, "r", encoding="utf-8") as f:
                session = json.load(f)
            logger.info(f"API: Found code editor session file, returning session data.")
            return jsonify(session), 200
        else:
            logger.warning(
                f"API: Code editor session file not found at {CODE_EDITOR_SESSION_FILE}. Returning 404."
            )
            return jsonify({"error": "Code editor session not found"}), 404

    except json.JSONDecodeError as e:
        logger.error(
            f"API Error (GET /code-editor/session): Invalid JSON in session file: {e}",
            exc_info=True,
        )
        return (
            jsonify({"error": "Invalid JSON in code editor session file", "details": str(e)}),
            500,
        )
    except Exception as e:
        logger.error(
            f"API Error (GET /code-editor/session): Error reading session file {CODE_EDITOR_SESSION_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to read code editor session file.", "details": str(e)}
            ),
            500,
        )


@state_bp.route("/code-editor/session", methods=["POST"])
def save_code_editor_session():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/state/code-editor/session request")

    if not request.is_json:
        logger.warning("API Error (POST /code-editor/session): Request body not JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        session_data = request.get_json()
        logger.info(f"API: Saving code editor session with keys: {list(session_data.keys())}")

        expected_keys = ["openTabs", "activeTabIndex", "chatMessages", "fileTree", "searchResults"]
        for key in expected_keys:
            if key not in session_data:
                logger.warning(f"API: Missing expected key '{key}' in session data")

        os.makedirs(LAYOUT_DIR, exist_ok=True)

        session_data["lastSaved"] = json.dumps(
            {"timestamp": "now"}, default=str
        ).replace('"now"', f'"{logger.handlers[0].formatter.formatTime(logger.makeRecord("", 0, "", 0, "", (), None)) if logger.handlers else "unknown"}"')

        import datetime
        session_data["lastSaved"] = datetime.datetime.now().isoformat()

        with open(CODE_EDITOR_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)

        logger.info(f"API: Successfully saved code editor session to {CODE_EDITOR_SESSION_FILE}")
        return jsonify({"success": True, "message": "Code editor session saved successfully"}), 200

    except Exception as e:
        logger.error(
            f"API Error (POST /code-editor/session): Error saving session file {CODE_EDITOR_SESSION_FILE}: {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "Failed to save code editor session file.", "details": str(e)}
            ),
            500,
        )
