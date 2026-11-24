# backend/services/task_handlers/code_operations_handler.py
# Handler for code analysis, generation, and self-improvement tasks
# Version 2.0 - Full implementation wrapping code_intelligence_api and llama_code_tools

import logging
import os
from pathlib import Path
import shutil
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List

from .base_handler import BaseTaskHandler, TaskResult, TaskResultStatus

logger = logging.getLogger(__name__)


class CodeOperationsHandler(BaseTaskHandler):
    """
    Handler for code analysis and generation operations.
    Wraps: code_intelligence_api.py, llama_code_tools.py
    """

    @property
    def handler_name(self) -> str:
        return "code_operations"

    @property
    def display_name(self) -> str:
        return "Code Operations"

    @property
    def process_type(self) -> str:
        return "code_analysis"

    @property
    def celery_queue(self) -> str:
        return "default"

    @property
    def default_priority(self) -> int:
        return 4  # Medium-high priority for code operations

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["operation"],
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["analyze", "generate", "edit", "refactor", "explain", "test_gen", "validate", "batch_analyze"],
                    "description": "Type of code operation"
                },
                "target_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths to operate on"
                },
                "code_content": {
                    "type": "string",
                    "description": "Code content for analysis/generation"
                },
                "instructions": {
                    "type": "string",
                    "description": "Natural language instructions for the operation"
                },
                "language": {
                    "type": "string",
                    "default": "python",
                    "description": "Programming language"
                },
                "output_path": {
                    "type": "string",
                    "description": "Output file path for generated code"
                },
                "model_name": {
                    "type": "string",
                    "default": "default",
                    "description": "LLM model for code operations"
                },
                "create_backup": {
                    "type": "boolean",
                    "default": True,
                    "description": "Create backup before editing files"
                },
                "bypass_rules": {
                    "type": "boolean",
                    "default": False,
                    "description": "Bypass rules system for code generation"
                },
                "refactor_type": {
                    "type": "string",
                    "enum": ["optimize", "readability", "maintainability", "security", "modernize"],
                    "default": "optimize",
                    "description": "Type of refactoring to apply"
                },
                "test_framework": {
                    "type": "string",
                    "enum": ["auto", "pytest", "jest", "unittest", "mocha"],
                    "default": "auto",
                    "description": "Test framework for test generation"
                }
            }
        }

    def execute(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable[[int, str, Optional[Dict[str, Any]]], None]
    ) -> TaskResult:
        """
        Execute code operations.
        Supports:
        - analyze: Code analysis for errors and improvements
        - generate: Generate code from description
        - edit: Edit existing code based on instructions
        - refactor: Refactor code for better quality
        - explain: Explain what code does
        - test_gen: Generate unit tests
        - validate: Validate code syntax
        - batch_analyze: Analyze multiple files
        """
        started_at = datetime.now()

        try:
            operation = config.get("operation", "analyze")

            operations = {
                "analyze": self._execute_analyze,
                "generate": self._execute_generate,
                "edit": self._execute_edit,
                "refactor": self._execute_refactor,
                "explain": self._execute_explain,
                "test_gen": self._execute_test_gen,
                "validate": self._execute_validate,
                "batch_analyze": self._execute_batch_analyze
            }

            handler = operations.get(operation)
            if not handler:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message=f"Unknown operation: {operation}",
                    error_message=f"operation must be one of: {', '.join(operations.keys())}",
                    started_at=started_at,
                    completed_at=datetime.now()
                )

            return handler(task, config, progress_callback, started_at)

        except Exception as e:
            logger.error(f"Code operations handler error: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Code operation failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _save_generated(self, output_path: str, reply: str, subject: str):
        """Write the code a model reply carries to ``output_path``.

        Returns (saved path or None, why it was not saved or None). The file
        gets the code, not the chat around it. Inside this install a new file
        may be created; an existing or protected one is never overwritten by
        a task.
        """
        from backend.services import inbound_guard_service as inbound_guard
        from backend.services.guarded_code_service import default_repo_root, protected_file_reason
        from backend.tools.code_tools import _extract_code
        from backend.utils.path_safety import is_sensitive

        code = _extract_code(reply, output_path)
        if not code.strip():
            return None, "the model returned no code"
        target = Path(output_path).expanduser().resolve()
        if is_sensitive(str(target)):
            return None, "credential and key files are not written by tasks"
        try:
            relative = str(target.relative_to(default_repo_root()))
        except ValueError:
            relative = None
        if relative is not None:
            if protected_file_reason(relative):
                return None, f"{relative} is a protected file"
            if target.exists():
                return None, (f"{relative} already exists in this install; a task does not overwrite it. "
                              "Choose a new file name, or ask for the change in chat.")
        try:
            inbound_guard.guard_file_write(str(target), code, source="code_task", subject=subject)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code if code.endswith("\n") else code + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save to {output_path}: {e}")
            return None, str(e)
        return str(target), None

    # A whole-file rewrite is only asked of a file the prompt can carry whole;
    # 8000 characters was the cut this handler already applied to its prompt.
    WHOLE_FILE_EDIT_CHARS = 8000

    def _get_chat_function(self):
        """Get the internal chat function for code operations"""
        try:
            from backend.api.code_intelligence_api import send_chat_message_internal
            return send_chat_message_internal
        except ImportError:
            logger.warning("code_intelligence_api not available")
            return None

    def _execute_analyze(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Analyze code for errors and improvements"""
        code_content = config.get("code_content", "")
        target_files = config.get("target_files", [])
        language = config.get("language", "python")
        bypass_rules = config.get("bypass_rules", False)

        if not code_content and not target_files:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No code content or files provided",
                started_at=started_at,
                completed_at=datetime.now()
            )

        progress_callback(0, "Starting code analysis...", None)

        # If files provided, read their content
        if target_files and not code_content:
            try:
                from backend.tools.llama_code_tools import read_code
                file_contents = []
                for filepath in target_files[:5]:  # Limit to 5 files
                    content = read_code(filepath)
                    if not content.startswith("ERROR"):
                        file_contents.append(f"=== {filepath} ===\n{content}")
                code_content = "\n\n".join(file_contents)
            except Exception as e:
                logger.warning(f"Could not read files: {e}")

        progress_callback(20, "Sending to LLM for analysis...", None)

        chat_func = self._get_chat_function()
        if not chat_func:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="LLM service not available",
                started_at=started_at,
                completed_at=datetime.now()
            )

        # Create analysis prompt
        prompt = f"""Please analyze this {language} code for errors, potential improvements, and suggestions:

```{language}
{code_content[:10000]}
```

Please provide:
1. Any syntax or logical errors
2. Performance improvements
3. Code quality suggestions
4. Security considerations
5. Best practice recommendations"""

        try:
            session_id = f"code-analysis-{task.id}-{datetime.now().timestamp()}"
            response = chat_func(
                session_id=session_id,
                user_message=prompt,
                project_id=None,
                use_rag=True,
                bypass_rules=bypass_rules
            )

            progress_callback(100, "Analysis complete", None)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            analysis_text = response.get("message", "") if response else "Analysis completed"

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message="Code analysis completed",
                output_data={
                    "analysis": analysis_text,
                    "language": language,
                    "files_analyzed": target_files
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Code analysis failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Analysis failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _execute_generate(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Generate code from description"""
        instructions = config.get("instructions", "")
        language = config.get("language", "python")
        output_path = config.get("output_path", "")
        bypass_rules = config.get("bypass_rules", False)

        if not instructions:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No instructions provided for code generation",
                started_at=started_at,
                completed_at=datetime.now()
            )

        progress_callback(0, "Starting code generation...", None)

        chat_func = self._get_chat_function()
        if not chat_func:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="LLM service not available",
                started_at=started_at,
                completed_at=datetime.now()
            )

        prompt = f"""Generate {language} code for the following requirement:

"{instructions}"

Please provide:
1. Complete, working code
2. Comments explaining the logic
3. Any necessary imports/dependencies
4. Usage examples if applicable

Return the code properly formatted for {language}."""

        progress_callback(30, "Generating code...", None)

        try:
            session_id = f"code-gen-{task.id}-{int(datetime.now().timestamp() * 1000)}"
            response = chat_func(
                session_id=session_id,
                user_message=prompt,
                project_id=None,
                use_rag=True,
                bypass_rules=bypass_rules
            )

            generated_code = response.get("message", "") if response else ""

            # Optionally save to file
            if not generated_code.strip():
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message="The model returned no code",
                    started_at=started_at,
                    completed_at=datetime.now()
                )

            output_files = []
            save_error = None
            if output_path:
                progress_callback(80, f"Saving to {output_path}...", None)
                saved, save_error = self._save_generated(
                    output_path, generated_code, f"generated code -> {output_path}")
                if saved:
                    output_files.append(saved)

            progress_callback(100, "Code generation complete", None)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            return TaskResult(
                status=TaskResultStatus.PARTIAL if save_error else TaskResultStatus.SUCCESS,
                message=(f"Code generated, but not saved to {output_path}: {save_error}"
                         if save_error else "Code generated successfully"),
                output_files=output_files,
                output_data={
                    "generated_code": generated_code,
                    "language": language,
                    "description": instructions,
                    "save_error": save_error,
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Code generation failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Generation failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _execute_edit(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Edit existing code based on instructions"""
        code_content = config.get("code_content", "")
        target_files = config.get("target_files", [])
        instructions = config.get("instructions", "")
        language = config.get("language", "python")
        create_backup = config.get("create_backup", True)
        bypass_rules = config.get("bypass_rules", False)

        if not instructions:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No edit instructions provided",
                started_at=started_at,
                completed_at=datetime.now()
            )

        progress_callback(0, "Starting code edit...", None)

        # Use llama_code_tools for file editing
        if target_files:
            return self._edit_files(task, target_files, instructions, language, create_backup, bypass_rules, progress_callback, started_at)

        # Edit code content directly
        if not code_content:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No code content or files provided",
                started_at=started_at,
                completed_at=datetime.now()
            )

        chat_func = self._get_chat_function()
        if not chat_func:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="LLM service not available",
                started_at=started_at,
                completed_at=datetime.now()
            )

        prompt = f"""Edit this {language} code according to the following instructions:

Instructions: {instructions}

Original code:
```{language}
{code_content[:10000]}
```

Please provide the complete modified code with requested changes applied."""

        progress_callback(30, "Processing edit instructions...", None)

        try:
            session_id = f"code-edit-{task.id}-{datetime.now().timestamp()}"
            response = chat_func(
                session_id=session_id,
                user_message=prompt,
                project_id=None,
                use_rag=True,
                bypass_rules=bypass_rules
            )

            edited_code = response.get("message", "") if response else ""

            progress_callback(100, "Edit complete", None)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message="Code edited successfully",
                output_data={
                    "edited_code": edited_code,
                    "original_code": code_content[:500] + "..." if len(code_content) > 500 else code_content,
                    "instructions": instructions,
                    "language": language
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Code edit failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Edit failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _edit_files(
        self,
        task: Any,
        target_files: List[str],
        instructions: str,
        language: str,
        create_backup: bool,
        bypass_rules: bool,
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Propose an edit to each file and stage it for review.

        Nothing is written here: each proposal becomes a pending fix that a
        person approves and applies (Self-improvement → Fixes), where it gets
        a restore point and its checks. The model returns the whole file, so
        only files that fit in the prompt whole are taken.
        """
        try:
            from backend.services.guarded_code_service import (
                GuardedCodeError, stage_pending_fix,
            )
            from backend.tools.code_tools import _extract_code
            from backend.tools.llama_code_tools import _read_source_file

            staged_files = []
            failed_files = []
            chat_func = self._get_chat_function()

            for i, filepath in enumerate(target_files):
                progress = int(10 + (i / len(target_files)) * 80)
                progress_callback(progress, f"Processing {filepath}...", None)

                try:
                    if not chat_func:
                        failed_files.append({"file": filepath, "error": "The chat model is not available"})
                        continue
                    current_content = _read_source_file(filepath, allow_external=False)["content"]
                    if len(current_content) > self.WHOLE_FILE_EDIT_CHARS:
                        failed_files.append({"file": filepath, "error": (
                            f"The file is {len(current_content)} characters; this task rewrites whole files "
                            f"up to {self.WHOLE_FILE_EDIT_CHARS}. Ask for the change in chat, which edits "
                            "the lines it names.")})
                        continue

                    session_id = f"file-edit-{task.id}-{i}-{datetime.now().timestamp()}"
                    prompt = f"""Edit this {language} code according to the instructions:

Instructions: {instructions}

File: {filepath}

Current code:
{current_content}

Provide the COMPLETE edited file content."""

                    response = chat_func(
                        session_id=session_id,
                        user_message=prompt,
                        project_id=None,
                        use_rag=True,
                        bypass_rules=bypass_rules
                    )

                    edited_code = _extract_code((response or {}).get("message", ""), filepath)
                    if not edited_code.strip():
                        failed_files.append({"file": filepath, "error": "The model returned no code"})
                        continue
                    if not edited_code.endswith("\n") and current_content.endswith("\n"):
                        edited_code += "\n"
                    if edited_code == current_content:
                        failed_files.append({"file": filepath, "error": "The model returned the file unchanged"})
                        continue
                    fix_id = stage_pending_fix(
                        filepath, current_content, edited_code,
                        f"Task {task.id}: {instructions}"[:500],
                    )
                    staged_files.append({"file": filepath, "edited": False, "staged": True,
                                         "pending_fix_id": fix_id})

                except GuardedCodeError as e:
                    failed_files.append({"file": filepath, "error": str(e)})
                except Exception as e:
                    failed_files.append({"file": filepath, "error": str(e)})

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, f"Proposed changes to {len(staged_files)} file(s)", None)

            status = TaskResultStatus.SUCCESS
            if failed_files:
                status = TaskResultStatus.PARTIAL if staged_files else TaskResultStatus.FAILED

            return TaskResult(
                status=status,
                message=(f"Proposed changes to {len(staged_files)}/{len(target_files)} file(s). Nothing is changed "
                         "until they are approved and applied under Self-improvement → Fixes."),
                output_data={
                    "staged_files": staged_files,
                    "edited_files": [],
                    "failed_files": failed_files,
                },
                items_processed=len(staged_files),
                items_total=len(target_files),
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except ImportError as e:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"The code tools are not available: {e}",
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _execute_refactor(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Refactor code for better quality"""
        code_content = config.get("code_content", "")
        language = config.get("language", "python")
        refactor_type = config.get("refactor_type", "optimize")
        bypass_rules = config.get("bypass_rules", False)

        if not code_content:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No code content provided",
                started_at=started_at,
                completed_at=datetime.now()
            )

        progress_callback(0, f"Starting {refactor_type} refactoring...", None)

        chat_func = self._get_chat_function()
        if not chat_func:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="LLM service not available",
                started_at=started_at,
                completed_at=datetime.now()
            )

        prompt = f"""Refactor this {language} code to {refactor_type} it:

Original code:
```{language}
{code_content[:10000]}
```

Please provide:
1. Refactored code with improvements
2. Explanation of changes made
3. Benefits of the refactoring

Focus on improving {refactor_type} while preserving functionality."""

        progress_callback(30, "Processing refactoring...", None)

        try:
            session_id = f"code-refactor-{task.id}-{datetime.now().timestamp()}"
            response = chat_func(
                session_id=session_id,
                user_message=prompt,
                project_id=None,
                use_rag=True,
                bypass_rules=bypass_rules
            )

            refactored_code = response.get("message", "") if response else ""

            progress_callback(100, "Refactoring complete", None)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Code refactored for {refactor_type}",
                output_data={
                    "refactored_code": refactored_code,
                    "refactor_type": refactor_type,
                    "language": language
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Refactoring failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Refactoring failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _execute_explain(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Explain what code does"""
        code_content = config.get("code_content", "")
        language = config.get("language", "python")
        bypass_rules = config.get("bypass_rules", False)

        if not code_content:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No code content provided",
                started_at=started_at,
                completed_at=datetime.now()
            )

        progress_callback(0, "Analyzing code...", None)

        chat_func = self._get_chat_function()
        if not chat_func:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="LLM service not available",
                started_at=started_at,
                completed_at=datetime.now()
            )

        prompt = f"""Please explain this {language} code in detail:

```{language}
{code_content[:10000]}
```

Please provide:
1. Overall purpose and functionality
2. Step-by-step explanation of what the code does
3. Key concepts and patterns used
4. Input/output description
5. Any notable implementation details"""

        progress_callback(30, "Generating explanation...", None)

        try:
            session_id = f"code-explain-{task.id}-{datetime.now().timestamp()}"
            response = chat_func(
                session_id=session_id,
                user_message=prompt,
                project_id=None,
                use_rag=True,
                bypass_rules=bypass_rules
            )

            explanation = response.get("message", "") if response else ""

            progress_callback(100, "Explanation complete", None)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message="Code explanation generated",
                output_data={
                    "explanation": explanation,
                    "language": language
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Explanation failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Explanation failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _execute_test_gen(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Generate unit tests for code"""
        code_content = config.get("code_content", "")
        language = config.get("language", "python")
        test_framework = config.get("test_framework", "auto")
        output_path = config.get("output_path", "")
        bypass_rules = config.get("bypass_rules", False)

        if not code_content:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No code content provided",
                started_at=started_at,
                completed_at=datetime.now()
            )

        # Determine appropriate framework
        frameworks = {
            'python': 'pytest',
            'javascript': 'Jest',
            'typescript': 'Jest',
            'java': 'JUnit',
        }
        framework = frameworks.get(language.lower(), "appropriate testing framework") if test_framework == "auto" else test_framework

        progress_callback(0, f"Generating {framework} tests...", None)

        chat_func = self._get_chat_function()
        if not chat_func:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="LLM service not available",
                started_at=started_at,
                completed_at=datetime.now()
            )

        prompt = f"""Generate unit tests for this {language} code using {framework}:

```{language}
{code_content[:10000]}
```

Please provide:
1. Complete test file with comprehensive test cases
2. Tests for normal cases, edge cases, and error conditions
3. Appropriate test setup and teardown
4. Clear test descriptions and assertions"""

        progress_callback(30, "Generating test cases...", None)

        try:
            session_id = f"test-gen-{task.id}-{datetime.now().timestamp()}"
            response = chat_func(
                session_id=session_id,
                user_message=prompt,
                project_id=None,
                use_rag=True,
                bypass_rules=bypass_rules
            )

            test_code = response.get("message", "") if response else ""

            # Save tests to file if path provided
            output_files = []
            save_error = None
            if output_path and test_code:
                progress_callback(80, f"Saving tests to {output_path}...", None)
                saved, save_error = self._save_generated(
                    output_path, test_code, f"generated tests -> {output_path}")
                if saved:
                    output_files.append(saved)

            progress_callback(100, "Test generation complete", None)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            return TaskResult(
                status=TaskResultStatus.PARTIAL if save_error else TaskResultStatus.SUCCESS,
                message=(f"Generated {framework} tests, but not saved to {output_path}: {save_error}"
                         if save_error else f"Generated {framework} tests"),
                output_files=output_files,
                output_data={
                    "save_error": save_error,
                    "test_code": test_code,
                    "framework": framework,
                    "language": language
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Test generation failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Test generation failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _execute_validate(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Validate code syntax"""
        code_content = config.get("code_content", "")
        language = config.get("language", "python")
        bypass_rules = config.get("bypass_rules", False)

        if not code_content:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No code content provided",
                started_at=started_at,
                completed_at=datetime.now()
            )

        progress_callback(0, "Validating code...", None)

        errors = []
        warnings = []

        # Python syntax validation
        if language.lower() == "python":
            try:
                compile(code_content, "<string>", "exec")
                progress_callback(50, "Syntax valid, checking for issues...", None)
            except SyntaxError as e:
                errors.append({
                    "line": e.lineno,
                    "message": str(e.msg),
                    "severity": "error"
                })

        # LLM-based validation for more issues
        chat_func = self._get_chat_function()
        if chat_func:
            prompt = f"""Validate this {language} code for errors:

```{language}
{code_content[:8000]}
```

Identify:
1. Syntax errors with line numbers
2. Logical errors and potential bugs
3. Missing imports or dependencies
4. Type errors (if applicable)"""

            try:
                session_id = f"code-validate-{task.id}-{datetime.now().timestamp()}"
                response = chat_func(
                    session_id=session_id,
                    user_message=prompt,
                    project_id=None,
                    use_rag=False,
                    bypass_rules=bypass_rules
                )
                validation_text = response.get("message", "") if response else ""
            except Exception as e:
                validation_text = f"LLM validation unavailable: {e}"
        else:
            validation_text = "Basic validation complete"

        progress_callback(100, "Validation complete", None)

        completed_at = datetime.now()
        duration = (completed_at - started_at).total_seconds()

        return TaskResult(
            status=TaskResultStatus.SUCCESS if not errors else TaskResultStatus.PARTIAL,
            message=f"Validation complete: {len(errors)} errors found",
            output_data={
                "validation": validation_text,
                "errors": errors,
                "warnings": warnings,
                "language": language
            },
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration
        )

    def _execute_batch_analyze(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        """Analyze multiple files"""
        target_files = config.get("target_files", [])
        language = config.get("language", "python")
        bypass_rules = config.get("bypass_rules", False)

        if not target_files:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No target files provided",
                started_at=started_at,
                completed_at=datetime.now()
            )

        progress_callback(0, f"Analyzing {len(target_files)} files...", None)

        results = []
        failed = []

        try:
            from backend.tools.llama_code_tools import read_code
        except ImportError:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="llama_code_tools not available",
                started_at=started_at,
                completed_at=datetime.now()
            )

        for i, filepath in enumerate(target_files):
            progress = int(10 + (i / len(target_files)) * 85)
            progress_callback(progress, f"Analyzing {filepath}...", None)

            try:
                content = read_code(filepath)
                if content.startswith("ERROR"):
                    failed.append({"file": filepath, "error": content})
                    continue

                # Quick analysis without full LLM call for each file
                lines = content.count('\n')
                results.append({
                    "file": filepath,
                    "lines": lines,
                    "analyzed": True
                })

            except Exception as e:
                failed.append({"file": filepath, "error": str(e)})

        completed_at = datetime.now()
        duration = (completed_at - started_at).total_seconds()

        progress_callback(100, f"Analyzed {len(results)} files", None)

        status = TaskResultStatus.SUCCESS
        if failed:
            status = TaskResultStatus.PARTIAL if results else TaskResultStatus.FAILED

        return TaskResult(
            status=status,
            message=f"Analyzed {len(results)}/{len(target_files)} files",
            output_data={
                "analyzed_files": results,
                "failed_files": failed,
                "language": language
            },
            items_processed=len(results),
            items_total=len(target_files),
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration
        )

    def get_estimated_duration(self, config: Dict[str, Any]) -> Optional[int]:
        """Estimate based on operation type"""
        operation = config.get("operation", "analyze")
        target_files = config.get("target_files", [])

        base_times = {
            "analyze": 30,
            "generate": 45,
            "edit": 40,
            "refactor": 60,
            "explain": 30,
            "test_gen": 60,
            "validate": 15,
            "batch_analyze": 20 * len(target_files) if target_files else 60
        }

        return base_times.get(operation, 30)

    def can_retry(self, task: Any, error: Exception) -> bool:
        """Check if task can be retried"""
        error_msg = str(error).lower()
        # Don't retry on file permission or path errors
        if "permission" in error_msg or "not found" in error_msg:
            return False
        return super().can_retry(task, error)
