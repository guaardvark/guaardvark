# backend/api/code_execution_api.py
# Code execution API for CodeEditorPage

import os
import subprocess
import tempfile
import json
import logging
import time
import shlex
import hashlib
from collections import defaultdict
from pathlib import Path
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

code_exec_bp = Blueprint("code_execution", __name__, url_prefix="/api/code-execution")

# Security settings
MAX_EXECUTION_TIME = 30  # seconds
MAX_OUTPUT_SIZE = 1024 * 1024  # 1MB
ALLOWED_LANGUAGES = ['python', 'javascript', 'shell', 'bash']
RATE_LIMIT_MAX = 30  # requests per window
RATE_LIMIT_WINDOW = 60  # seconds

# Rate limiting state
_rate_limits = defaultdict(list)


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

def execute_command(command, timeout=MAX_EXECUTION_TIME, cwd=None, use_shell=False):
    """Execute a command safely with timeout

    Args:
        command: Command to execute (string if use_shell=True, list otherwise)
        timeout: Execution timeout in seconds
        cwd: Working directory
        use_shell: If True, execute command through shell (DANGEROUS - only for trusted input)
    """
    try:
        start_time = time.time()

        result = subprocess.run(
            command,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=os.environ.copy()
        )

        execution_time = time.time() - start_time

        # Limit output size
        stdout = result.stdout[:MAX_OUTPUT_SIZE] if result.stdout else ""
        stderr = result.stderr[:MAX_OUTPUT_SIZE] if result.stderr else ""

        return {
            "success": result.returncode == 0,
            "output": stdout + stderr if stderr else stdout,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": result.returncode,
            "executionTime": execution_time
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": f"Execution timed out after {timeout} seconds",
            "stdout": "",
            "stderr": f"Execution timed out after {timeout} seconds",
            "exitCode": -1,
            "executionTime": timeout
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

def execute_command_with_stdin(command, timeout=MAX_EXECUTION_TIME, stdin_data="", cwd=None):
    """Execute a command with stdin data provided

    Args:
        command: Command to execute as list
        timeout: Execution timeout in seconds
        stdin_data: Data to pass to stdin
        cwd: Working directory
    """
    try:
        start_time = time.time()

        result = subprocess.run(
            command,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=os.environ.copy()
        )

        execution_time = time.time() - start_time

        # Limit output size
        stdout = result.stdout[:MAX_OUTPUT_SIZE] if result.stdout else ""
        stderr = result.stderr[:MAX_OUTPUT_SIZE] if result.stderr else ""

        return {
            "success": result.returncode == 0,
            "output": stdout + stderr if stderr else stdout,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": result.returncode,
            "executionTime": execution_time
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": f"Execution timed out after {timeout} seconds",
            "stdout": "",
            "stderr": f"Execution timed out after {timeout} seconds",
            "exitCode": -1,
            "executionTime": timeout
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
        working_directory = data.get('workingDirectory', '/tmp')

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

@code_exec_bp.route("/format", methods=["POST"])
def format_code():
    """Format code using appropriate formatter"""
    try:
        data = request.get_json()
        if not data or 'code' not in data or 'language' not in data:
            return jsonify({"error": "code and language are required"}), 400

        code = data['code']
        language = data['language']
        options = data.get('options', {})

        # Create temporary file
        extensions = {
            'python': '.py',
            'javascript': '.js',
            'typescript': '.ts',
            'html': '.html',
            'css': '.css',
            'json': '.json'
        }

        ext = extensions.get(language, '.txt')
        temp_file = create_temp_file(code, ext)

        try:
            formatted_code = code
            changes = []

            if language == 'python':
                # Use black for Python formatting
                command = ['black', '--line-length', str(options.get('maxLineLength', 80)), temp_file]
                result = execute_command(command, 10)
                if result['success']:
                    with open(temp_file, 'r') as f:
                        formatted_code = f.read()
                    changes = ["Applied Black formatting"]

            elif language in ['javascript', 'typescript']:
                # Use prettier for JS/TS formatting
                command = ['prettier', '--write', temp_file]
                result = execute_command(command, 10)
                if result['success']:
                    with open(temp_file, 'r') as f:
                        formatted_code = f.read()
                    changes = ["Applied Prettier formatting"]

            elif language == 'html':
                # Use prettier for HTML formatting
                command = ['prettier', '--write', '--parser', 'html', temp_file]
                result = execute_command(command, 10)
                if result['success']:
                    with open(temp_file, 'r') as f:
                        formatted_code = f.read()
                    changes = ["Applied Prettier formatting"]

            elif language == 'css':
                # Use prettier for CSS formatting
                command = ['prettier', '--write', '--parser', 'css', temp_file]
                result = execute_command(command, 10)
                if result['success']:
                    with open(temp_file, 'r') as f:
                        formatted_code = f.read()
                    changes = ["Applied Prettier formatting"]

            elif language == 'json':
                # Use prettier for JSON formatting
                command = ['prettier', '--write', '--parser', 'json', temp_file]
                result = execute_command(command, 10)
                if result['success']:
                    with open(temp_file, 'r') as f:
                        formatted_code = f.read()
                    changes = ["Applied Prettier formatting"]

            # Cleanup
            cleanup_temp_file(temp_file)

            return jsonify({
                "success": True,
                "formattedCode": formatted_code,
                "originalCode": code,
                "language": language,
                "changes": changes
            })

        except Exception as e:
            cleanup_temp_file(temp_file)
            raise e

    except Exception as e:
        logger.error(f"Error formatting code: {e}")
        return jsonify({"error": str(e)}), 500

@code_exec_bp.route("/lint", methods=["POST"])
def lint_code():
    """Lint code for errors and warnings"""
    try:
        data = request.get_json()
        if not data or 'code' not in data or 'language' not in data:
            return jsonify({"error": "code and language are required"}), 400

        code = data['code']
        language = data['language']
        options = data.get('options', {})

        # Create temporary file
        extensions = {
            'python': '.py',
            'javascript': '.js',
            'typescript': '.ts',
            'html': '.html',
            'css': '.css'
        }

        ext = extensions.get(language, '.txt')
        temp_file = create_temp_file(code, ext)

        try:
            errors = []
            warnings = []
            suggestions = []
            score = 100

            if language == 'python':
                # Use flake8 for Python linting — pass as list, no shell
                command = ['flake8', temp_file, '--format=json']
                result = execute_command(command, 10)
                if result['success'] or result['stderr']:
                    try:
                        if result['stderr']:
                            # Parse flake8 output
                            lines = result['stderr'].strip().split('\n')
                            for line in lines:
                                if ':' in line:
                                    parts = line.split(':')
                                    if len(parts) >= 4:
                                        line_num = parts[1]
                                        col_num = parts[2]
                                        error_code = parts[3].split()[0]
                                        message = ':'.join(parts[3:]).strip()

                                        if error_code.startswith('E'):
                                            errors.append({
                                                "line": int(line_num),
                                                "column": int(col_num),
                                                "code": error_code,
                                                "message": message
                                            })
                                        elif error_code.startswith('W'):
                                            warnings.append({
                                                "line": int(line_num),
                                                "column": int(col_num),
                                                "code": error_code,
                                                "message": message
                                            })
                    except Exception as e:
                        logger.warning(f"Failed to parse flake8 output: {e}")

            elif language in ['javascript', 'typescript']:
                # Use eslint for JS/TS linting — pass as list, no shell
                command = ['eslint', temp_file, '--format=json']
                result = execute_command(command, 10)
                if result['stdout']:
                    try:
                        eslint_output = json.loads(result['stdout'])
                        for file_result in eslint_output:
                            for message in file_result.get('messages', []):
                                if message['severity'] == 2:  # Error
                                    errors.append({
                                        "line": message['line'],
                                        "column": message['column'],
                                        "code": message['ruleId'],
                                        "message": message['message']
                                    })
                                elif message['severity'] == 1:  # Warning
                                    warnings.append({
                                        "line": message['line'],
                                        "column": message['column'],
                                        "code": message['ruleId'],
                                        "message": message['message']
                                    })
                    except Exception as e:
                        logger.warning(f"Failed to parse eslint output: {e}")

            # Calculate score
            score = max(0, 100 - len(errors) * 10 - len(warnings) * 5)

            # Cleanup
            cleanup_temp_file(temp_file)

            return jsonify({
                "success": True,
                "errors": errors,
                "warnings": warnings,
                "suggestions": suggestions,
                "score": score,
                "language": language
            })

        except Exception as e:
            cleanup_temp_file(temp_file)
            raise e

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

        project_path = data['projectPath']
        options = data.get('options', {})

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
