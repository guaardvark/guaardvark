#!/usr/bin/env python3
"""
Code Tools
Executable tools for code analysis, generation, and file processing.
Wraps existing code intelligence services for agent system integration.
"""

import logging
import os
from typing import Any, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


class CodeGeneratorTool(BaseTool):
    """
    Complete file analysis and code generation tool.
    Converted from /codegen command rule (rule ID: 17).

    Reads, understands, and generates complete code files with precision.
    Preserves existing functionality while making requested modifications.
    """

    name = "codegen"
    description = "Analyze uploaded code files and generate complete, modified versions with requested changes"

    parameters = {
        "input_file": ToolParameter(
            name="input_file",
            type="string",
            required=False,
            description="Path to input file to analyze and modify (optional)",
            default="",
        ),
        "output_filename": ToolParameter(
            name="output_filename",
            type="string",
            required=True,
            description="Output filename for generated code",
        ),
        "instructions": ToolParameter(
            name="instructions",
            type="string",
            required=True,
            description="Modification instructions or code generation request",
        ),
        "language": ToolParameter(
            name="language",
            type="string",
            required=False,
            description="Programming language (auto-detected from extension if not specified)",
            default="auto",
        ),
        "preserve_structure": ToolParameter(
            name="preserve_structure",
            type="bool",
            required=False,
            description="Preserve original file structure and formatting",
            default=True,
        ),
    }

    LANGUAGE_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript-react",
        ".ts": "typescript",
        ".tsx": "typescript-react",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c-header",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".cs": "csharp",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".xml": "xml",
        ".sh": "bash",
        ".bash": "bash",
    }

    def __init__(self):
        super().__init__()
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from backend.utils.llm_service import get_default_llm

            self._llm = get_default_llm()
        return self._llm

    def _detect_language(self, filename: str) -> str:
        """Detect programming language from file extension"""
        ext = os.path.splitext(filename)[1].lower()
        return self.LANGUAGE_MAP.get(ext, "unknown")

    def _read_input_file(self, filepath: str) -> Optional[str]:
        """Read input file content if it exists"""
        if not filepath:
            return None

        # Try multiple possible locations
        possible_paths = [
            filepath,
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), filepath
            ),
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "uploads",
                filepath,
            ),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception as e:
                    logger.warning(f"Failed to read {path}: {e}")

        return None

    def execute(self, **kwargs) -> ToolResult:
        """Generate or modify code based on instructions"""
        input_file = kwargs.get("input_file", "")
        output_filename = kwargs.get("output_filename")
        instructions = kwargs.get("instructions")
        language = kwargs.get("language", "auto")
        preserve_structure = kwargs.get("preserve_structure", True)

        try:
            llm = self._get_llm()

            # Detect language
            if language == "auto":
                language = self._detect_language(output_filename)

            # Read input file if provided
            input_content = self._read_input_file(input_file) if input_file else None

            # Build the generation prompt
            if input_content:
                prompt = f"""You are CodeGen, an expert AI specialized in complete file analysis and generation.

CRITICAL INSTRUCTIONS:
1. Read EVERY character of the provided file completely
2. Generate a complete, identical file with the requested modifications
3. Preserve ALL existing functionality except specified changes
4. Never truncate or summarize - return the complete file
5. Maintain exact formatting, indentation, and structure
6. Include all imports, functions, classes, and dependencies

INPUT FILE ({input_file}):
```{language}
{input_content}
```

REQUESTED MODIFICATIONS:
{instructions}

REQUIREMENTS:
- Output the COMPLETE modified file
- Preserve existing code patterns and conventions
- Add requested features without breaking existing code
- Generate production-ready, clean code

OUTPUT THE COMPLETE FILE NOW (no explanations, just code):"""
            else:
                prompt = f"""You are CodeGen, an expert AI specialized in code generation.

TASK: Generate a complete {language} file

FILENAME: {output_filename}

REQUIREMENTS:
{instructions}

QUALITY STANDARDS:
- Generate clean, readable, maintainable code
- Follow {language} best practices
- Include proper error handling
- Add appropriate comments for complex logic
- Ensure the file is immediately usable

OUTPUT THE COMPLETE FILE NOW (no explanations, no markdown fences, just code):"""

            from backend.utils.llm_service import ChatMessage, MessageRole

            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            response = llm.chat(messages)

            if response.message:
                try:
                    code_content = str(response.message.content).strip()
                except (ValueError, AttributeError):
                    blocks = getattr(response.message, "blocks", [])
                    code_content = next(
                        (
                            getattr(b, "text", str(b))
                            for b in blocks
                            if getattr(b, "text", None)
                        ),
                        "",
                    )
                    code_content = code_content.strip()
            else:
                code_content = ""

            # Clean up markdown artifacts
            if code_content.startswith("```"):
                lines = code_content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                code_content = "\n".join(lines)

            # Save to disk — use project's configured OUTPUT_DIR
            from backend.config import OUTPUT_DIR

            output_dir = os.path.join(OUTPUT_DIR, "code")

            # Use context to determine output logic if available
            if self._context:
                # Example: If project_id is present, maybe save to a project-specific folder
                # For now, we just log it as a proof of concept
                project_id = self._context.get("project_id")
                if project_id:
                    logger.info(f"CodeGenerator using context: project_id={project_id}")

            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, output_filename)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(code_content)

            # Calculate some metrics
            line_count = len(code_content.split("\n"))
            char_count = len(code_content)

            return ToolResult(
                success=True,
                output={
                    "output_path": output_path,
                    "filename": output_filename,
                    "language": language,
                    "line_count": line_count,
                    "char_count": char_count,
                    "content_preview": (
                        code_content[:500] + "..."
                        if len(code_content) > 500
                        else code_content
                    ),
                },
                metadata={
                    "filename": output_filename,
                    "language": language,
                    "had_input_file": bool(input_content),
                    "lines": line_count,
                },
            )

        except Exception as e:
            logger.error(f"Code generation failed: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Code generation failed: {str(e)}")


class CodeAnalysisTool(BaseTool):
    """
    Analyze code files for structure, patterns, and potential improvements.
    """

    name = "analyze_code"
    description = "Analyze code files for structure, patterns, best practices, and potential improvements"

    parameters = {
        "file_path": ToolParameter(
            name="file_path",
            type="string",
            required=True,
            description="Path to the code file to analyze",
        ),
        "analysis_type": ToolParameter(
            name="analysis_type",
            type="string",
            required=False,
            description="Type of analysis: 'full', 'structure', 'security', 'performance', 'style'",
            default="full",
        ),
    }

    def __init__(self):
        super().__init__()
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from backend.utils.llm_service import get_default_llm

            self._llm = get_default_llm()
        return self._llm

    def _read_file(self, filepath: str) -> Optional[str]:
        """Read file content"""
        possible_paths = [
            filepath,
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), filepath
            ),
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "uploads",
                filepath,
            ),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception as e:
                    logger.warning(f"Failed to read {path}: {e}")

        return None

    def _extract_structure(self, content: str, language: str) -> str:
        """Extract code structure summary for large files"""
        lines = content.split("\n")
        structure_parts = []

        if language in ["python"]:
            import_lines = [
                l for l in lines[:50] if l.strip().startswith(("import ", "from "))
            ]
            class_lines = [
                (i + 1, l)
                for i, l in enumerate(lines)
                if l.strip().startswith("class ")
            ]
            func_lines = [
                (i + 1, l) for i, l in enumerate(lines) if l.strip().startswith("def ")
            ]
        else:
            import_lines = [
                l
                for l in lines[:50]
                if l.strip().startswith(
                    ("import ", "from ", "require(", "const ", "let ")
                )
                and ("require" in l or "import" in l)
            ]
            class_lines = [
                (i + 1, l)
                for i, l in enumerate(lines)
                if "class " in l and "{" in l or l.strip().startswith("class ")
            ]
            func_lines = [
                (i + 1, l)
                for i, l in enumerate(lines)
                if "function " in l or ("=>" in l and ("const " in l or "let " in l))
            ]

        if import_lines:
            structure_parts.append(
                f"Imports ({len(import_lines)}): {', '.join(import_lines[:5])}..."
            )
        if class_lines:
            structure_parts.append(
                f"Classes: {', '.join([l[1].strip()[:50] for l in class_lines[:5]])}"
            )
        if func_lines:
            structure_parts.append(
                f"Functions ({len(func_lines)}): lines {', '.join([str(l[0]) for l in func_lines[:10]])}"
            )

        return (
            "\n".join(structure_parts)
            if structure_parts
            else "Structure could not be extracted"
        )

    def execute(self, **kwargs) -> ToolResult:
        """Analyze code file"""
        file_path = kwargs.get("file_path")
        analysis_type = kwargs.get("analysis_type", "full")
        MAX_CONTENT_SIZE = 6000

        try:
            content = self._read_file(file_path)
            if not content:
                return ToolResult(
                    success=False, error=f"Could not read file: {file_path}"
                )

            llm = self._get_llm()
            original_size = len(content)
            line_count = len(content.split("\n"))

            ext = os.path.splitext(file_path)[1].lower()
            language = CodeGeneratorTool.LANGUAGE_MAP.get(ext, "unknown")

            analysis_prompts = {
                "full": "Provide a comprehensive analysis including structure, patterns, best practices, potential issues, and improvement suggestions.",
                "structure": "Analyze the file structure: imports, classes, functions, dependencies, and overall organization.",
                "security": "Perform a security review: identify potential vulnerabilities, injection risks, authentication issues, and security best practices.",
                "performance": "Analyze performance: identify bottlenecks, inefficient patterns, memory usage concerns, and optimization opportunities.",
                "style": "Review code style: naming conventions, formatting, documentation, readability, and adherence to language conventions.",
            }

            analysis_instruction = analysis_prompts.get(
                analysis_type, analysis_prompts["full"]
            )

            truncated = False
            structure_summary = ""
            if len(content) > MAX_CONTENT_SIZE:
                truncated = True
                structure_summary = self._extract_structure(content, language)
                first_portion = int(MAX_CONTENT_SIZE * 0.6)
                last_portion = int(MAX_CONTENT_SIZE * 0.3)
                content = (
                    content[:first_portion]
                    + f"\n\n... [TRUNCATED: {original_size - MAX_CONTENT_SIZE} chars omitted from middle] ...\n\n"
                    + content[-last_portion:]
                )

            prompt = f"""Analyze this {language} code file.

FILE: {file_path}
SIZE: {original_size} chars, {line_count} lines{' (TRUNCATED for analysis)' if truncated else ''}
{f'STRUCTURE SUMMARY: {structure_summary}' if structure_summary else ''}

```{language}
{content}
```

ANALYSIS REQUEST: {analysis_instruction}

Provide a structured analysis with clear sections and actionable insights."""

            from backend.utils.llm_service import ChatMessage, MessageRole

            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            response = llm.chat(messages)

            if response.message:
                try:
                    analysis = str(response.message.content).strip()
                except (ValueError, AttributeError):
                    blocks = getattr(response.message, "blocks", [])
                    analysis = next(
                        (
                            getattr(b, "text", str(b))
                            for b in blocks
                            if getattr(b, "text", None)
                        ),
                        "",
                    )
                    analysis = analysis.strip()
            else:
                analysis = ""

            return ToolResult(
                success=True,
                output={
                    "file": file_path,
                    "language": language,
                    "analysis_type": analysis_type,
                    "analysis": analysis,
                    "line_count": line_count,
                    "char_count": original_size,
                    "truncated": truncated,
                },
                metadata={
                    "file": file_path,
                    "language": language,
                    "analysis_type": analysis_type,
                    "truncated": truncated,
                },
            )

        except Exception as e:
            logger.error(f"Code analysis failed: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Code analysis failed: {str(e)}")
