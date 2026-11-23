# backend/api/code_intelligence_api.py
# Code Intelligence API - Provides AI-powered code analysis, editing, and assistance
# Integrates with existing Monaco editor and enhanced chat capabilities

import logging
import json
import os
import traceback
from typing import Dict, List, Optional, Any, Tuple
from flask import Blueprint, current_app, request, jsonify
from datetime import datetime

logger = logging.getLogger(__name__)

# Simple response utilities - no external dependencies
def error_response(msg, code=400):
    return {"error": msg, "status": code}

def success_response(data):
    return {"success": True, "data": data}

# Optional service imports — None when unavailable (no silent no-op stubs).
try:
    from backend.utils.llm_service import get_llm_instance, run_llm_code_prompt
    _llm_service_available = True
    logger.info("LLM service is available for code intelligence")
except ImportError as e:
    logger.warning(f"LLM service not available for code intelligence: {e}")
    _llm_service_available = False
    get_llm_instance = None
    run_llm_code_prompt = None

try:
    from backend.utils.code_storage_bridge import get_code_storage_bridge
    _code_storage_bridge_available = True
    logger.info("Code storage bridge is available for code intelligence")
except ImportError as e:
    logger.warning(f"Code storage bridge not available for code intelligence: {e}")
    _code_storage_bridge_available = False
    get_code_storage_bridge = None

# Try to import real EnhancedChatManager for code intelligence
try:
    from backend.api.enhanced_chat_api import get_chat_manager
    _chat_manager_available = True
    logger.info("EnhancedChatManager is available for code intelligence")
except ImportError as e:
    logger.warning(f"EnhancedChatManager not available: {e}")
    _chat_manager_available = False
    get_chat_manager = None

class ModelCallFailed(Exception):
    """The model behind a code-intelligence route gave no usable reply."""


def _model_failed(what, exc):
    """The answer when the model could not do the work: an error that says so."""
    logger.warning(f"Code intelligence {what} failed: {exc}")
    return error_response(
        f"The model could not be reached for this ({exc}). Nothing was changed or generated; "
        "check that a chat model is loaded and try again.", 503), 503


def send_chat_message_internal(session_id: str, user_message: str, project_id=None, use_rag=True, bypass_rules=False):
    """
    Send a chat message to the enhanced chat API.
    
    Args:
        session_id: Session identifier
        user_message: The user's message/prompt
        project_id: Optional project ID for context
        use_rag: Whether to use RAG (default: True)
        bypass_rules: Whether to bypass rules system (default: False)
    
    Returns:
        Dict with 'message' key containing the model's reply.

    Raises:
        ModelCallFailed: the chat service is missing, the model returned an
            error, or the reply had no text. Callers answer with an error; a
            route never invents a result.
    """
    if not _chat_manager_available or not get_chat_manager:
        raise ModelCallFailed("the chat service is not available")
    
    try:
        chat_manager = get_chat_manager()
        response = chat_manager.process_chat_message(
            session_id=session_id,
            message=user_message,
            use_rag=use_rag,
            debug_mode=False,
            simple_mode=False,
            chat_mode=None,
            voice_mode=False,
            bypass_rules=bypass_rules
        )
        
        # Extract message from response. The chat API doesn't always include
        # a `success` field — most successful responses just have `response`
        # containing the content. Check error first to avoid masking failures,
        # then fall through to content keys in priority order.
        if isinstance(response, dict):
            if 'error' in response:
                raise ModelCallFailed(str(response.get('error')))
            text = response.get('response') or response.get('message')
            if isinstance(text, str) and text.strip():
                return {'message': text}

        logger.warning(f"Unexpected response format from chat API: {response}")
        raise ModelCallFailed("the model returned no text")
        
    except ModelCallFailed:
        raise
    except Exception as e:
        logger.error(f"Failed to send chat message: {e}", exc_info=True)
        raise ModelCallFailed(str(e)) from e

code_intelligence_bp = Blueprint("code_intelligence", __name__, url_prefix="/api/code-intelligence")

@code_intelligence_bp.route("/analyze", methods=["POST"])
def analyze_code():
    """Analyze code for errors, improvements, and suggestions"""
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided"), 400

        # Extract code context
        file_path = data.get('filePath', 'untitled')
        language = data.get('language', 'javascript')
        content = data.get('content', '')
        selected_text = data.get('selectedText', '')
        custom_prompt = data.get('customPrompt', '')

        # Multi-file context support
        related_files = data.get('relatedFiles', [])  # Array of {filePath, content, language}
        project_structure = data.get('projectStructure', '')
        dependencies = data.get('dependencies', [])  # Array of import/require paths
        rules_cutoff = data.get('rulesCutoff', False) or data.get('bypassRules', False)  # Support both naming conventions

        if not content and not selected_text:
            return error_response("No code content provided"), 400

        # Use custom prompt if provided, otherwise use default analysis prompt
        code_to_analyze = selected_text if selected_text else content

        MAX_CODE_SIZE = 6000
        truncated = False
        if len(code_to_analyze) > MAX_CODE_SIZE:
            truncated = True
            first_portion = int(MAX_CODE_SIZE * 0.6)
            last_portion = int(MAX_CODE_SIZE * 0.3)
            original_size = len(code_to_analyze)
            code_to_analyze = (
                code_to_analyze[:first_portion] +
                f"\n\n... [TRUNCATED: {original_size - MAX_CODE_SIZE} chars omitted] ...\n\n" +
                code_to_analyze[-last_portion:]
            )

        if custom_prompt:
            prompt = custom_prompt
        else:
            prompt = f"""Please analyze this {language} code for errors, potential improvements, and suggestions:

File: {file_path}
Language: {language}

```{language}
{code_to_analyze}
```

Please provide:
1. Any syntax or logical errors
2. Performance improvements
3. Code quality suggestions
4. Security considerations
5. Best practice recommendations

Format your response as structured analysis with clear sections.

Please provide recommendations in the following JSON format if possible:
{{
  "recommendations": [{{
    "type": "refactor" | "optimize" | "fix" | "improve" | "security",
    "priority": "high" | "medium" | "low",
    "filePath": "{file_path}",
    "lineRange": {{"start": line_number, "end": line_number}},
    "description": "Human-readable description",
    "suggestedCode": "actual code replacement (if applicable)",
    "rationale": "Why this change improves the code",
    "canAutoApply": true/false
  }}]
}}

If JSON format is not possible, provide recommendations in structured text format."""

        # Get enhanced context from codebase if available
        context_info = ""
        try:
            if get_code_storage_bridge:
                bridge = get_code_storage_bridge()
                search_results = bridge.search_stored_code(f"{language} {file_path}", top_k=3)
                if search_results:
                    context_info = f"\n\nRelated code context:\n{json.dumps(search_results[:2], indent=2)}"
        except Exception as e:
            logger.warning(f"Failed to get code context: {e}")
        
        # Add multi-file context to prompt if available
        if related_files:
            related_context = "\n\nRelated Files:\n"
            for rf in related_files[:5]:  # Limit to 5 related files to avoid token overflow
                rf_path = rf.get('filePath', 'unknown')
                rf_content = rf.get('content', '')
                rf_lang = rf.get('language', language)
                # Truncate content if too long
                if len(rf_content) > 1000:
                    rf_content = rf_content[:1000] + "\n... (truncated)"
                related_context += f"File: {rf_path} ({rf_lang})\n```{rf_lang}\n{rf_content}\n```\n\n"
            prompt += related_context
        
        if project_structure:
            prompt += f"\n\nProject Structure:\n{project_structure}\n"
        
        if dependencies:
            deps_text = "\n".join([f"- {dep}" for dep in dependencies[:10]])  # Limit to 10 dependencies
            prompt += f"\n\nDependencies/Imports:\n{deps_text}\n"

        # Send to LLM for analysis
        if send_chat_message_internal:
            try:
                session_id = f"code-analysis-{datetime.now().timestamp()}"
                response = send_chat_message_internal(
                    session_id=session_id,
                    user_message=prompt + context_info,
                    project_id=None,
                    use_rag=True,
                    bypass_rules=rules_cutoff
                )

                response_message = response.get("message", "Analysis completed")
                
                # Try to parse structured recommendations from response
                recommendations = []
                try:
                    # Try to extract JSON from response
                    import re
                    key_at = response_message.find('"recommendations"')
                    start = response_message.rfind('{', 0, key_at) if key_at != -1 else -1
                    if start != -1:
                        parsed, _end = json.JSONDecoder().raw_decode(response_message[start:])
                        found = parsed.get("recommendations", []) if isinstance(parsed, dict) else []
                        recommendations = found if isinstance(found, list) else []
                except Exception as e:
                    logger.debug(f"Could not parse recommendations from response: {e}")
                    # Recommendations will remain empty array, analysis text will still be returned

                return success_response({
                    "analysis": response_message,
                    "file_path": file_path,
                    "language": language,
                    "suggestions": recommendations,  # Structured recommendations
                    "errors": [],      # Could be parsed from structured response
                    "warnings": []     # Could be parsed from structured response
                })
            except Exception as e:
                return _model_failed("analysis", e)

    except Exception as e:
        logger.error(f"Code analysis failed: {e}", exc_info=True)
        return error_response(f"Analysis failed: {str(e)}"), 500

def _handle_multi_file_edit(multi_file_edits, edit_instructions, language, rules_cutoff=False):
    """Handle multi-file code edits"""
    try:
        edit_results = []
        
        for edit_item in multi_file_edits:
            edit_file_path = edit_item.get('filePath', 'untitled')
            edit_content = edit_item.get('newContent', '')
            edit_description = edit_item.get('description', '')
            
            if not edit_content:
                edit_results.append({
                    "filePath": edit_file_path,
                    "success": False,
                    "error": "No content provided"
                })
                continue
            
            # Create prompt for multi-file edit
            prompt = f"""Apply the following edit to file {edit_file_path}:

Edit Instructions: {edit_instructions}
Additional Context: {edit_description}

File: {edit_file_path}
Language: {language}

New Content:
```{language}
{edit_content}
```

Please verify this edit is correct and explain what was changed."""
            
            if send_chat_message_internal:
                try:
                    session_id = f"multi-file-edit-{datetime.now().timestamp()}"
                    response = send_chat_message_internal(
                        session_id=session_id,
                        user_message=prompt,
                        project_id=None,
                        use_rag=True,
                        bypass_rules=rules_cutoff
                    )
                    
                    edit_results.append({
                        "filePath": edit_file_path,
                        "success": True,
                        "editedCode": edit_content,
                        "reviewed": True,
                        "explanation": response.get("message", "")
                    })
                except Exception as e:
                    logger.warning(f"LLM multi-file edit failed for {edit_file_path}: {e}")
                    edit_results.append({
                        "filePath": edit_file_path,
                        "success": True,  # the content is the caller's own
                        "reviewed": False,
                        "editedCode": edit_content,
                        "explanation": f"The model could not be reached ({e}), so this edit was not reviewed."
                    })
        
        return success_response({
            "multiFileEdits": edit_results,
            "editStrategy": "multi-file",
            "totalFiles": len(edit_results),
            "successfulEdits": len([r for r in edit_results if r.get('success')])
        })
        
    except Exception as e:
        logger.error(f"Multi-file edit failed: {e}", exc_info=True)
        return error_response(f"Multi-file edit failed: {str(e)}"), 500

@code_intelligence_bp.route("/generate", methods=["POST"])
def generate_code():
    """Generate code based on natural language description"""
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided"), 400

        description = data.get('description', '')
        language = data.get('language', 'javascript')
        file_path = data.get('filePath', 'untitled')
        existing_code = data.get('existingCode', '')
        rules_cutoff = data.get('rulesCutoff', False) or data.get('bypassRules', False)

        if not description:
            return error_response("No description provided"), 400

        prompt = f"""Generate {language} code for the following requirement:

"{description}"

File: {file_path}
Target language: {language}
{f'Existing code context:\n```{language}\n{existing_code}\n```' if existing_code else ''}

Please provide:
1. Complete, working code
2. Comments explaining the logic
3. Any necessary imports/dependencies
4. Usage examples if applicable

Return only the code, properly formatted for {language}."""

        # Get enhanced context from codebase
        context_info = ""
        try:
            if get_code_storage_bridge:
                bridge = get_code_storage_bridge()
                search_results = bridge.search_stored_code(f"{description} {language}", top_k=3)
                if search_results:
                    context_info = f"\n\nSimilar code patterns in project:\n{json.dumps(search_results[:2], indent=2)}"
        except Exception as e:
            logger.warning(f"Failed to get code context: {e}")

        if send_chat_message_internal:
            try:
                session_id = f"code-generation-{datetime.now().timestamp()}"
                response = send_chat_message_internal(
                    session_id=session_id,
                    user_message=prompt + context_info,
                    project_id=None,
                    use_rag=True,
                    bypass_rules=rules_cutoff
                )

                return success_response({
                    "code": response.get("message", ""),
                    "language": language,
                    "description": description,
                    "explanation": "Generated code based on your description"
                })
            except Exception as e:
                return _model_failed("generation", e)

    except Exception as e:
        logger.error(f"Code generation failed: {e}", exc_info=True)
        return error_response(f"Generation failed: {str(e)}"), 500

@code_intelligence_bp.route("/edit", methods=["POST"])
def edit_code():
    """Edit existing code based on instructions"""
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided"), 400

        original_code = data.get('originalCode', '')
        edit_instructions = data.get('editInstructions', '')
        language = data.get('language', 'javascript')
        file_path = data.get('filePath', 'untitled')
        
        # Multi-file edit support
        multi_file_edits = data.get('multiFileEdits', [])  # Array of {filePath, newContent, description}
        edit_strategy = data.get('editStrategy', 'single-file')  # 'single-file' | 'multi-file' | 'cross-file-refactor'
        rules_cutoff = data.get('rulesCutoff', False) or data.get('bypassRules', False)

        if not original_code or not edit_instructions:
            return error_response("Original code and edit instructions required"), 400
        
        # Handle multi-file edits
        if edit_strategy in ['multi-file', 'cross-file-refactor'] and multi_file_edits:
            return _handle_multi_file_edit(multi_file_edits, edit_instructions, language, rules_cutoff)

        prompt = f"""Edit this {language} code according to the following instructions:

Instructions: {edit_instructions}

Original code:
```{language}
{original_code}
```

Please provide:
1. The modified code with requested changes applied
2. Brief explanation of what was changed
3. Any important notes about the modifications

Return the complete modified code, properly formatted for {language}."""

        if send_chat_message_internal:
            try:
                session_id = f"code-edit-{datetime.now().timestamp()}"
                response = send_chat_message_internal(
                    session_id=session_id,
                    user_message=prompt,
                    project_id=None,
                    use_rag=True,
                    bypass_rules=rules_cutoff
                )

                return success_response({
                    "editedCode": response.get("message", ""),
                    "originalCode": original_code,
                    "instructions": edit_instructions,
                    "language": language
                })
            except Exception as e:
                return _model_failed("edit", e)

    except Exception as e:
        logger.error(f"Code editing failed: {e}", exc_info=True)
        return error_response(f"Edit failed: {str(e)}"), 500

@code_intelligence_bp.route("/explain", methods=["POST"])
def explain_code():
    """Explain selected code or provide documentation"""
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided"), 400

        code_content = data.get('content', '')
        language = data.get('language', 'javascript')
        file_path = data.get('filePath', 'untitled')
        rules_cutoff = data.get('rulesCutoff', False) or data.get('bypassRules', False)

        if not code_content:
            return error_response("No code content provided"), 400

        prompt = f"""Please explain this {language} code in detail:

File: {file_path}

```{language}
{code_content}
```

Please provide:
1. Overall purpose and functionality
2. Step-by-step explanation of what the code does
3. Key concepts and patterns used
4. Input/output description
5. Any notable implementation details

Make the explanation clear and educational."""

        if send_chat_message_internal:
            try:
                session_id = f"code-explanation-{datetime.now().timestamp()}"
                response = send_chat_message_internal(
                    session_id=session_id,
                    user_message=prompt,
                    project_id=None,
                    use_rag=True,
                    bypass_rules=rules_cutoff
                )

                return success_response({
                    "explanation": response.get("message", ""),
                    "code": code_content,
                    "language": language
                })
            except Exception as e:
                return _model_failed("explanation", e)

    except Exception as e:
        logger.error(f"Code explanation failed: {e}", exc_info=True)
        return error_response(f"Explanation failed: {str(e)}"), 500

@code_intelligence_bp.route("/refactor", methods=["POST"])
def refactor_code():
    """Refactor code according to best practices"""
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided"), 400

        code_content = data.get('content', '')
        refactor_type = data.get('refactorType', 'optimize')
        rules_cutoff = data.get('rulesCutoff', False) or data.get('bypassRules', False)
        language = data.get('language', 'javascript')
        file_path = data.get('filePath', 'untitled')

        if not code_content:
            return error_response("No code content provided"), 400

        prompt = f"""Please refactor this {language} code to {refactor_type} it:

File: {file_path}
Refactor type: {refactor_type}

Original code:
```{language}
{code_content}
```

Please provide:
1. Refactored code with improvements
2. Explanation of changes made
3. Benefits of the refactoring
4. Any trade-offs or considerations

Focus on improving readability, performance, and maintainability while preserving functionality."""

        if send_chat_message_internal:
            try:
                session_id = f"code-refactor-{datetime.now().timestamp()}"
                response = send_chat_message_internal(
                    session_id=session_id,
                    user_message=prompt,
                    project_id=None,
                    use_rag=True,
                    bypass_rules=rules_cutoff
                )

                return success_response({
                    "refactoredCode": response.get("message", ""),
                    "originalCode": code_content,
                    "refactorType": refactor_type,
                    "language": language
                })
            except Exception as e:
                return _model_failed("refactoring", e)

    except Exception as e:
        logger.error(f"Code refactoring failed: {e}", exc_info=True)
        return error_response(f"Refactoring failed: {str(e)}"), 500

@code_intelligence_bp.route("/generate-tests", methods=["POST"])
def generate_tests():
    """Generate unit tests for the provided code"""
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided"), 400

        code_content = data.get('content', '')
        language = data.get('language', 'javascript')
        file_path = data.get('filePath', 'untitled')
        rules_cutoff = data.get('rulesCutoff', False) or data.get('bypassRules', False)
        test_framework = data.get('testFramework', 'auto')

        if not code_content:
            return error_response("No code content provided"), 400

        # Determine appropriate test framework based on language
        frameworks = {
            'javascript': "Jest",
            'typescript': "Jest",
            'python': "pytest",
            'java': "JUnit",
            'csharp': "NUnit",
            'go': "Go testing",
            'rust': "Rust testing"
        }

        framework = frameworks.get(language, "appropriate testing framework") if test_framework == "auto" else test_framework

        prompt = f"""Generate unit tests for this {language} code using {framework}:

File: {file_path}

Code to test:
```{language}
{code_content}
```

Please provide:
1. Complete test file with comprehensive test cases
2. Tests for normal cases, edge cases, and error conditions
3. Appropriate test setup and teardown if needed
4. Mock objects or test data as required
5. Clear test descriptions and assertions

Ensure tests follow {framework} best practices and conventions."""

        if send_chat_message_internal:
            session_id = f"test-generation-{datetime.now().timestamp()}"
            response = send_chat_message_internal(
                session_id=session_id,
                user_message=prompt,
                project_id=None,
                use_rag=True,
                bypass_rules=rules_cutoff
            )

            return success_response({
                "tests": response.get("message", ""),
                "framework": framework,
                "originalCode": code_content,
                "language": language
            })
        else:
            return error_response("LLM service not available"), 503

    except Exception as e:
        logger.error(f"Test generation failed: {e}", exc_info=True)
        return error_response(f"Test generation failed: {str(e)}"), 500

@code_intelligence_bp.route("/completion", methods=["POST"])
def code_completion():
    """Provide intelligent code completion suggestions"""
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided"), 400

        code_before = data.get('codeBefore', '')
        code_after = data.get('codeAfter', '')
        language = data.get('language', 'javascript')
        file_path = data.get('filePath', 'untitled')
        cursor_position = data.get('cursorPosition', {})
        rules_cutoff = data.get('rulesCutoff', False) or data.get('bypassRules', False)

        prompt = f"""Provide intelligent code completion for this {language} code:

File: {file_path}
Cursor Position: Line {cursor_position.get('line', 0)}, Column {cursor_position.get('column', 0)}

Code before cursor:
```{language}
{code_before}
```

Code after cursor:
```{language}
{code_after}
```

Please provide:
1. Most likely completion suggestions (up to 5)
2. Brief explanation for each suggestion
3. Confidence level for each suggestion

Format as structured JSON with suggestions array."""

        if send_chat_message_internal:
            try:
                session_id = f"code-completion-{datetime.now().timestamp()}"
                response = send_chat_message_internal(
                    session_id=session_id,
                    user_message=prompt,
                    project_id=None,
                    use_rag=False,
                    bypass_rules=rules_cutoff
                )

                return success_response({
                    "suggestions": response.get("message", ""),
                    "language": language,
                    "position": cursor_position
                })
            except Exception as e:
                logger.warning(f"LLM completion failed: {e}")
                # Fall through to basic completion

        # The model gave nothing: offer the language's common openers, and say
        # that is what they are.
        context_suggestions = []
        if language.lower() == 'javascript':
            context_suggestions = [
                {"text": "function ", "label": "function", "kind": "Function"},
                {"text": "const ", "label": "const", "kind": "Variable"},
                {"text": "async ", "label": "async", "kind": "Keyword"},
                {"text": "await ", "label": "await", "kind": "Keyword"},
                {"text": "console.log(", "label": "console.log", "kind": "Method"}
            ]
        elif language.lower() == 'python':
            context_suggestions = [
                {"text": "def ", "label": "def", "kind": "Function"},
                {"text": "class ", "label": "class", "kind": "Class"},
                {"text": "async def ", "label": "async def", "kind": "Function"},
                {"text": "import ", "label": "import", "kind": "Module"},
                {"text": "print(", "label": "print", "kind": "Function"}
            ]
        else:
            context_suggestions = [
                {"text": "# ", "label": "comment", "kind": "Comment"},
                {"text": "function", "label": "function", "kind": "Function"},
                {"text": "var ", "label": "variable", "kind": "Variable"}
            ]

        return success_response({
            "suggestions": context_suggestions,
            "language": language,
            "position": cursor_position,
            "model_used": None,
            "source": "keywords"
        })

    except Exception as e:
        logger.error(f"Code completion failed: {e}", exc_info=True)
        return error_response(f"Completion failed: {str(e)}"), 500

@code_intelligence_bp.route("/validate", methods=["POST"])
def validate_code():
    """Syntax-check code with a real parser and return the errors it found.

    This used to send the file to the model and discard the answer, returning
    empty error lists that the editor showed as "no issues". It now runs
    backend.services.code_syntax_check (Python and JSON via the standard
    library, JavaScript via `node --check`) and says in `mode` and `note` when
    the language could not be checked at all.
    """
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided"), 400

        code_content = data.get('content', '')
        language = data.get('language', 'javascript')

        if not code_content:
            return error_response("No code content provided"), 400

        from backend.services.code_syntax_check import check_syntax
        checked = check_syntax(code_content, language)
        if not checked['supported']:
            return success_response({
                "validation": checked['note'],
                "errors": [],
                "warnings": [],
                "language": language,
                "mode": "unchecked",
                "tool": None,
                "note": checked['note'],
            })

        count = len(checked['errors'])
        return success_response({
            "validation": "No syntax errors" if count == 0 else f"{count} syntax error{'s' if count != 1 else ''}",
            "errors": checked['errors'],
            "warnings": checked['warnings'],
            "language": language,
            "mode": "syntax",
            "tool": checked['tool'],
            "note": "",
        })

    except Exception as e:
        logger.error(f"Code validation failed: {e}", exc_info=True)
        return error_response(f"Validation failed: {str(e)}"), 500

@code_intelligence_bp.route("/health", methods=["GET"])
def health_check():
    """Health check for code intelligence service"""
    try:
        logger.info("Health check called")

        # LLM availability — reads current_app.config; cheap, no model load.
        llm_available = False
        if get_llm_instance is not None:
            try:
                llm_available = get_llm_instance() is not None
            except Exception as e:
                logger.warning(f"LLM availability probe failed: {e}")
        logger.info(f"LLM available check: {llm_available}")

        # REAL chat availability — this module's chat path runs through the enhanced
        # chat manager; reflect whether it actually imported and is wired.
        chat_available = bool(_chat_manager_available and get_chat_manager)
        logger.info(f"Chat available check: {chat_available}")

        # Get active model information
        try:
            from backend.models import get_active_model_name
            active_model = get_active_model_name()
        except Exception as e:
            logger.warning(f"Could not get active model: {e}")
            active_model = "unknown"

        # Tiered, honest status — no "fully_functional_offline" claim when nothing works.
        if llm_available and chat_available:
            status = "ready"
        elif llm_available or chat_available:
            status = "degraded"
        else:
            status = "unavailable"

        return success_response({
            "status": status,
            "active_model": active_model,
            "llm_available": llm_available,
            "chat_available": chat_available,
            "timestamp": datetime.now().isoformat(),
            # Declared surface area (routes that exist), NOT a runtime health claim.
            # Field names kept as `endpoints`/`capabilities` for the existing
            # frontend consumer (codeIntelligenceService.checkCodeIntelligenceHealth).
            "endpoints": [
                "/analyze", "/generate", "/edit", "/explain",
                "/refactor", "/generate-tests", "/completion", "/validate"
            ],
            "capabilities": [
                "code_analysis", "code_generation", "code_editing",
                "code_explanation", "code_refactoring", "test_generation",
                "code_completion", "code_validation"
            ]
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return error_response(f"Health check failed: {str(e)}"), 500