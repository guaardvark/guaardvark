#!/usr/bin/env python3
"""
Code Tools
Executable tools for code analysis, generation, and file processing.
Wraps existing code intelligence services for agent system integration.
"""

import json
import logging
import os
import re
import warnings
from typing import Any, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.tools.generation_tools import FileGeneratorTool
from backend.utils.backend_http import is_mcp_transport
from backend.utils.display_paths import display_path, display_text

logger = logging.getLogger(__name__)

# Largest input file the code tools read: the ceiling read_repo_file puts on a
# checkout file, applied to uploads as well.
MAX_INPUT_BYTES = 10 * 1024 * 1024
# Characters per token assumed when sizing a prompt against the model's context
# window. Measured with the cl100k_base tokenizer on this repository's twelve
# largest source files: 3.6 to 5.3. 3.5 keeps the estimate on the high side, so
# a file that passes the check fits.
PROMPT_CHARS_PER_TOKEN = 3.5

# A Markdown fence line: up to three spaces, three or more backticks or tildes,
# then an optional info string such as "python". Only a bare one closes a block.
_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^`\n]*?)[ \t]*$")
# Output that is itself Markdown: fences inside it are part of the file.
_MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdx"}
_MARKDOWN_TAGS = {"", "markdown", "md", "mdx"}
# A lead-in such as "Here is the complete file:" is at most this many lines.
_LEAD_IN_MAX_LINES = 2
# Fence tags that name an output type, besides its extension and the name
# LANGUAGE_MAP gives it (".py" already accepts "py" and "python").
_FENCE_TAG_ALIASES = {
    ".js": ("node", "nodejs"),
    ".jsx": ("javascript", "js", "react"),
    ".tsx": ("typescript", "ts", "react"),
    ".sh": ("sh", "shell", "zsh"),
    ".bash": ("sh", "shell"),
    ".yaml": ("yml",),
    ".h": ("c", "cpp", "c++"),
    ".cpp": ("c++", "cxx", "cc"),
    ".go": ("golang",),
    ".cs": ("c#",),
    ".html": ("htm",),
}


def _syntax_ok(filename: str, text: str) -> Optional[bool]:
    """Whether text parses as the language filename's extension names: True or
    False for Python and JSON, None for languages with no parser here. The text
    is compiled, never run."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".py":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                compile(text, filename, "exec", dont_inherit=True)
            return True
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return False
    if ext == ".json":
        try:
            json.loads(text)
            return True
        except (ValueError, RecursionError):
            return False
    return None


def _fence_tag(info: str) -> str:
    """The language a fence's info string names, lower-cased: "python" for
    "Python title=app.py", "" for a bare fence."""
    m = re.match(r"[\s{.]*([\w+#-]*)", info or "")
    return m.group(1).lower() if m else ""


def _names_output_type(tag: str, filename: str) -> bool:
    """True when a fence tag names the language of the output file: its
    extension, its LANGUAGE_MAP name or an alias, with or without a version
    ("python3")."""
    ext = os.path.splitext(filename)[1].lower()
    if not tag or not ext:
        return False
    accepted = {ext[1:], CodeGeneratorTool.LANGUAGE_MAP.get(ext, ext[1:]), *_FENCE_TAG_ALIASES.get(ext, ())}
    return tag in accepted or tag.rstrip("0123456789") in accepted


def _fenced_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """(opening line, closing line, tag) of the top-level fenced blocks.

    Inside a block, a fence with an info string opens a nested block and the
    next bare fence closes that one, so a fenced example in a docstring does not
    end the block around it. A block the reply never closes runs to the end: its
    closing index is len(lines)."""
    blocks: list[tuple[int, int, str]] = []
    opened: Optional[tuple[int, str, int, str]] = None  # line, fence character, fence length, tag
    depth = 0
    for i, line in enumerate(lines):
        m = _FENCE_LINE.match(line)
        if not m:
            continue
        marker, info = m.group(1), m.group(2)
        if opened is None:
            opened, depth = (i, marker[0], len(marker), _fence_tag(info)), 0
        elif marker[0] != opened[1]:
            continue
        elif info:
            depth += 1
        elif depth:
            depth -= 1
        elif len(marker) >= opened[2]:
            blocks.append((opened[0], i, opened[3]))
            opened = None
    if opened is not None:
        blocks.append((opened[0], len(lines), opened[3]))
    return blocks


def _file_block(tagged_bodies: list[tuple[str, str]], filename: str) -> str:
    """Which of a reply's fenced blocks is the file, given (tag, body) pairs in
    reply order.

    A block tagged with the output file's language comes first; then a block
    with no tag; a block tagged as another language (the "bash" block that shows
    how to run the file) only when there is nothing else. Among equals the
    first one wins, so of two blocks in the file's language the earlier is the
    file; for Python and JSON the first that parses is preferred."""
    bodies = [(tag, body) for tag, body in tagged_bodies if body.strip()]
    tiers = (
        [body for tag, body in bodies if _names_output_type(tag, filename)],
        [body for tag, body in bodies if not tag],
        [body for tag, body in bodies if tag and not _names_output_type(tag, filename)],
    )
    for tier in tiers:
        if tier:
            return next((body for body in tier if _syntax_ok(filename, body)), tier[0])
    return ""


def _extract_code(reply: str, filename: str) -> str:
    """The file a model reply carries, without the chat formatting around it.

    A reply with no fence is the file. So is one that already parses as the
    target language (Python, JSON): its fences are content, such as a docstring
    example. Otherwise the reply is read as chat, code in fences with
    commentary around them, when one of these holds: it starts with a fence; the
    text before the first fence is a short lead-in ending in a colon; or the
    longest fenced block is at least as long as everything outside the blocks.
    The file is then the block _file_block picks, never the usage example that
    follows or precedes it. Anything else is returned unchanged: a script whose
    heredoc holds a fenced example is a file, not chat.

    Markdown output keeps its fences; only a fence wrapped around the whole
    reply is removed. Fences with nothing in them give "", which the caller
    reports as a reply with no code."""
    text = (reply or "").strip()
    lines = text.split("\n")
    blocks = _fenced_blocks(lines)
    if not blocks or _syntax_ok(filename, text):
        return text

    first_open, last_close = blocks[0][0], blocks[-1][1]
    wrapped = first_open == 0 and last_close == len(lines) - 1
    span = "\n".join(lines[first_open + 1:last_close])
    if os.path.splitext(filename)[1].lower() in _MARKDOWN_EXTENSIONS:
        return span.strip("\n") if wrapped and blocks[0][2] in _MARKDOWN_TAGS else text

    tagged_bodies = [(tag, "\n".join(lines[a + 1:b])) for a, b, tag in blocks]
    chosen = _file_block(tagged_bodies, filename)
    if not chosen:
        return ""
    if len(blocks) == 1 and wrapped:
        return chosen.strip("\n")

    inside = {i for a, b, _tag in blocks for i in range(a, b + 1)}
    outside = "\n".join(line for i, line in enumerate(lines) if i not in inside).strip()
    lead_in = [line.strip() for line in lines[:first_open] if line.strip()]
    longest = max(len(body) for _tag, body in tagged_bodies)
    is_chat = (
        first_open == 0
        or (len(lead_in) <= _LEAD_IN_MAX_LINES and lead_in[-1].endswith(":"))
        or longest >= len(outside)
    )
    if not is_chat:
        return text

    # One block that holds bare fences of its own (a Markdown string in the
    # file) is read as several. When the pick does not parse and everything from
    # the first fence to the last does, that whole stretch is the file.
    if not _syntax_ok(filename, chosen) and _syntax_ok(filename, span):
        return span.strip("\n")
    return chosen.strip("\n")


def _active_llm():
    """An Ollama client for the chat model that is active now. Built on every
    call: the MCP server process outlives a model switch in Settings, and a
    client kept from the first call would go on asking for the old model."""
    from backend.utils.llm_service import get_default_llm
    return get_default_llm()


def _estimated_tokens(text: str) -> int:
    return int(len(text) / PROMPT_CHARS_PER_TOKEN) + 1


def _context_window(llm) -> int:
    """The context window, in tokens, the model call will run with (Ollama's
    num_ctx); 0 when the client does not say."""
    try:
        from backend.utils.ollama_resource_manager import refresh_context_window
        return int(refresh_context_window(llm) or 0)
    except Exception:
        return 0


def _too_large(filepath: str) -> str:
    return f"'{filepath}' is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB, which is more than the code tools read"


def _confine_candidates(paths):
    """With Settings → Agents → "Project folder only" on, keep only input files
    inside the project, upload/data folders and GUAARDVARK_ALLOWED_PATHS, and
    never credential files. Off: the paths are returned unchanged."""
    from backend.utils.settings_utils import get_confine_tool_paths

    if not get_confine_tool_paths():
        return paths
    from backend import config
    from backend.utils.path_safety import is_sensitive, is_within

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    allowed = [root, getattr(config, "UPLOAD_DIR", ""), getattr(config, "STORAGE_DIR", "")]
    allowed += list(getattr(config, "ALLOWED_AUTOMATION_PATHS", []))
    return [p for p in paths if is_within(p, allowed, base=root) and not is_sensitive(p)]


def _read_code_input(tool: BaseTool, filepath: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Text of a file a code tool was pointed at, as (content, error, resolved path).

    Tried in order: a path inside this checkout, or in chat an absolute path the
    user named, under read_code's rules (.env, credential and key files, excluded
    folders and git-ignored data other than uploads and outputs are refused); then
    a path inside UPLOAD_DIR; then, for a bare file name in chat, the upload's
    Document row. Over MCP nothing outside the checkout and the uploads is read.
    """
    from backend.services.guarded_code_service import GuardedCodeError
    from backend.tools.llama_code_tools import _read_source_file
    from backend.utils.backend_http import is_mcp_transport
    from backend.utils.path_safety import is_sensitive, is_within, safe_join

    try:
        file_data = _read_source_file(filepath, allow_external=not is_mcp_transport(tool))
        if file_data["scope"] == "external" and not _confine_candidates([file_data["path"]]):
            return None, f"'{filepath}' is outside the project folder (Settings: Project folder only)", None
        return file_data["content"], None, file_data["path"]
    except GuardedCodeError as e:
        if e.code != "FILE_NOT_FOUND":
            return None, f"'{filepath}' was refused: {e}", None

    from backend import config
    upload_dir = getattr(config, "UPLOAD_DIR", "")
    if upload_dir:
        try:
            candidate = safe_join(upload_dir, filepath)
        except ValueError:
            candidate = None
        if candidate and os.path.isfile(candidate):
            if is_sensitive(candidate):
                return None, f"'{filepath}' was refused: credential and key files are not read by the code tools", None
            if os.path.getsize(candidate) > MAX_INPUT_BYTES:
                return None, _too_large(filepath), None
            try:
                with open(candidate, 'r', encoding='utf-8') as f:
                    return f.read(), None, os.path.realpath(candidate)
            except UnicodeDecodeError:
                return None, f"'{filepath}' is not UTF-8 text", None

    # A chat upload may be known only by its Document row (needs the app database).
    # Only a bare file name is looked up this way, and only inside the uploads.
    if "/" not in filepath.replace("\\", "/") and upload_dir:
        try:
            from backend.utils.uploaded_file_resolver import find_uploaded_file
            uploaded = find_uploaded_file(filepath)
            if uploaded:
                content, on_disk = uploaded
                if on_disk and not is_within(on_disk, [upload_dir]):
                    uploaded = None
                elif content is not None:
                    if len(content) > MAX_INPUT_BYTES:
                        return None, _too_large(filepath), None
                    return content, None, on_disk
                elif on_disk and not is_sensitive(on_disk):
                    if os.path.getsize(on_disk) > MAX_INPUT_BYTES:
                        return None, _too_large(filepath), None
                    with open(on_disk, 'r', encoding='utf-8') as f:
                        return f.read(), None, os.path.realpath(on_disk)
        except Exception as e:
            logger.warning(f"Upload fallback failed for {filepath}: {e}")

    return None, f"'{filepath}' was not found in this Guaardvark checkout or its uploads", None


def _output_file_path(output_dir: str, output_filename: Any) -> str:
    """The file codegen writes: a relative name strictly inside output_dir.
    Raises ValueError with the reason otherwise."""
    from backend.utils.path_safety import safe_join

    name = str(output_filename or "").strip().replace("\\", "/")
    parts = name.split("/")
    if not name or name.endswith("/"):
        raise ValueError("it must name a file")
    if name.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", name):
        raise ValueError("absolute paths are refused")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("'.', '..' and empty folder names are refused")
    path = safe_join(output_dir, name)
    if os.path.isdir(path):
        raise ValueError("it names an existing folder")
    parent = os.path.dirname(path)
    while parent and os.path.realpath(parent) != os.path.realpath(output_dir):
        if os.path.exists(parent) and not os.path.isdir(parent):
            raise ValueError(f"'{os.path.relpath(parent, output_dir)}' is a file, not a folder")
        parent = os.path.dirname(parent)
    return path


class CodeGeneratorTool(BaseTool):
    """
    Complete file analysis and code generation tool.
    Converted from /codegen command rule (rule ID: 17).

    Reads, understands, and generates complete code files with precision.
    Preserves existing functionality while making requested modifications.
    """

    name = "codegen"
    read_only = False
    # Writes OUTPUT_DIR/code/<output_filename>, replacing a file of that name.
    destructive = True
    description = (
        "Ask Guaardvark's local LLM (Ollama) for a complete version of a code file and save its reply "
        "as data/outputs/code/<output_filename>, replacing any file of that name there; input_file is "
        "only read and can never be the output. With input_file the prompt holds that file's full text "
        "plus your instructions; the prompt and a reply of the same length must fit the active model's "
        "context window, and a longer file (or one over 10 MB) is refused before the model runs. "
        "Without it the model writes a new file from the instructions alone, and the call is refused "
        "if the instructions ask to improve, refactor, rewrite or update a file that exists in the checkout "
        "or the uploads (a new file that only shares an existing name, such as README.md, is fine). Only the code is saved: "
        "a Markdown fence and any sentences the model puts around it are dropped, and a reply with no code is an error "
        "and writes nothing. Returns output_path (over MCP relative to the checkout, e.g. "
        "'data/outputs/code/app_v2.py', which read_code opens), filename, language, line and character counts, syntax_ok "
        "(true or false for Python and JSON output, which is parsed but never run; null for other languages) and "
        "the first 500 characters. The model call stops after 180 s; over MCP the call returns an "
        "error after its timeout (120 s by default); the run is not cancelled and saves if the model "
        "answers within 180 s of the start, and "
        "repeating the call with the same idempotency_key waits for that run instead of starting "
        "another. For review notes without writing, use analyze_code; for non-code files, generate_file."
    )

    parameters = {
        "input_file": ToolParameter(
            name="input_file",
            type="string",
            required=False,
            description="Existing file to rewrite: a path relative to the Guaardvark checkout (e.g. 'backend/app.py'; refused like read_code: .env, key files, git-ignored data other than uploads and outputs), or a path inside the uploads folder (e.g. 'Code/app.py'). Over MCP an absolute path works only inside the checkout; in Guaardvark's own chat one elsewhere works too, except system and key folders, and not with Settings > Project folder only on. Leave empty to write a new file. A named file that is missing, unreadable or empty is an error.",
            default=""
        ),
        "output_filename": ToolParameter(
            name="output_filename",
            type="string",
            required=True,
            description="File name to write under data/outputs/code, e.g. 'app_v2.py' or 'web/form.jsx'; subfolders are created. Absolute paths, '~', '.', '..' and existing folder names are refused. Its extension picks the language when language is 'auto'."
        ),
        "instructions": ToolParameter(
            name="instructions",
            type="string",
            required=True,
            description="What to change in input_file, or what the new file should do. Besides this text the model gets only input_file's content, the file names and the language: no chat history and no other files."
        ),
        "language": ToolParameter(
            name="language",
            type="string",
            required=False,
            description="Language name for the prompt, e.g. 'python' or 'typescript'. Default 'auto' maps output_filename's extension (.py, .js, .jsx, .ts, .tsx, .go, .rs, .java, .sh, .sql, .html, .css, .json, .yaml and others); for other extensions name the language.",
            default="auto"
        ),
        "preserve_structure": ToolParameter(
            name="preserve_structure",
            type="bool",
            required=False,
            description="With input_file: true (default) tells the model to keep the file's exact layout and formatting apart from the requested changes; false lets it reorganise where the changes call for it.",
            default=True
        )
    }

    LANGUAGE_MAP = {
        '.py': 'python',
        '.js': 'javascript',
        '.jsx': 'javascript-react',
        '.ts': 'typescript',
        '.tsx': 'typescript-react',
        '.java': 'java',
        '.cpp': 'cpp',
        '.c': 'c',
        '.h': 'c-header',
        '.go': 'go',
        '.rs': 'rust',
        '.rb': 'ruby',
        '.php': 'php',
        '.swift': 'swift',
        '.kt': 'kotlin',
        '.cs': 'csharp',
        '.sql': 'sql',
        '.html': 'html',
        '.css': 'css',
        '.scss': 'scss',
        '.json': 'json',
        '.yaml': 'yaml',
        '.yml': 'yaml',
        '.xml': 'xml',
        '.sh': 'bash',
        '.bash': 'bash',
    }

    def _get_llm(self):
        return _active_llm()

    def _detect_language(self, filename: str) -> str:
        """Detect programming language from file extension"""
        ext = os.path.splitext(filename)[1].lower()
        return self.LANGUAGE_MAP.get(ext, 'unknown')

    def _read_input_file(self, filepath: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        return _read_code_input(self, filepath)

    # What counts as a request to change an existing file is generate_file's
    # rule, shared so the two tools refuse the same wording: a modify verb or an
    # existing-file reference aimed at a file, not a name that merely exists.
    _MODIFY_VERBS = FileGeneratorTool._MODIFY_VERBS
    _EXISTING_REFERENCES = FileGeneratorTool._EXISTING_REFERENCES
    _TARGET_WINDOW_WORDS = FileGeneratorTool._TARGET_WINDOW_WORDS
    _PRONOUN_TARGET = FileGeneratorTool._PRONOUN_TARGET
    _targets = FileGeneratorTool._targets
    _verb_on_pronoun = FileGeneratorTool._verb_on_pronoun
    _detect_modify_existing = FileGeneratorTool._detect_modify_existing

    def _resolves(self, name: str) -> bool:
        """True when name is a file input_file could read. It is looked up exactly
        as input_file would be (checkout, then uploads), so a refused path (outside
        the checkout over MCP, git-ignored, credentials) reads as absent and the
        answer never reveals whether it exists."""
        try:
            _content, _error, found = _read_code_input(self, name)
        except Exception:
            return False
        return bool(found)

    def _referenced_existing_file(self, instructions: str, output_filename: str = "") -> Optional[str]:
        """The file a request without input_file is asking to change, or None
        for a new-file request.

        Every install has a README.md and a start.sh, and a new file of that
        name goes to the outputs folder, so a name that exists is not enough:
        the instructions must aim a modify verb or an existing-file reference
        at it ("refactor backend/app.py", "improve it")."""
        output_name = str(output_filename or "").strip().replace("\\", "/")
        referenced = self._detect_modify_existing(output_name, instructions)
        if referenced:
            return referenced
        # The shared rule looks the output file up by its base name. codegen
        # names its output by path, so "refactor it" with output
        # 'backend/app.py' is aimed at that path.
        if "/" in output_name:
            text = (instructions or "").lower()
            base = os.path.basename(output_name)
            aimed = self._targets(text, base) or self._verb_on_pronoun(text)
            if aimed and self._resolves(output_name):
                return output_name
        return None

    def execute(self, **kwargs) -> ToolResult:
        """Generate or modify code based on instructions"""
        input_file = kwargs.get("input_file", "")
        output_filename = kwargs.get("output_filename")
        instructions = kwargs.get("instructions")
        language = kwargs.get("language", "auto")
        preserve_structure = kwargs.get("preserve_structure", True)

        try:
            # Check the output name before spending any model time on it.
            from backend.config import OUTPUT_DIR
            output_dir = os.path.join(OUTPUT_DIR, "code")
            try:
                output_path = _output_file_path(output_dir, output_filename)
            except ValueError as e:
                return ToolResult(
                    success=False,
                    error=f"output_filename '{output_filename}' must be a file name inside data/outputs/code: {e}",
                )
            clean_name = os.path.relpath(output_path, output_dir)

            # Read input file first (no LLM needed). A named input that cannot be
            # read, or is empty, is an error, never a silent generate-from-scratch.
            input_content = None
            if input_file:
                input_content, read_error, source_path = self._read_input_file(input_file)
                if read_error:
                    return ToolResult(success=False, error=f"codegen could not read input_file: {read_error}")
                if not (input_content or "").strip():
                    return ToolResult(success=False, error=f"input_file '{input_file}' is empty")
                if source_path and os.path.realpath(source_path) == os.path.realpath(output_path):
                    return ToolResult(
                        success=False,
                        error="output_filename names input_file itself; choose a different output name",
                    )

            # Refuse to fabricate before doing any work: if nothing was read but
            # the instructions name an existing file, require input_file rather
            # than inventing a "version" of a file we never saw.
            if not input_content:
                referenced = self._referenced_existing_file(instructions, output_filename)
                if referenced:
                    how = (
                        f"call again with input_file='{referenced}'"
                        if self._resolves(referenced)
                        else "call again with input_file set to that file's path"
                    )
                    return ToolResult(
                        success=False,
                        error=(
                            f"codegen received no input_file, but the instructions ask to change "
                            f"an existing file ('{referenced}'). Without reading it the result "
                            f"would be invented. To change it, {how}. If you want a new file "
                            f"written from scratch, say what it should contain without asking to "
                            f"improve, refactor, rewrite or update an existing one."
                        ),
                    )

            llm = self._get_llm()

            # Detect language
            if not language or language == "auto":
                language = self._detect_language(clean_name)
            fence = "" if language == "unknown" else language
            kind = "" if language == "unknown" else f"{language} "

            layout_rule = (
                "Maintain exact formatting, indentation, and structure"
                if preserve_structure not in (False, "false", "False", 0)
                else "You may reorganise and reformat the file where the requested changes call for it"
            )

            # Build the generation prompt
            if input_content:
                prompt = f"""You are CodeGen, an expert AI specialized in complete file analysis and generation.

CRITICAL INSTRUCTIONS:
1. Read EVERY character of the provided file completely
2. Generate a complete, identical file with the requested modifications
3. Preserve ALL existing functionality except specified changes
4. Never truncate or summarize - return the complete file
5. {layout_rule}
6. Include all imports, functions, classes, and dependencies

INPUT FILE ({input_file}):
```{fence}
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

TASK: Generate a complete {kind}file

FILENAME: {clean_name}

REQUIREMENTS:
{instructions}

QUALITY STANDARDS:
- Generate clean, readable, maintainable code
- Follow the conventions of the language the file name implies
- Include proper error handling
- Add appropriate comments for complex logic
- Ensure the file is immediately usable

OUTPUT THE COMPLETE FILE NOW (no explanations, no markdown fences, just code):"""

            # The prompt and the reply share the context window, and the reply
            # is a whole file about as long as the input. An input that does not
            # leave room would be cut and saved as a complete file.
            window = _context_window(llm)
            if input_content and window:
                needed = _estimated_tokens(prompt) + _estimated_tokens(input_content)
                if needed > window:
                    overhead = _estimated_tokens(prompt) - _estimated_tokens(input_content)
                    fits = max(int((window - overhead) / 2 * PROMPT_CHARS_PER_TOKEN), 0)
                    return ToolResult(
                        success=False,
                        error=(
                            f"input_file '{input_file}' is too long to rewrite in one call: it is "
                            f"{len(input_content):,} characters, and the prompt plus a reply of the "
                            f"same length need about {needed:,} tokens against the {window:,}-token "
                            f"context window of the active model ({getattr(llm, 'model', 'unknown')}). "
                            f"The file would be cut and saved incomplete, so nothing was written. "
                            f"About {fits:,} characters fit: rewrite a smaller file, or switch to a "
                            f"model with a larger context window in Settings."
                        ),
                    )

            from backend.utils.llm_service import ChatMessage, MessageRole
            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            response = llm.chat(messages)

            if response.message:
                try:
                    code_content = str(response.message.content).strip()
                except (ValueError, AttributeError):
                    blocks = getattr(response.message, 'blocks', [])
                    code_content = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                    code_content = code_content.strip()
            else:
                code_content = ""

            code_content = _extract_code(code_content, clean_name)

            # Checked after the fences are removed: a reply of only a fence is empty too.
            if not code_content.strip():
                return ToolResult(
                    success=False,
                    error="The model returned no code, so nothing was written. Try again or reword the instructions.",
                )

            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(code_content)

            # Calculate some metrics
            line_count = len(code_content.split('\n'))
            char_count = len(code_content)

            # An MCP client gets the path relative to the checkout, which read_code
            # accepts; where the checkout sits on disk is not its business. The
            # chat keeps the real path: it builds the reply's file card from it.
            shown_path = display_path(output_path) if is_mcp_transport(self) else output_path

            return ToolResult(
                success=True,
                output={
                    "output_path": shown_path,
                    "filename": clean_name,
                    "language": language,
                    "line_count": line_count,
                    "char_count": char_count,
                    "syntax_ok": _syntax_ok(clean_name, code_content),
                    "content_preview": code_content[:500] + "..." if len(code_content) > 500 else code_content
                },
                metadata={
                    "filename": output_filename,
                    "language": language,
                    "had_input_file": bool(input_content),
                    "lines": line_count
                }
            )

        except Exception as e:
            logger.error(f"Code generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=display_text(f"Code generation failed: {str(e)}")
            )


class CodeAnalysisTool(BaseTool):
    """
    Analyze code files for structure, patterns, and potential improvements.
    """

    name = "analyze_code"
    # The review is the answer, and it cost a model call: the chat model reads
    # it, not its first two lines. 6000 is read_ast_node's budget.
    observation_chars = 6000
    read_only = True
    description = (
        "Review one text file with Guaardvark's local LLM (Ollama) and return its written findings; "
        "nothing is changed or written, and an empty reply is an error. The model is told to cite a line number for each finding, "
        "which is not checked. analysis_type picks the focus. A file over 48,000 characters is not "
        "sent whole: the model gets its first 28,800 and last 14,400 characters plus an outline of "
        "imports, classes and function lines, and the result has truncated=true. Returns file, "
        "language, analysis_type, analysis, line_count, char_count, truncated and visible_lines. The "
        "model call stops after 180 s; over MCP a call that outlasts its timeout (120 s by default) "
        "returns an error. To read the file yourself use read_code (for an upload, 'data/uploads/<path>'); "
        "for a changed copy, codegen."
    )

    parameters = {
        "file_path": ToolParameter(
            name="file_path",
            type="string",
            required=True,
            description="File to review: a path relative to the Guaardvark checkout (e.g. 'backend/app.py'; refused like read_code: .env, key files, git-ignored data other than uploads and outputs), or a path inside the uploads folder (e.g. 'Code/app.py'). Over MCP an absolute path works only inside the checkout; in Guaardvark's own chat one elsewhere works too, except system and key folders, and not with Settings > Project folder only on."
        ),
        "analysis_type": ToolParameter(
            name="analysis_type",
            type="string",
            required=False,
            enum=["full", "structure", "security", "performance", "style"],
            description="Focus of the review: full (default) covers everything; structure (imports, classes, functions, organisation); security (injection, auth, unsafe calls); performance (bottlenecks, memory); style (naming, formatting, documentation).",
            default="full"
        )
    }

    def _get_llm(self):
        return _active_llm()

    def _extract_structure(self, content: str, language: str) -> str:
        """Extract code structure summary for large files"""
        lines = content.split('\n')
        structure_parts = []

        if language in ['python']:
            import_lines = [l for l in lines[:50] if l.strip().startswith(('import ', 'from '))]
            class_lines = [(i+1, l) for i, l in enumerate(lines) if l.strip().startswith('class ')]
            func_lines = [(i+1, l) for i, l in enumerate(lines) if l.strip().startswith('def ')]
        else:
            import_lines = [l for l in lines[:50] if l.strip().startswith(('import ', 'from ', 'require(', 'const ', 'let ')) and ('require' in l or 'import' in l)]
            class_lines = [(i+1, l) for i, l in enumerate(lines) if 'class ' in l and '{' in l or l.strip().startswith('class ')]
            func_lines = [(i+1, l) for i, l in enumerate(lines) if 'function ' in l or ('=>' in l and ('const ' in l or 'let ' in l))]

        if import_lines:
            structure_parts.append(f"Imports ({len(import_lines)}): {', '.join(import_lines[:5])}...")
        if class_lines:
            structure_parts.append(f"Classes: {', '.join([l[1].strip()[:50] for l in class_lines[:5]])}")
        if func_lines:
            structure_parts.append(f"Functions ({len(func_lines)}): lines {', '.join([str(l[0]) for l in func_lines[:10]])}")

        return '\n'.join(structure_parts) if structure_parts else "Structure could not be extracted"

    def execute(self, **kwargs) -> ToolResult:
        """Analyze code file"""
        file_path = kwargs.get("file_path")
        analysis_type = kwargs.get("analysis_type", "full")
        # 48 KB ≈ 12K tokens — fits the vast majority of real source files end
        # to end while staying inside every backend model's context window.
        # The old 6 KB ceiling forced a head/tail split so aggressive that the
        # LLM was effectively reviewing files it hadn't read, and the user
        # was getting confidently-worded generic advice. That's worse than no
        # review — bumped here, with stricter guardrails below.
        MAX_CONTENT_SIZE = 48000

        try:
            content, read_error, _source = _read_code_input(self, file_path)
            if read_error:
                return ToolResult(success=False, error=f"Could not read file: {read_error}")
            if not content:
                return ToolResult(success=False, error=f"'{file_path}' is empty; there is nothing to analyse")

            llm = self._get_llm()
            original_size = len(content)
            # A final newline ends the last line; it does not start another.
            ends_with_newline = content.endswith('\n')
            line_count = content.count('\n') + (0 if ends_with_newline else 1)

            ext = os.path.splitext(file_path)[1].lower()
            language = CodeGeneratorTool.LANGUAGE_MAP.get(ext, 'unknown')

            analysis_prompts = {
                "full": "Provide a comprehensive analysis including structure, patterns, best practices, potential issues, and improvement suggestions.",
                "structure": "Analyze the file structure: imports, classes, functions, dependencies, and overall organization.",
                "security": "Perform a security review: identify potential vulnerabilities, injection risks, authentication issues, and security best practices.",
                "performance": "Analyze performance: identify bottlenecks, inefficient patterns, memory usage concerns, and optimization opportunities.",
                "style": "Review code style: naming conventions, formatting, documentation, readability, and adherence to language conventions."
            }

            analysis_instruction = analysis_prompts.get(analysis_type, analysis_prompts["full"])

            truncated = False
            structure_summary = ""
            head_last_line = line_count
            tail_first_line = line_count + 1
            if len(content) > MAX_CONTENT_SIZE:
                truncated = True
                structure_summary = self._extract_structure(content, language)
                first_portion = int(MAX_CONTENT_SIZE * 0.6)
                last_portion = int(MAX_CONTENT_SIZE * 0.3)
                head_text = content[:first_portion]
                tail_text = content[-last_portion:]
                head_last_line = head_text.count('\n') + 1
                tail_first_line = line_count - tail_text.count('\n') + (1 if ends_with_newline else 0)
                omitted_lines = max(tail_first_line - head_last_line - 1, 0)
                content = (
                    head_text +
                    f"\n\n... [TRUNCATED: {original_size - first_portion - last_portion} chars / "
                    f"~{omitted_lines} lines omitted. You are seeing lines 1-{head_last_line} "
                    f"and lines {tail_first_line}-{line_count} only.] ...\n\n" +
                    tail_text
                )

            if truncated:
                integrity_clause = (
                    "\nSCOPE GUARDRAIL — read this before writing your review:\n"
                    f"You can see lines 1-{head_last_line} and lines {tail_first_line}-{line_count} of this file.\n"
                    f"Lines {head_last_line + 1}-{tail_first_line - 1} are NOT visible to you.\n\n"
                    "Hard rules for your response:\n"
                    f"1. Open with one line stating the visible range, e.g. \"Reviewed lines 1-{head_last_line} and {tail_first_line}-{line_count}; middle ~{max(tail_first_line - head_last_line - 1, 0)} lines not shown.\"\n"
                    "2. Every issue you raise must cite a specific line number you can actually see. If you can't cite a line, you can't see it — leave it out.\n"
                    "3. Do NOT offer generic best-practice advice (\"use structured logging\", \"add type hints\", \"implement retries\", \"use a config object\") unless you observe a concrete instance in the visible code, and you cite the line.\n"
                    "4. If the user's question can only be answered from the omitted middle, say so explicitly and recommend the user re-run analysis on a narrower scope or read those lines directly.\n"
                    "5. Better to give 3 grounded observations than 10 plausible-sounding ones. The user is making real changes based on this — half-read advice can break their project.\n"
                )
            else:
                integrity_clause = (
                    "\nYou have the full file. Every issue you raise must cite a specific line number. "
                    "Be concrete — point at the actual line, not at the language in general. "
                    "Do not pad the review with generic best-practice advice that isn't tied to something you actually observed.\n"
                )

            label = "" if language == "unknown" else f"{language} "
            prompt = f"""Analyze this {label}code file.

FILE: {file_path}
SIZE: {original_size} chars, {line_count} lines{' (TRUNCATED for analysis)' if truncated else ''}
{f'STRUCTURE SUMMARY: {structure_summary}' if structure_summary else ''}

```{"" if language == "unknown" else language}
{content}
```

ANALYSIS REQUEST: {analysis_instruction}
{integrity_clause}
Provide a structured analysis grounded in the visible code, with line citations for every point."""

            from backend.utils.llm_service import ChatMessage, MessageRole
            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            response = llm.chat(messages)

            if response.message:
                try:
                    analysis = str(response.message.content).strip()
                except (ValueError, AttributeError):
                    blocks = getattr(response.message, 'blocks', [])
                    analysis = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                    analysis = analysis.strip()
            else:
                analysis = ""
            if not analysis:
                return ToolResult(success=False, error="The model returned no analysis. Try again.")

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
                    "visible_lines": (
                        [[1, head_last_line], [tail_first_line, line_count]] if truncated else [[1, line_count]]
                    ),
                },
                metadata={
                    "file": file_path,
                    "language": language,
                    "analysis_type": analysis_type,
                    "truncated": truncated
                }
            )

        except Exception as e:
            logger.error(f"Code analysis failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=display_text(f"Code analysis failed: {str(e)}")
            )
