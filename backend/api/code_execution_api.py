# backend/api/code_execution_api.py
# Code execution API for CodeEditorPage

import contextlib
import os
import subprocess
import tempfile
import json
import logging
import time
import shlex
import shutil
import re
import hashlib
import signal
import threading
from collections import defaultdict
from pathlib import Path
from flask import Blueprint, request, jsonify
from backend.utils.path_guard import PathEscapesRoot, contained, contained_path
from backend.services.guarded_code_service import default_repo_root

logger = logging.getLogger(__name__)

code_exec_bp = Blueprint("code_execution", __name__, url_prefix="/api/code-execution")

# Security settings
MAX_EXECUTION_TIME = 30  # seconds
MAX_OUTPUT_SIZE = 1024 * 1024  # 1MB
_TRUNCATED_NOTE = "\n[output cut at 1 MB]\n"
ALLOWED_LANGUAGES = ['python', 'javascript', 'shell', 'bash']
RATE_LIMIT_MAX = 30  # requests per window
RATE_LIMIT_WINDOW = 60  # seconds

# Rate limiting state
_rate_limits = defaultdict(list)

# The environment code run from the editor gets: these variables, the LC_*,
# CUDA_* and NVIDIA_* families, and PYTHONPATH when every entry is inside this
# install (the checkout or its virtualenv). Nothing else of the backend's
# environment, which carries the .env secrets (the API key, database, broker
# and result-backend URLs with their passwords, hub, bot and proxy
# credentials), reaches the child.
#
# This is not a boundary. The code runs as the same user as the backend and
# can read .env, or /proc/<pid>/environ of the backend, if it sets out to. It
# only stops casual leakage: a script that prints os.environ, or a library
# that dumps it in a traceback, no longer shows the secrets in the output.
ENV_PASSTHROUGH = (
    "PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR",
    "VIRTUAL_ENV", "LD_LIBRARY_PATH", "NODE_PATH",
)
ENV_PASSTHROUGH_PREFIXES = ("LC_", "CUDA_", "NVIDIA_")
INSTALL_ROOT = Path(__file__).resolve().parents[2]


def _pythonpath_is_this_installs(value: str) -> bool:
    """True when every entry of a PYTHONPATH is inside the checkout or the
    virtualenv the backend runs in, so passing it on points the child at this
    install's own code and nothing else."""
    roots = [INSTALL_ROOT]
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        roots.append(Path(venv).resolve())
    for entry in value.split(os.pathsep):
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except OSError:
            return False
        if not any(resolved == root or root in resolved.parents for root in roots):
            return False
    return True


def execution_env() -> dict:
    """The environment for code the editor runs (see ENV_PASSTHROUGH)."""
    env = {
        name: value for name, value in os.environ.items()
        if name in ENV_PASSTHROUGH or name.startswith(ENV_PASSTHROUGH_PREFIXES)
    }
    pythonpath = os.environ.get("PYTHONPATH", "")
    if pythonpath and _pythonpath_is_this_installs(pythonpath):
        env["PYTHONPATH"] = pythonpath
    return env


def _check_rate_limit():
    """Enforce per-IP rate limiting on execution endpoints. Returns error response or None."""
    ip = request.remote_addr
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    # Prune old entries
    _rate_limits[ip] = [t for t in _rate_limits[ip] if t > cutoff]

    if len(_rate_limits[ip]) >= RATE_LIMIT_MAX:
        logger.warning(f"Rate limit exceeded for {ip} on {request.path}")
        return jsonify({"error": f"Rate limit exceeded ({RATE_LIMIT_MAX} requests per {RATE_LIMIT_WINDOW}s)"}), 429

    _rate_limits[ip].append(now)
    return None


def _audit_log(language, code_or_cmd):
    """Log execution request for audit trail."""
    code_hash = hashlib.sha256(code_or_cmd.encode(errors='replace')).hexdigest()[:16]
    logger.info(
        f"[CODE_EXEC] ip={request.remote_addr} lang={language} "
        f"len={len(code_or_cmd)} hash={code_hash}"
    )


def create_temp_file(content, extension):
    """Create a temporary file with the given content"""
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix=extension, delete=False)
    temp_file.write(content)
    temp_file.close()
    return temp_file.name

def cleanup_temp_file(file_path):
    """Clean up temporary file"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning(f"Failed to cleanup temp file {file_path}: {e}")

@contextlib.contextmanager
def _working_directory(cwd):
    """`cwd` as given, or a fresh temporary folder for the duration."""
    if cwd:
        yield cwd
        return
    with tempfile.TemporaryDirectory(prefix="guaardvark-run-") as scratch:
        yield scratch


def _kill_group(proc):
    """Stop the process and everything it started (it leads its own session)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _drain(stream, sink, limit):
    """Read a pipe to its end, keeping the first `limit` characters. Reading
    on past the limit keeps the process from blocking on a full pipe without
    holding its whole output in memory."""
    kept = 0
    try:
        for chunk in iter(lambda: stream.read(8192), ""):
            if kept < limit:
                sink.append(chunk[: limit - kept])
                kept += len(sink[-1])
                if kept >= limit:
                    sink.append(_TRUNCATED_NOTE)
    except (OSError, ValueError):
        pass


def _run(command, timeout, cwd, use_shell=False, stdin_data=None):
    start_time = time.time()
    try:
        with _working_directory(cwd) as run_cwd:
            proc = subprocess.Popen(
                command,
                shell=use_shell,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                cwd=run_cwd,
                env=execution_env(),
                start_new_session=True,
            )
            out, err = [], []
            readers = [
                threading.Thread(target=_drain, args=(proc.stdout, out, MAX_OUTPUT_SIZE), daemon=True),
                threading.Thread(target=_drain, args=(proc.stderr, err, MAX_OUTPUT_SIZE), daemon=True),
            ]
            for reader in readers:
                reader.start()
            if stdin_data is not None:
                def _feed():
                    try:
                        proc.stdin.write(stdin_data)
                        proc.stdin.close()
                    except (OSError, ValueError):
                        pass
                threading.Thread(target=_feed, daemon=True).start()

            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            # A child left running would keep the pipes open and outlive the
            # run; the program itself has finished or run out of time.
            for reader in readers:
                reader.join(timeout=0 if timed_out else 1)
            _kill_group(proc)
            proc.wait()
            for reader in readers:
                reader.join(timeout=2)

        stdout, stderr = "".join(out), "".join(err)
        if timed_out:
            note = f"Execution timed out after {timeout} seconds"
            stderr = f"{stderr}\n{note}".strip()
            return {
                "success": False,
                "output": f"{stdout}\n{stderr}".strip(),
                "stdout": stdout,
                "stderr": stderr,
                "exitCode": -1,
                "timedOut": True,
                "executionTime": timeout,
            }
        return {
            "success": proc.returncode == 0,
            "output": stdout + stderr if stderr else stdout,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": proc.returncode,
            "executionTime": time.time() - start_time,
        }
    except Exception as e:
        return {
            "success": False,
            "output": f"Execution failed: {str(e)}",
            "stdout": "",
            "stderr": f"Execution failed: {str(e)}",
            "exitCode": -1,
            "executionTime": 0
        }


def execute_command(command, timeout=MAX_EXECUTION_TIME, cwd=None, use_shell=False):
    """Run a command with a time limit and bounded output.

    Args:
        command: Command to execute (string if use_shell=True, list otherwise)
        timeout: Execution timeout in seconds; on expiry the command and every
            process it started are stopped, and what it printed is returned
        cwd: Working directory; None runs in a fresh temporary folder that
            is removed afterwards, so nothing starts in the backend's folder
        use_shell: If True, execute command through shell (DANGEROUS - only for trusted input)
    """
    return _run(command, timeout, cwd, use_shell=use_shell)


def execute_command_with_stdin(command, timeout=MAX_EXECUTION_TIME, stdin_data="", cwd=None):
    """`execute_command` for a command list, with `stdin_data` on its stdin."""
    return _run(command, timeout, cwd, stdin_data=stdin_data or "")


@code_exec_bp.route("/python", methods=["POST"])
def execute_python():
    """Execute Python code"""
    rate_err = _check_rate_limit()
    if rate_err:
        return rate_err

    try:
        data = request.get_json()
        if not data or 'code' not in data:
            return jsonify({"error": "code is required"}), 400

        code = data['code']
        timeout = min(data.get('timeout', MAX_EXECUTION_TIME), MAX_EXECUTION_TIME)
        input_data = data.get('input', '')

        _audit_log('python', code)

        # Create temporary file
        temp_file = create_temp_file(code, '.py')
        input_file = None

        try:
            # Prepare command - use list to avoid shell injection
            if input_data:
                # Write input to a file
                input_file = create_temp_file(input_data, '.txt')
                # Read input file and pass to stdin
                with open(input_file, 'r') as f:
                    input_content = f.read()
                # Execute without shell, providing stdin directly
                command = ['python3', temp_file]
                result = execute_command_with_stdin(command, timeout, input_content)
            else:
                command = ['python3', temp_file]
                result = execute_command(command, timeout)

            # Cleanup
            cleanup_temp_file(temp_file)
            if input_file:
                cleanup_temp_file(input_file)

            return jsonify(result)

        except Exception as e:
            cleanup_temp_file(temp_file)
            if input_file:
                cleanup_temp_file(input_file)
            raise e

    except Exception as e:
        logger.error(f"Error executing Python code: {e}")
        return jsonify({"error": str(e)}), 500

@code_exec_bp.route("/javascript", methods=["POST"])
def execute_javascript():
    """Execute JavaScript code"""
    rate_err = _check_rate_limit()
    if rate_err:
        return rate_err

    try:
        data = request.get_json()
        if not data or 'code' not in data:
            return jsonify({"error": "code is required"}), 400

        code = data['code']
        timeout = min(data.get('timeout', MAX_EXECUTION_TIME), MAX_EXECUTION_TIME)
        input_data = data.get('input', '')

        _audit_log('javascript', code)

        # Create temporary file
        temp_file = create_temp_file(code, '.js')
        input_file = None

        try:
            # Prepare command - use list to avoid shell injection
            if input_data:
                # Write input to a file
                input_file = create_temp_file(input_data, '.txt')
                # Read input file and pass to stdin
                with open(input_file, 'r') as f:
                    input_content = f.read()
                # Execute without shell, providing stdin directly
                command = ['node', temp_file]
                result = execute_command_with_stdin(command, timeout, input_content)
            else:
                command = ['node', temp_file]
                result = execute_command(command, timeout)

            # Cleanup
            cleanup_temp_file(temp_file)
            if input_file:
                cleanup_temp_file(input_file)

            return jsonify(result)

        except Exception as e:
            cleanup_temp_file(temp_file)
            if input_file:
                cleanup_temp_file(input_file)
            raise e

    except Exception as e:
        logger.error(f"Error executing JavaScript code: {e}")
        return jsonify({"error": str(e)}), 500

@code_exec_bp.route("/shell", methods=["POST"])
def execute_shell():
    """Execute shell command (without shell=True for safety)"""
    rate_err = _check_rate_limit()
    if rate_err:
        return rate_err

    try:
        data = request.get_json()
        if not data or 'command' not in data:
            return jsonify({"error": "command is required"}), 400

        command = data['command']
        timeout = min(data.get('timeout', MAX_EXECUTION_TIME), MAX_EXECUTION_TIME)
        # Unset: a fresh temporary folder (execute_command).
        working_directory = data.get('workingDirectory') or None

        # Security check - block dangerous commands (defense-in-depth)
        dangerous_commands = ['rm -rf', 'sudo', 'su', 'chmod 777', 'dd if=', 'mkfs', 'fdisk',
                             'format', '>', '>>', '|', '&', ';', '$(', '`']
        if any(dangerous in command.lower() for dangerous in dangerous_commands):
            logger.warning(f"[CODE_EXEC] Blocked dangerous shell command from {request.remote_addr}: {command}")
            return jsonify({"error": "Command not allowed for security reasons"}), 403

        _audit_log('shell', command)

        # Parse command into list to avoid shell injection (no shell=True)
        try:
            cmd_list = shlex.split(command)
        except ValueError as e:
            return jsonify({"error": f"Invalid command syntax: {e}"}), 400

        if not cmd_list:
            return jsonify({"error": "Empty command"}), 400

        # Execute without shell=True
        result = execute_command(cmd_list, timeout, working_directory, use_shell=False)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error executing shell command: {e}")
        return jsonify({"error": str(e)}), 500

FORMATTERS = {
    # language: (tool on PATH, argv builder, note shown when the tool is missing)
    'python': ('black', lambda path, opts: ['black', '--quiet', '--line-length', str(opts.get('maxLineLength', 88)), path]),
    'javascript': ('prettier', lambda path, opts: ['prettier', '--write', path]),
    'typescript': ('prettier', lambda path, opts: ['prettier', '--write', path]),
    'html': ('prettier', lambda path, opts: ['prettier', '--write', '--parser', 'html', path]),
    'css': ('prettier', lambda path, opts: ['prettier', '--write', '--parser', 'css', path]),
}

INSTALL_HINTS = {
    'black': 'pip install black',
    'prettier': 'npm install -g prettier',
    'flake8': 'pip install flake8',
    'eslint': 'npm install -g eslint',
}


def _tool_missing(tool, action):
    """A missing formatter or linter is a failure the user can act on, never a silent no-op."""
    return jsonify({
        "success": False,
        "error": f"{action} needs {tool}, which is not installed on this machine ({INSTALL_HINTS.get(tool, 'install it and retry')}).",
        "tool": tool,
    }), 501


@code_exec_bp.route("/format", methods=["POST"])
def format_code():
    """Format code with a formatter that is actually present.

    JSON is formatted with the standard library. Other languages need their
    formatter on PATH (black, prettier); when it is missing the response is a
    501 with the install hint instead of the unchanged code marked "formatted".
    """
    try:
        data = request.get_json()
        if not data or 'code' not in data or 'language' not in data:
            return jsonify({"error": "code and language are required"}), 400

        code = data['code']
        language = data['language']
        options = data.get('options', {}) or {}

        if language == 'json':
            try:
                indent = options.get('indentSize', 2)
                formatted_code = json.dumps(json.loads(code), indent=indent, ensure_ascii=False) + "\n"
            except json.JSONDecodeError as exc:
                return jsonify({"success": False, "error": f"Not valid JSON: {exc.msg} (line {exc.lineno})"}), 422
            return jsonify({
                "success": True,
                "formattedCode": formatted_code,
                "originalCode": code,
                "language": language,
                "tool": "json",
                "changes": [] if formatted_code == code else ["Reindented with the json module"],
            })

        spec = FORMATTERS.get(language)
        if not spec:
            return jsonify({"success": False, "error": f"No formatter for {language} here."}), 501
        tool, build_argv = spec
        if not shutil.which(tool):
            return _tool_missing(tool, "Format")

        extensions = {'python': '.py', 'javascript': '.js', 'typescript': '.ts', 'html': '.html', 'css': '.css'}
        temp_file = create_temp_file(code, extensions.get(language, '.txt'))
        try:
            result = execute_command(build_argv(temp_file, options), 10)
            if not result['success']:
                return jsonify({
                    "success": False,
                    "error": (result['stderr'] or result['output'] or f"{tool} exited with {result['exitCode']}").strip(),
                    "tool": tool,
                }), 422
            with open(temp_file, 'r') as f:
                formatted_code = f.read()
        finally:
            cleanup_temp_file(temp_file)

        return jsonify({
            "success": True,
            "formattedCode": formatted_code,
            "originalCode": code,
            "language": language,
            "tool": tool,
            "changes": [] if formatted_code == code else [f"Applied {tool} formatting"],
        })

    except Exception as e:
        logger.error(f"Error formatting code: {e}")
        return jsonify({"error": str(e)}), 500


_FLAKE8_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+): (?P<code>[A-Z]+\d+) (?P<msg>.*)$")


def parse_flake8_output(stdout):
    """flake8's default format is `path:line:col: CODE message`, one per line, on stdout."""
    errors, warnings = [], []
    for raw in (stdout or "").splitlines():
        match = _FLAKE8_LINE.match(raw.strip())
        if not match:
            continue
        issue = {
            "line": int(match.group('line')),
            "column": int(match.group('col')),
            "code": match.group('code'),
            "message": match.group('msg').strip(),
        }
        (errors if match.group('code')[0] in 'EF' else warnings).append(issue)
    return errors, warnings


def parse_eslint_output(stdout):
    """eslint --format=json prints one entry per file with its messages."""
    errors, warnings = [], []
    for file_result in json.loads(stdout):
        for message in file_result.get('messages', []):
            issue = {
                "line": message.get('line', 1),
                "column": message.get('column', 1),
                "code": message.get('ruleId') or 'eslint',
                "message": message.get('message', ''),
            }
            (errors if message.get('severity') == 2 else warnings).append(issue)
    return errors, warnings


@code_exec_bp.route("/lint", methods=["POST"])
def lint_code():
    """Lint code, saying which tool did it.

    flake8 (Python) or eslint (JavaScript/TypeScript) are used when they are on
    PATH. Otherwise the response falls back to the real syntax check in
    backend.services.code_syntax_check and says so in `tool` and `note`; when
    not even that can parse the language it is a 501, never a clean report.
    """
    try:
        data = request.get_json()
        if not data or 'code' not in data or 'language' not in data:
            return jsonify({"error": "code and language are required"}), 400

        code = data['code']
        language = data['language']

        errors, warnings, tool, note = [], [], None, ""
        linter = {'python': 'flake8', 'javascript': 'eslint', 'typescript': 'eslint'}.get(language)
        if linter and shutil.which(linter):
            extensions = {'python': '.py', 'javascript': '.js', 'typescript': '.ts'}
            temp_file = create_temp_file(code, extensions[language])
            try:
                if linter == 'flake8':
                    result = execute_command(['flake8', '--max-line-length', '120', temp_file], 10)
                    # flake8 exits 1 when it found something; only an empty stdout with a non-zero exit is a tool failure.
                    if not result['success'] and not result['stdout'].strip():
                        return jsonify({"success": False, "error": (result['stderr'] or 'flake8 failed').strip(), "tool": linter}), 422
                    errors, warnings = parse_flake8_output(result['stdout'])
                else:
                    result = execute_command(['eslint', '--format=json', '--no-color', temp_file], 10)
                    try:
                        errors, warnings = parse_eslint_output(result['stdout'])
                    except (ValueError, TypeError, AttributeError):
                        # exit 2 and no JSON: eslint could not run (usually no config for a lone file).
                        return jsonify({"success": False, "error": (result['stderr'] or result['stdout'] or 'eslint failed').strip(), "tool": linter}), 422
                tool = linter
            finally:
                cleanup_temp_file(temp_file)
        else:
            from backend.services.code_syntax_check import check_syntax
            checked = check_syntax(code, language)
            if not checked['supported']:
                hint = f" ({INSTALL_HINTS[linter]} for a full lint)" if linter else ""
                return jsonify({"success": False, "error": checked['note'] + hint, "tool": None}), 501
            errors, warnings, tool = checked['errors'], checked['warnings'], checked['tool']
            note = f"Syntax check only; install {linter} for a full lint." if linter else "Syntax check only."

        score = max(0, 100 - len(errors) * 10 - len(warnings) * 5)
        return jsonify({
            "success": True,
            "errors": errors,
            "warnings": warnings,
            "suggestions": [],
            "score": score,
            "language": language,
            "tool": tool,
            "note": note,
        })

    except Exception as e:
        logger.error(f"Error linting code: {e}")
        return jsonify({"error": str(e)}), 500

@code_exec_bp.route("/build", methods=["POST"])
def build_project():
    """Build project"""
    rate_err = _check_rate_limit()
    if rate_err:
        return rate_err

    try:
        data = request.get_json()
        if not data or 'projectPath' not in data:
            return jsonify({"error": "projectPath is required"}), 400

        options = data.get('options', {})

        # Builds run inside the configured repository root only.
        try:
            project_path = contained_path(default_repo_root(), data['projectPath'])
        except PathEscapesRoot:
            return jsonify({"error": "projectPath must be inside the configured repository root"}), 403

        if not os.path.exists(project_path):
            return jsonify({"error": "Project path not found"}), 404

        _audit_log('build', project_path)

        # Determine build command based on project type
        build_command = None

        # Check for package.json (Node.js project)
        if os.path.exists(os.path.join(project_path, 'package.json')):
            if options.get('clean'):
                # Run clean then build as separate commands
                clean_result = execute_command(['npm', 'run', 'clean'], 60, project_path)
                if not clean_result['success']:
                    return jsonify({
                        "success": False,
                        "output": clean_result['output'],
                        "error": clean_result['stderr'],
                        "buildTime": clean_result['executionTime'],
                        "artifacts": [],
                        "exitCode": clean_result['exitCode']
                    })
            build_command = ['npm', 'run', 'build']

        # Check for requirements.txt (Python project)
        elif os.path.exists(os.path.join(project_path, 'requirements.txt')):
            build_command = ['python', '-m', 'pip', 'install', '-r', 'requirements.txt']

        # Check for Makefile
        elif os.path.exists(os.path.join(project_path, 'Makefile')):
            build_command = ['make']

        # Check for Cargo.toml (Rust project)
        elif os.path.exists(os.path.join(project_path, 'Cargo.toml')):
            build_command = ['cargo', 'build']

        if not build_command:
            return jsonify({"error": "No build system detected"}), 400

        # Execute build command as list (no shell=True)
        start_time = time.time()
        result = execute_command(build_command, 300, project_path)  # 5 minute timeout for builds
        build_time = time.time() - start_time

        # Find build artifacts
        artifacts = []
        if result['success']:
            # Look for common build output directories
            output_dirs = ['dist', 'build', 'out', 'target', 'bin']
            for output_dir in output_dirs:
                output_path = os.path.join(project_path, output_dir)
                if os.path.exists(output_path):
                    for root, dirs, files in os.walk(output_path):
                        for file in files:
                            artifacts.append(os.path.join(root, file))

        return jsonify({
            "success": result['success'],
            "output": result['output'],
            "error": result['stderr'] if not result['success'] else "",
            "buildTime": build_time,
            "artifacts": artifacts,
            "exitCode": result['exitCode']
        })

    except Exception as e:
        logger.error(f"Error building project: {e}")
        return jsonify({"error": str(e)}), 500
