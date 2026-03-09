"""Execute dynamically generated Python code with optional auto-correction."""

import logging
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from backend.utils import llm_service, prompt_templates

logger = logging.getLogger(__name__)


def execute_generated_code(
    python_code: str, original_requirements: str, max_retries: int = 2
):
    """Execute LLM generated Python code with automatic correction attempts."""
    if not python_code:
        return {"success": False, "error": "No code provided"}

    for attempt in range(max_retries + 1):
        logger.info("Executing generated code (attempt %d)...", attempt + 1)
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "script.py"
            script_path.write_text(python_code)
            try:
                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                return {
                    "success": True,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            except Exception as e:
                logger.error("Execution attempt %d failed: %s", attempt + 1, e)
                if attempt >= max_retries:
                    return {
                        "success": False,
                        "error": f"Final execution failed after {max_retries + 1} attempts: {e}",
                    }

                error_traceback = traceback.format_exc()
                correction_prompt = prompt_templates.CODE_CORRECTION_TEMPLATE.format(
                    user_requirements=original_requirements,
                    failed_code=python_code,
                    error_traceback=error_traceback,
                )
                logger.info("Attempting auto-correction...")
                corrected_response = llm_service.run_llm_code_prompt(correction_prompt)
                new_code = llm_service.extract_python_code(corrected_response)
                if not new_code:
                    return {
                        "success": False,
                        "error": "Auto-correction failed to generate new code.",
                    }
                python_code = new_code
                logger.info("Retrying with corrected code...")
