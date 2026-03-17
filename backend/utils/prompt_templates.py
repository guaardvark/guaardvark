# backend/utils/prompt_templates.py
# Version 5.0: Made the '__main__' guard instruction a non-negotiable part of the core code gen prompt.

import logging
from functools import lru_cache  # <-- ADDED IMPORT

# --- Import Prompt Utils ---
from backend.utils import prompt_utils

logger = logging.getLogger(__name__)
# --- End Import ---

import os
import re
import textwrap
from typing import List, Optional

from llama_index.core.retrievers import BaseRetriever

# Template used for automatic code correction attempts
CODE_CORRECTION_TEMPLATE = """
**Objective:** You are a senior debugging engineer. The user provided initial requirements, and a previous version of an AI-generated Python script failed to execute. Your task is to analyze the original requirements, the failed code, and the resulting error message to produce a corrected, runnable Python script.

**Original Requirements:**
{user_requirements}

**Failed Python Code:**
```python
{failed_code}
```

**Execution Error (Traceback):**

```
{error_traceback}
```

**Instructions:**

1. Carefully analyze the error message and traceback to understand the root cause of the failure.
2. Review the failed code in the context of the original requirements.
3. Rewrite the entire Python script, incorporating the necessary fixes.
4. Do NOT just explain the error. Provide only the complete, corrected Python script inside a single ```python...``` code block. """

if "logger" not in globals():  # Should be set by above, but as a safeguard
    logger = logging.getLogger(__name__)


def get_code_generation_prompt(
    input_csv_path: Optional[str],
    input_xml_path: Optional[str],
    output_filename: str,  # This is the FULL TARGET PATH
    user_instructions: str,  # These are the detailed, potentially context-injected instructions
    retriever: Optional[BaseRetriever] = None,
    previous_code: Optional[str] = None,
    error_message: Optional[str] = None,
    project_id: Optional[int] = None,  # For RAG context query if needed
    tags: Optional[List[str]] = None,  # For RAG context query if needed
    available_tools: Optional[List[str]] = None,
    model_name: Optional[str] = None,  # Model used for this code generation
    safe_builtins_list: Optional[List[str]] = None,
    whitelisted_funcs_list: Optional[List[str]] = None,
) -> str:
    """
    Generates the final prompt for the LLM to create Python code.
    The user_instructions are expected to be comprehensive.
    This function wraps them with standard code-gen role, tool info, and critical execution rules.
    """
    logger.info(
        f"Assembling code generation prompt for model: {model_name} (prompt_templates.py v5.0)"
    )

    if not prompt_utils:
        logger.error(
            "prompt_utils module not available for fetching base code_gen_default template."
        )
        # We can proceed without it if user_instructions are self-contained,
        # but it's better if the base provides overall structure.
        # For now, we'll rely on the hardcoded structure below.
        # raise ImportError("Failed to import prompt_utils module.")

    # --- RAG Context Retrieval (if input files are provided for context) ---
    # This context is about the *input files* for the script, not general project context.
    # General project context should be part of the `user_instructions`.
    formatted_input_file_context = "No specific input file context provided for RAG."
    if (input_csv_path or input_xml_path) and retriever:
        try:
            query_parts = [
                f"User goal for script: {user_instructions[:200]}..."
            ]  # Truncate user_instructions for query
            if input_csv_path:
                query_parts.append(f"Regarding input CSV: {input_csv_path}")
            if input_xml_path:
                query_parts.append(f"Regarding input XML: {input_xml_path}")
            context_query = ". ".join(query_parts)
            logger.debug(
                f"Retrieving RAG context for input files with query: '{context_query[:100]}...'"
            )
            context_nodes = retriever.retrieve(context_query)
            if context_nodes:
                formatted_input_file_context = "\n".join(
                    [
                        f"- Context for input files {i+1}: {node.node.get_content(metadata_mode='all').strip()}"
                        for i, node in enumerate(context_nodes[:2])
                    ]  # Limit to 2 snippets for brevity
                )
                logger.debug(
                    f"Retrieved {len(context_nodes)} context nodes for input files."
                )
            else:
                formatted_input_file_context = "No relevant context found in documents for the specified input files."
        except Exception as e:
            logger.warning(f"RAG retrieval for input files failed: {e}", exc_info=True)
            formatted_input_file_context = "Context retrieval error for input files."
    elif not retriever:
        formatted_input_file_context = (
            "Context retrieval for input files skipped (no retriever)."
        )
    # --- End RAG Context for input files ---

    # --- Build Tool Schema ---
    tool_schema_str = (
        "- " + "\n- ".join(available_tools)
        if available_tools
        else "No specific tools provided by the system."
    )

    # --- Retry Block ---
    retry_block_str = ""
    if error_message and previous_code:
        retry_block_str = textwrap.dedent(f"""
        -----------------------------------------------------
        **RETRYING FAILED ATTEMPT**
        The previous Python code attempt failed.
        Previous Code:
        ```python
        {previous_code.strip()}
        ```
        Error Encountered:
        ```
        {error_message.strip()}
        ```
        **Action Required:** Analyze the error and the previous code. Review ALL execution rules below, especially regarding allowed tools, file paths, and the mandatory `output_path` variable. Provide the fully corrected Python code.
        -----------------------------------------------------
        """)
    elif error_message:
        retry_block_str = f"\n**Retry Information:** A previous attempt failed with the error: {error_message}. Please review the rules carefully and provide corrected code.\n"

    # --- Core Prompt Structure ---
    # This structure now directly includes the critical __main__ instruction.
    # The `user_instructions` (which come from the database prompt, e.g. /csv_gen template, with placeholders filled)
    # will be injected into this.

    final_prompt = f"""
You are an expert Python programmer and AI assistant. Your primary task is to generate a Python code snippet that fulfills the user's detailed request.
The generated code will be executed in a restricted environment with access ONLY to specific tools and libraries.

**CRITICAL INSTRUCTION: Your Python script will be executed directly. DO NOT include an `if __name__ == "__main__":` block or any top-level executable code outside of function definitions if not intended to run immediately upon script execution. All main logic should be directly executable or within clearly defined functions that are called.**

**Input File Variables (available in the Python script's execution scope):**
- `csv_input_filename`: '{input_csv_path if input_csv_path else "N/A"}' (This is the FULL PATH to an input CSV, if provided. Value is None if no CSV.)
- `xml_input_filename`: '{input_xml_path if input_xml_path else "N/A"}' (This is the FULL PATH to an input XML, if provided. Value is None if no XML.)

**Target Output Filename Variable (available in the Python script's execution scope):**
- `output_target_filename`: '{output_filename}' (This is the FULL, ABSOLUTE PATH where your script's final output file MUST be written using a provided write tool.)

**User's Detailed Instructions & Context (Follow these meticulously):**
--- BEGIN USER INSTRUCTIONS ---
{user_instructions}
--- END USER INSTRUCTIONS ---

**Available Tools & Libraries (Use ONLY these in your Python script):**
{tool_schema_str}
  - Standard Python built-ins available: {safe_builtins_list or ['print', 'len', 'str', 'int', 'float', 'list', 'dict', 'set', 'range', 'open (restricted)']}
  - Allowed Imports: `import csv`, `import re`, `import os` (for basic path ops like `os.path.join` if absolutely necessary, but prefer using provided full paths). `pandas as pd` can be imported if complex CSV manipulation is needed beyond the `read_csv` tool. `slugify` from `slugify` might be available (check `if slugify:`).

**Context from Input Files (if applicable, for your script's logic):**
{formatted_input_file_context}

{retry_block_str}

**Execution Rules for Your Python Script (MANDATORY):**
1.  **File Output Path:** Your script MUST use the `output_target_filename` variable (which holds the full absolute path) when calling `write_csv` or `write_text`.
    Example: `output_path = write_text(my_text_content, output_target_filename)`
2.  **File Input Path:** If reading from `csv_input_filename` or `xml_input_filename`, use these variables directly as they contain full paths.
    Example: `if csv_input_filename: df = read_csv(csv_input_filename)`
3.  **Restricted Environment:**
    - Adhere strictly to the `Available Tools & Libraries` listed above.
    - DO NOT attempt to import other modules (e.g., `requests`, `subprocess`, `sys`, `glob`).
    - DO NOT attempt direct filesystem operations beyond using the provided `open` (for paths within allowed directories) or the `write_` tools with `output_target_filename`.
    - DO NOT use forbidden built-ins like `eval()`, `exec()` (except for the system executing your script), `globals()`.
4.  **Content First:** If generating content within the script (e.g., text for `write_text` or rows for `write_csv`), ensure the content variable is fully populated and is of the correct type *before* calling the write tool. Do not pass `None` to write tools unless explicitly allowed by the tool's documentation (which is generally not the case).
5.  **Mandatory `output_path` Variable:** Your script **MUST** define and set a variable named `output_path`. This variable must be assigned the return value of the `write_csv` or `write_text` tool call. This is how the system knows where the final file was written.
    Example: `output_path = write_csv(list_of_dictionaries, output_target_filename)`
6.  **Python Validity:** Ensure all variables are defined before use. Double-check basic Python syntax (indentation, colons, etc.).
7.  **Resumability (If applicable, based on User Instructions for multi-item tasks):**
    If the `User Instructions` imply generating multiple items (e.g., rows for a CSV, sections of a text file) and resumability is desired:
    - Your script will have access to:
        - `get_already_processed_item_data()`: Call this at the start. It returns `(processed_data_list, processed_item_ids_set)`.
        - `log_item_as_processed(item_identifier_str, item_data_dict, model_name_str)`: Call this after successfully generating data for one item. Use `current_model_name` (available in scope) for `model_name_str`.
        - `update_overall_job_status(status_message_str)`: Call this periodically.
    - Adapt your script's main loop to check `processed_item_ids_set` and skip already processed items. Accumulate all data (new and previously processed) before the final `write_csv` or `write_text`.

**RESPONSE FORMAT: Respond ONLY with the complete Python code block. Enclose it in ```python ... ```.**
"""
    logger.debug(
        f"Final assembled code-gen prompt (len: {len(final_prompt)}). Snippet: {final_prompt[:350]}..."
    )
    return final_prompt.strip()


# --- QA Prompt Retrieval (No changes needed from v1.8) ---
@lru_cache(maxsize=128)
def get_qa_prompt(model_name: Optional[str] = None) -> str:
    """Retrieves the QA prompt template from database rules (RulesPage)."""

    logger.debug(f"Retrieving QA prompt template for model: {model_name}")

    try:
        # Import rule_utils for database-based rule fetching
        from backend import rule_utils
        from backend.models import db

        # Fetch qa_default template from database via RulesPage
        template, rule_id = rule_utils.get_active_qa_default_template(
            db.session, model_name=model_name
        )

        if rule_id:
            logger.debug(
                f"Using qa_default rule ID {rule_id} from database for model '{model_name}'"
            )
        else:
            logger.debug(f"Using fallback qa_default template for model '{model_name}'")

        return template

    except ImportError as e:
        logger.error(f"Failed to import rule_utils for QA template: {e}")
        # Fallback to basic template
        fallback = "{context_str}\n\n{query_str}"
        return fallback
    except Exception as e:
        logger.error(f"Error fetching QA template from database: {e}", exc_info=True)
        # Fallback to basic template
        fallback = "{context_str}\n\n{query_str}"
        return fallback
