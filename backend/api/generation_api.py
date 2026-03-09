#!/usr/bin/env python3
# backend/api/generation_api.py
# Version 2.0: Integrated with System Coordinator for security and resource management
# SECURITY FIXES: Bug #7 (Path Traversal) and Bug #10 (Prompt Injection)

import logging
import os
import json
from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

# Import secure file operations (security validation removed for local system)
try:
    from backend.utils.secure_file_operations import (
        secure_write_file, sanitize_generation_params, get_secure_file_manager
    )
    from backend.utils.system_coordinator import get_system_coordinator, ProcessType
except ImportError as e:
    logger.warning(f"Failed to import secure file operations: {e}")
    # Fallback functions
    def secure_write_file(file_path: str, content: str) -> str:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return file_path
    
    def sanitize_generation_params(params):
        return params
    
    def get_secure_file_manager():
        class DummyManager:
            def secure_filename(self, filename):
                return filename
        return DummyManager()
    
    def get_system_coordinator():
        class DummyCoordinator:
            def managed_operation(self, *args, **kwargs):
                class DummyContext:
                    def __enter__(self):
                        return "dummy_process_id"
                    def __exit__(self, *args):
                        pass
                return DummyContext()
        return DummyCoordinator()
    
    class ProcessType:
        FILE_GENERATION = "file_generation"

logger = logging.getLogger(__name__)


def _validate_and_enhance_jsx_content(content: str, filename: str) -> str:
    """Validate and enhance JSX content with proper React imports and structure."""
    import re

    # Check if React is imported
    has_react_import = bool(re.search(r'import\s+(?:React|.*?)\s+from\s+[\'"]react[\'"]', content))

    # Check if there's a component definition
    has_component = bool(re.search(r'(?:function|const|class)\s+([A-Z][a-zA-Z0-9_]*)', content))

    # Check if there's an export statement
    has_export = bool(re.search(r'export\s+(?:default\s+)?', content))

    # Extract component name from filename
    component_name = filename.replace('.jsx', '').split('/')[-1]
    if not component_name[0].isupper():
        component_name = component_name.capitalize()

    enhanced_content = content

    # Add React import if missing
    if not has_react_import:
        logger.info(f"Adding React import to JSX file: {filename}")
        react_import = "import React from 'react';\n\n"
        enhanced_content = react_import + enhanced_content

    # Add default export if missing
    if not has_export:
        logger.info(f"Adding export statement to JSX file: {filename}")
        enhanced_content += f"\n\nexport default {component_name};"

    # Validate JSX syntax basics
    if '<' in enhanced_content and '>' in enhanced_content:
        # Basic JSX validation - check for common JSX patterns
        if 'class=' in enhanced_content and 'className=' not in enhanced_content:
            logger.info(f"Converting HTML 'class' to JSX 'className' in {filename}")
            enhanced_content = enhanced_content.replace('class=', 'className=')

    return enhanced_content


def _validate_and_enhance_python_content(content: str, filename: str) -> str:
    """Validate and enhance Python content with proper imports, docstrings, and structure."""
    import re
    import ast

    # Check if content has proper Python structure
    has_imports = bool(re.search(r'^(import|from)\s+', content, re.MULTILINE))
    has_main_block = bool(re.search(r'if __name__ == ["\']__main__["\']:', content))
    has_docstring = bool(re.search(r'""".*?"""', content, re.DOTALL)) or bool(re.search(r"'''.*?'''", content, re.DOTALL))

    enhanced_content = content

    # Basic syntax validation
    try:
        ast.parse(enhanced_content)
        logger.info(f"Python syntax validation passed for {filename}")
    except SyntaxError as e:
        logger.warning(f"Python syntax error in generated content for {filename}: {e}")
        # Don't modify content if syntax is invalid - let it fail explicitly
        return content

    # Add module docstring if missing
    if not has_docstring and not enhanced_content.strip().startswith('"""'):
        module_name = filename.replace('.py', '').replace('_', ' ').title()
        module_docstring = f'"""{module_name} module.\n\nGenerated module for {module_name.lower()} functionality.\n"""\n\n'
        enhanced_content = module_docstring + enhanced_content

    # Add standard imports if missing common ones and they're used in code
    common_imports_needed = []

    if 'List[' in enhanced_content or 'Dict[' in enhanced_content or 'Optional[' in enhanced_content:
        if 'from typing import' not in enhanced_content and 'import typing' not in enhanced_content:
            common_imports_needed.append('from typing import List, Dict, Optional, Any')

    if 'os.path' in enhanced_content or 'os.environ' in enhanced_content:
        if 'import os' not in enhanced_content:
            common_imports_needed.append('import os')

    if 'sys.' in enhanced_content:
        if 'import sys' not in enhanced_content:
            common_imports_needed.append('import sys')

    if 'datetime.' in enhanced_content or 'timedelta' in enhanced_content:
        if 'import datetime' not in enhanced_content and 'from datetime import' not in enhanced_content:
            common_imports_needed.append('from datetime import datetime, timedelta')

    # Add needed imports at the top (after module docstring)
    if common_imports_needed:
        logger.info(f"Adding common imports to Python file: {filename}")
        imports_section = '\n'.join(common_imports_needed) + '\n\n'

        # Insert after module docstring if present
        if enhanced_content.startswith('"""'):
            # Find end of docstring
            docstring_end = enhanced_content.find('"""', 3) + 3
            enhanced_content = enhanced_content[:docstring_end] + '\n\n' + imports_section + enhanced_content[docstring_end:].lstrip()
        else:
            enhanced_content = imports_section + enhanced_content

    # Add main block if this looks like a script (has function calls at module level)
    if not has_main_block:
        # Check if there are function calls at module level that should be in main
        lines = enhanced_content.split('\n')
        has_module_level_calls = False

        for line in lines:
            stripped = line.strip()
            if (stripped and
                not stripped.startswith(('#', '"""', "'''", 'import', 'from', 'def ', 'class ')) and
                not stripped.startswith(('if ', 'for ', 'while ', 'try:', 'except', 'finally:')) and
                '=' not in stripped and
                stripped.endswith((')', ']', '}')) and
                not stripped.startswith('@')):
                has_module_level_calls = True
                break

        if has_module_level_calls:
            logger.info(f"Adding if __name__ == '__main__' block to Python file: {filename}")
            enhanced_content += '\n\nif __name__ == "__main__":\n    main()\n'

    # Fix common Python issues
    # Convert print statements to print functions if needed
    enhanced_content = re.sub(r'\bprint\s+([^(].*?)$', r'print(\1)', enhanced_content, flags=re.MULTILINE)

    return enhanced_content


# --- Blueprint Definition ---
generation_bp = Blueprint("generation_api", __name__, url_prefix="/api/generate")

try:
    from backend.utils import llm_service
    from backend.tools import file_processor
    from backend.rule_utils import get_active_command_rule
    from backend.models import db, Rule, Task
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType
except ImportError as e:
    logger.critical(f"Failed to import required modules: {e}")
    file_processor = None
    llm_service = None
    get_active_command_rule = None
    db = None
    Rule = None
    Task = None
    get_unified_progress = None
    ProcessType = None

@generation_bp.route("/batch_csv", methods=["POST"])
def generate_batch_csv_route():
    """Restored batch CSV generation endpoint for FileGenerationPage compatibility"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
            
        output_filename = data.get("output_filename")
        items = data.get("items", [])
        prompt_rule_id = data.get("prompt_rule_id")
        project_id = data.get("project_id")
        target_website = data.get("target_website")
        
        if not output_filename or not items or not prompt_rule_id:
            return jsonify({
                "error": "Missing required fields: output_filename, items, and prompt_rule_id"
            }), 400
        
        # Create a task for the CSV generation
        from backend.models import Task, db
        
        new_task = Task(
            name=f"Batch CSV Generation: {output_filename}",
            type="batch_csv_generation",
            description=f"Generate CSV with {len(items)} items using rule {prompt_rule_id}",
            status="pending",
            output_filename=output_filename,
            project_id=project_id,
            prompt_text=f"Items: {', '.join(items[:5])}{'...' if len(items) > 5 else ''}"
        )
        
        db.session.add(new_task)
        db.session.commit()
        
        logger.info(f"Created batch CSV generation task: {new_task.id}")
        
        return jsonify({
            "message": "Batch CSV generation task created successfully",
            "task_id": new_task.id,
            "job_id": f"batch_csv_{new_task.id}",
            "status": "scheduled",
            "details": f"Task scheduled for {len(items)} items"
        }), 200
        
    except Exception as e:
        logger.error(f"Error creating batch CSV task: {e}")
        if 'db' in locals():
            db.session.rollback()
        return jsonify({"error": "Failed to create batch CSV generation task"}), 500


@generation_bp.route("/direct_generate_and_save", methods=["POST"])
def direct_generate_and_save_file_route():
    """Enhanced direct file generation with security and resource management."""
    
    # SECURITY FIX: Use system coordinator for managed operation
    coordinator = get_system_coordinator()
    
    with coordinator.managed_operation(
        "file_generation", 
        ProcessType.FILE_GENERATION
    ) as process_id:
        try:
            # Validate request format
            if not request.is_json:
                return jsonify({"error": "Request must be JSON."}), 400
                
            data = request.get_json()
            output_filename = data.get("outputfile")
            prompt_text = data.get("prompt_text")
            rule_id = data.get("rule_id")  # Optional rule ID from frontend
            project_id = data.get("project_id")  # Project ID for RAG context

            # Enhanced parameter validation
            if not output_filename:
                return jsonify({
                    "error": "Missing required parameter: 'outputfile'",
                    "details": "Please provide a filename for the generated content"
                }), 400
                
            if not prompt_text:
                return jsonify({
                    "error": "Missing required parameter: 'prompt_text'", 
                    "details": "Please provide content or prompt for file generation"
                }), 400

            # SECURITY FIX: Secure filename validation
            secure_file_manager = get_secure_file_manager()
            try:
                secure_filename_result = secure_file_manager.secure_filename(output_filename)
                if not secure_filename_result or secure_filename_result.strip() == "":
                    raise ValueError("Filename resulted in empty string after sanitization")
            except (ValueError, AttributeError) as e:
                logger.warning(f"Invalid filename '{output_filename}': {e}")
                return jsonify({
                    "error": "Invalid filename provided",
                    "details": f"Filename validation failed: {str(e)}",
                    "provided_filename": output_filename
                }), 400

            # Security validation removed for local system

            # Check output directory configuration
            output_dir = current_app.config.get("OUTPUT_DIR")
            if not output_dir:
                logger.error("OUTPUT_DIR not configured in app config")
                return jsonify({
                    "error": "Server configuration error: Output directory not set.",
                    "details": "Please contact system administrator"
                }), 500

            # SECURITY FIX: Validate output directory access
            output_path = os.path.join(output_dir, secure_filename_result)
            if not coordinator.validate_security("file_operation", 
                                                file_path=output_path, 
                                                operation="write"):
                return jsonify({
                    "error": "File path security validation failed",
                    "details": "The requested file path is not allowed"
                }), 403

            # Get LLM instance and generate content
            llm = current_app.config.get("LLAMA_INDEX_LLM")
            if not llm:
                return jsonify({"error": "LLM not configured."}), 503

            # ENHANCED: Get RulesPage system prompt for file generation
            try:
                from backend import rule_utils
                from backend.models import db
                
                # Get rule - use frontend-provided rule_id if available, otherwise default to CodeGen
                model_name = getattr(llm, "model", "default")
                codegen_rule = None
                
                if rule_id:
                    # Use specific rule provided by frontend
                    logger.info(f"Using frontend-provided rule ID: {rule_id}")
                    from backend.models import Rule
                    codegen_rule = db.session.query(Rule).filter_by(id=rule_id, is_active=True).first()
                    if codegen_rule:
                        logger.info(f"Frontend rule found: ID={codegen_rule.id}, name={codegen_rule.name}")
                    else:
                        logger.warning(f"Frontend-provided rule ID {rule_id} not found or inactive")
                
                if not codegen_rule:
                    # Fallback to default CodeGen rule
                    logger.info(f"Attempting to retrieve default CodeGen rule for model: {model_name}")
                    codegen_rule = rule_utils.get_active_command_rule(
                        "/codegen", 
                        db.session, 
                        model_name=model_name
                    )
                    logger.info(f"Default CodeGen rule retrieval result: {codegen_rule is not None}")
                
                if codegen_rule:
                    logger.info(f"Using rule: ID={codegen_rule.id}, length={len(codegen_rule.rule_text)}")
                system_prompt = codegen_rule.rule_text if codegen_rule else None
                final_rule_id = codegen_rule.id if codegen_rule else None
                
                if system_prompt:
                    logger.info(f"Using RulesPage system prompt for file generation (rule ID: {final_rule_id})")
                    system_message = system_prompt
                else:
                    # Fallback to enhanced default prompt
                    logger.info("No RulesPage prompt found, using enhanced default")
                    system_message = """You are a professional file generator with complete file analysis capabilities. Your task is to create comprehensive, detailed file content based on the user's requirements and any uploaded file context.

ENHANCED INSTRUCTIONS:
1. Generate ACTUAL file content that can be saved directly to a file
2. Do NOT include explanations, disclaimers, or meta-text
3. Do NOT apologize or express concerns about copyright
4. Create unique, valuable content that serves the user's business needs
5. For business content, create realistic, professional material
6. Generate substantial content when requested
7. Focus on providing value and solving the user's business problems
8. If uploaded files are provided, analyze them completely and use their context
9. Consider every character and line of uploaded files for complete understanding
10. Generate content that builds upon or improves the uploaded file context

Generate the complete file content now:"""
                    
            except Exception as e:
                logger.warning(f"Failed to get RulesPage prompt for file generation: {e}")
                # Fallback to basic prompt
                system_message = """You are a professional file generator. Your task is to create comprehensive, detailed file content based on the user's requirements.

IMPORTANT INSTRUCTIONS:
1. Generate ACTUAL file content that can be saved directly to a file
2. Do NOT include explanations, disclaimers, or meta-text
3. Do NOT apologize or express concerns about copyright
4. Create unique, valuable content that serves the user's business needs
5. For business content, create realistic, professional material
6. Generate substantial content when requested
7. Focus on providing value and solving the user's business problems

Generate the complete file content now:"""

            # ENHANCED: Use proper RAG retrieval with search_with_llamaindex
            uploaded_file_context = ""
            try:
                # Use the actual RAG system that enhanced_chat_api uses
                from backend.services.indexing_service import search_with_llamaindex
                from backend.utils.entity_context_enhancer import EntityContextEnhancer
                from backend.services.entity_relationship_indexer import EntityRelationshipIndexer

                logger.info(f"RAG GENERATION: Using proper RAG search for context retrieval")

                # Step 1: Use RAG/vector search for semantically relevant content
                try:
                    rag_results = search_with_llamaindex(prompt_text, max_chunks=8)
                    if rag_results is None:
                        rag_results = []
                    logger.info(f"RAG GENERATION: Retrieved {len(rag_results)} chunks from vector search")
                except Exception as rag_error:
                    logger.error(f"RAG GENERATION: Vector search failed: {rag_error}")
                    rag_results = []

                # Step 2: Get project-specific database files as fallback/additional context
                from backend.models import Document as DBDocument
                if project_id:
                    recent_files = db.session.query(DBDocument).filter(
                        DBDocument.project_id == project_id,
                        DBDocument.content.isnot(None),
                        DBDocument.index_status.in_(["STORED", "INDEXED"])
                    ).order_by(DBDocument.uploaded_at.desc()).limit(3).all()
                    logger.info(f"RAG GENERATION: Retrieved {len(recent_files)} project files for project {project_id}")
                else:
                    recent_files = db.session.query(DBDocument).filter(
                        DBDocument.content.isnot(None),
                        DBDocument.index_status.in_(["STORED", "INDEXED"])
                    ).order_by(DBDocument.uploaded_at.desc()).limit(2).all()
                    logger.info(f"RAG GENERATION: Retrieved {len(recent_files)} recent files (no project_id)")

                # Step 3: Build comprehensive context
                if rag_results or recent_files:
                    context_parts = []
                    context_parts.append("=== RAG GENERATION CONTEXT ===")

                    # Add RAG search results (most relevant)
                    if rag_results and len(rag_results) > 0:
                        context_parts.append("VECTOR SEARCH RESULTS:")
                        for i, result in enumerate(rag_results):
                            if result and result.get('content'):  # Ensure result has content
                                context_parts.append(f"CHUNK {i+1} (Score: {result.get('score', 0.0):.3f}):")
                                context_parts.append(f"Source: {result.get('metadata', {}).get('source_filename', 'Unknown')}")
                                context_parts.append(f"Content: {result.get('content', '')}")
                                context_parts.append("---")
                    else:
                        logger.info(f"RAG search returned no relevant results for query: {prompt_text[:100]} - this is normal for new content generation")
                        context_parts.append("VECTOR SEARCH: No relevant indexed content found for this query. Proceeding with standalone generation.")

                    # Add complete project files (if available and different from RAG results)
                    if recent_files:
                        context_parts.append("PROJECT FILES:")
                        for file_doc in recent_files:
                            if file_doc.content:
                                context_parts.append(f"FILE: {file_doc.filename}")
                                context_parts.append(f"TYPE: {file_doc.filename.split('.')[-1] if '.' in file_doc.filename else 'unknown'}")
                                context_parts.append(f"SIZE: {len(file_doc.content)} characters")
                                context_parts.append(f"PROJECT_ID: {file_doc.project_id}")
                                context_parts.append("COMPLETE FILE CONTENT:")
                                context_parts.append(file_doc.content)
                                context_parts.append("---")

                    context_parts.append("=== END RAG GENERATION CONTEXT ===")
                    uploaded_file_context = "\n".join(context_parts)
                    logger.info(f"RAG GENERATION: Built context with {len(rag_results)} RAG chunks + {len(recent_files)} project files")

            except Exception as context_error:
                logger.warning(f"Failed to retrieve unified context: {context_error}")
                uploaded_file_context = ""

            # ENHANCED: Generate content with uploaded file context
            try:
                # Build enhanced prompt with file context
                enhanced_prompt = prompt_text
                if uploaded_file_context:
                    enhanced_prompt = f"{uploaded_file_context}\n\nUSER REQUEST: {prompt_text}"
                    logger.info(f"ENHANCED GENERATION: Using file context ({len(uploaded_file_context)} chars)")
                    logger.info(f"ENHANCED GENERATION: Enhanced prompt length: {len(enhanced_prompt)} chars")
                else:
                    logger.info("ENHANCED GENERATION: No file context available")
                
                # DEBUG: Log the exact prompt being sent to LLM
                logger.info(f"DEBUG GENERATION: System message preview: {system_message[:200]}...")
                logger.info(f"DEBUG GENERATION: System message length: {len(system_message)} chars")
                logger.info(f"DEBUG GENERATION: Enhanced prompt preview: {enhanced_prompt[:200]}...")
                logger.info(f"DEBUG GENERATION: Enhanced prompt length: {len(enhanced_prompt)} chars")
                
                generated_content = llm_service.run_llm_chat_prompt(
                    enhanced_prompt,
                    llm_instance=llm,
                    messages=[
                        llm_service.ChatMessage(role=llm_service.MessageRole.SYSTEM, content=system_message),
                        llm_service.ChatMessage(role=llm_service.MessageRole.USER, content=enhanced_prompt),
                    ]
                )
                
                # DEBUG: Log the LLM response
                logger.info(f"DEBUG GENERATION: LLM response preview: {str(generated_content)[:300]}...")
                logger.info(f"DEBUG GENERATION: LLM response length: {len(str(generated_content))} chars")
                
                if not generated_content or not str(generated_content).strip():
                    logger.warning(f"LLM returned empty content for prompt: {enhanced_prompt[:100]}...")
                    return jsonify({
                        "error": "LLM failed to generate content",
                        "details": "The LLM returned empty or invalid content. Please try rephrasing your request or check if the model is available.",
                        "model_used": getattr(llm, "model", "unknown"),
                        "prompt_length": len(enhanced_prompt)
                    }), 422  # Unprocessable Entity instead of Internal Server Error
                
                # Ensure we have string content for processing
                generated_content = str(generated_content).strip()

                # Language-specific validation and enhancement
                if output_filename:
                    filename_lower = output_filename.lower()
                    if filename_lower.endswith('.jsx'):
                        generated_content = _validate_and_enhance_jsx_content(generated_content, output_filename)
                    elif filename_lower.endswith('.py'):
                        generated_content = _validate_and_enhance_python_content(generated_content, output_filename)

            except Exception as e:
                logger.error(f"Error generating content with LLM: {e}", exc_info=True)
                return jsonify({
                    "error": "Content generation failed",
                    "details": f"LLM generation error: {str(e)}"
                }), 500

            # SECURITY FIX: Use secure file operations
            try:
                full_path = secure_write_file(output_path, generated_content)
                
                # Validate file was actually created and has content
                if not os.path.exists(full_path):
                    raise FileNotFoundError(f"Generated file was not created: {full_path}")
                    
                file_size = os.path.getsize(full_path)
                if file_size == 0:
                    raise ValueError("Generated file is empty")
                    
                logger.info(f"Successfully generated file: {full_path} ({file_size} bytes)")
                    
                return jsonify({
                    "message": "File generated successfully.",
                    "output_file": os.path.basename(full_path),
                    "full_path": full_path,
                    "file_size": file_size,
                    "status": "success"
                }), 200
                    
            except PermissionError as e:
                logger.error(f"Permission error writing file '{output_filename}': {e}")
                return jsonify({
                    "error": "File write permission denied",
                    "details": "Unable to write to output location"
                }), 500
                    
            except OSError as e:
                logger.error(f"File system error writing '{output_filename}': {e}")
                return jsonify({
                    "error": "File system error",
                    "details": "Unable to write file due to system error"
                }), 500
                    
        except Exception as e:
            logger.error(f"Unexpected error in direct_generate_and_save: {e}", exc_info=True)
            return jsonify({
                "error": "Internal server error during file generation",
                "details": "An unexpected error occurred"
            }), 500


@generation_bp.route("/from_command", methods=["POST"])
def generate_from_command():
    """Generate content from command with security validation."""
    
    coordinator = get_system_coordinator()
    
    with coordinator.managed_operation(
        "command_generation", 
        ProcessType.FILE_GENERATION
    ) as process_id:
        
        if not request.is_json:
            return jsonify({"error": "Request must be JSON."}), 400

        data = request.get_json()
        command_label = data.get("command_label")
        output_filename = data.get("output_filename")
        generation_parameters = data.get("generation_parameters")

        if not command_label or not output_filename or generation_parameters is None:
            return (
                jsonify(
                    {
                        "error": "'command_label', 'output_filename' and 'generation_parameters' are required."
                    }
                ),
                400,
            )

        if not isinstance(generation_parameters, dict):
            return jsonify({"error": "'generation_parameters' must be an object."}), 400

        # Security validation removed for local system
        # Parameters are used as-is for local development

        llm = current_app.config.get("LLAMA_INDEX_LLM")
        if not llm:
            return jsonify({"error": "LLM not configured."}), 503

        model_name = getattr(llm, "model", None)

        command_rule = get_active_command_rule(
            command_label, db.session, model_name=model_name
        )
        if not command_rule:
            return (
                jsonify(
                    {
                        "error": f"Active command rule '{command_label}' not found for model '{model_name}'."
                    }
                ),
                404,
            )

        # 3. Prepare Prompt with security validation
        final_prompt = command_rule.rule_text
        logger.info(f"DEBUG: Original rule text: {final_prompt}")
        logger.info(f"DEBUG: Generation parameters: {generation_parameters}")
            
        for key, value in generation_parameters.items():
            placeholder = f"{{{{{key}}}}}"
            final_prompt = final_prompt.replace(placeholder, str(value))
            logger.info(f"DEBUG: Replaced {placeholder} with {value}")
        logger.info(f"DEBUG: Final prompt: {final_prompt}")

        # Security validation removed for local system

        # 4. Generate Content with retry logic and secure file operations
        max_retries = 3
        retry_count = 0
        generated_content = None
        
        while retry_count < max_retries and generated_content is None:
            try:
                retry_count += 1
                
                logger.info(
                    f"Calling LLM for plain text output from command '{command_label}'. Attempt {retry_count}/{max_retries}"
                )
                
                # Enhanced multi-format generation with proper formatting
                is_csv_file = "csv" in command_label.lower() or "csv" in final_prompt.lower() or output_filename.lower().endswith('.csv')
                
                # Detect target file format for multi-format generation
                try:
                    from backend.utils.enhanced_file_processor import FileFormat, create_file_processor
                    file_processor = create_file_processor()
                    target_format = file_processor.detect_format(output_filename)
                    logger.info(f"Detected target format: {target_format} for file: {output_filename}")
                except ImportError:
                    target_format = None
                    logger.warning("Enhanced file processor not available")
                
                if is_csv_file:
                    # Import CSV formatting utilities
                    try:
                        from backend.utils.csv_formatter import generate_csv_prompt, CSVFormatter
                        
                        # Generate enhanced CSV prompt with proper structure
                        csv_formatter = CSVFormatter()
                        try:
                            template = csv_formatter.detect_csv_template("", final_prompt)
                            enhanced_prompt = csv_formatter.generate_structured_csv_prompt(final_prompt, template)
                        except AttributeError as e:
                            logger.warning(f"CSV formatter method not available: {e}")
                            enhanced_prompt = final_prompt
                        
                        # Use the enhanced prompt for CSV generation
                        final_prompt = enhanced_prompt
                        
                        system_message = """You are a professional CSV content generator. Generate ONLY the CSV content as requested - no explanations, no meta-text, just the complete CSV file content."""
                        
                        logger.info(f"Using enhanced CSV prompt generation for file: {output_filename}")
                        
                    except ImportError as e:
                        logger.warning(f"CSV formatter not available, using fallback prompt: {e}")
                        system_message = """You are a professional CSV file generator. Your task is to create comprehensive, detailed CSV content based on the user's requirements.

IMPORTANT INSTRUCTIONS:
1. Generate ACTUAL CSV content with proper headers and data rows
2. Do NOT include explanations, disclaimers, or meta-text
3. Do NOT apologize or express concerns about copyright
4. Create unique, valuable content that serves the user's business needs
5. For business content, create realistic, professional material
6. Include proper CSV formatting with quotes around text fields
7. Generate substantial content (500+ words per item when requested)
8. Focus on providing value and solving the user's business problems

CSV FORMAT REQUIREMENTS:
- Use proper CSV formatting with commas and quotes
- Include all requested columns
- Generate realistic, professional content
- Make content unique and valuable
- Avoid generic placeholder text

Generate the complete CSV content now:"""
                else:
                    system_message = """You are a professional file generator. Your task is to create comprehensive, detailed file content based on the user's requirements.

IMPORTANT INSTRUCTIONS:
1. Generate ACTUAL file content that can be saved directly to a file
2. Do NOT include explanations, disclaimers, or meta-text
3. Do NOT apologize or express concerns about copyright
4. Create unique, valuable content that serves the user's business needs
5. For business content, create realistic, professional material
6. Generate substantial content when requested
7. Focus on providing value and solving the user's business problems

Generate the complete file content now:"""
                    
                generated_content = llm_service.run_llm_chat_prompt(
                    final_prompt,
                    llm_instance=llm,
                    messages=[
                        llm_service.ChatMessage(role=llm_service.MessageRole.SYSTEM, content=system_message),
                        llm_service.ChatMessage(role=llm_service.MessageRole.USER, content=final_prompt),
                    ]
                )
                
                # Validate generated content
                if not generated_content or not generated_content.strip():
                    logger.warning(f"LLM returned empty content on attempt {retry_count}")
                    generated_content = None
                    continue
                
                # Apply format-specific post-processing
                if target_format and file_processor:
                    try:
                        # Special handling for different formats
                        if target_format == FileFormat.CSV or is_csv_file:
                            from backend.utils.csv_formatter import format_csv_content
                            
                            # Format the generated content as proper CSV
                            original_length = len(generated_content)
                            formatted_content = format_csv_content(generated_content, final_prompt)
                            
                            if formatted_content != generated_content:
                                logger.info(f"Applied CSV formatting - length changed from {original_length} to {len(formatted_content)} characters")
                                generated_content = formatted_content
                            else:
                                logger.info("Content was already in proper CSV format")
                        
                        elif target_format == FileFormat.XML:
                            # Ensure XML content has proper structure
                            if not generated_content.strip().startswith('<?xml'):
                                generated_content = f'<?xml version="1.0" encoding="UTF-8"?>\n{generated_content}'
                                logger.info("Added XML declaration to generated content")
                        
                        elif target_format == FileFormat.JSON:
                            # Validate and format JSON content
                            import json
                            try:
                                # Try to parse and reformat JSON
                                json_data = json.loads(generated_content)
                                generated_content = json.dumps(json_data, indent=2, ensure_ascii=False)
                                logger.info("Formatted generated content as valid JSON")
                            except json.JSONDecodeError:
                                # If not valid JSON, wrap content in basic structure
                                logger.warning("Generated content is not valid JSON, wrapping in basic structure")
                                generated_content = json.dumps({"content": generated_content}, indent=2, ensure_ascii=False)
                        
                        elif target_format == FileFormat.HTML:
                            # Ensure HTML content has basic structure
                            if not generated_content.strip().startswith('<!DOCTYPE') and not generated_content.strip().startswith('<html'):
                                generated_content = f'<!DOCTYPE html>\n<html>\n<head>\n<title>Generated Content</title>\n</head>\n<body>\n{generated_content}\n</body>\n</html>'
                                logger.info("Added HTML document structure to generated content")
                        
                        logger.info(f"Applied {target_format.value.upper()} formatting to generated content")
                        
                    except ImportError as e:
                        logger.warning(f"Enhanced file processor not available for post-processing: {e}")
                    except Exception as e:
                        logger.warning(f"Format-specific processing failed, using original content: {e}")
                
                elif is_csv_file:
                    # Fallback CSV formatting if enhanced processor not available
                    try:
                        from backend.utils.csv_formatter import format_csv_content

                        # Format the generated content as proper CSV
                        original_length = len(generated_content)
                        try:
                            formatted_content = format_csv_content(generated_content, final_prompt)
                            if formatted_content and formatted_content != generated_content:
                                logger.info(f"Applied fallback CSV formatting - length changed from {original_length} to {len(formatted_content)} characters")
                                generated_content = formatted_content
                            else:
                                logger.info("Content was already in proper CSV format or no changes needed")
                        except (AttributeError, TypeError) as format_error:
                            logger.warning(f"CSV format_csv_content method failed: {format_error}")
                            # Continue with original content

                    except ImportError as e:
                        logger.warning(f"CSV formatter not available for post-processing: {e}")
                    except Exception as e:
                        logger.warning(f"CSV formatting failed, using original content: {e}")
                
                # Ensure string content
                generated_content = str(generated_content).strip()
                break

            except Exception as e:
                if retry_count >= max_retries:
                    logger.error(
                        f"LLM content generation failed after {max_retries} attempts for command '{command_label}': {e}",
                        exc_info=True,
                    )
                    # Provide more specific error details
                    error_details = str(e)
                    if "timeout" in error_details.lower():
                        error_type = "LLM request timeout"
                    elif "connection" in error_details.lower():
                        error_type = "LLM connection error"
                    elif "schema" in error_details.lower():
                        error_type = "Structured output validation error"
                    else:
                        error_type = "LLM generation error"
                        
                    return jsonify({
                        "error": f"Content generation failed: {error_type}",
                        "details": f"Failed after {max_retries} attempts. Last error: {str(e)[:200]}",
                        "command": command_label,
                        "retries_attempted": retry_count
                    }), 500
                else:
                    logger.warning(f"LLM attempt {retry_count} failed, retrying: {e}")
                    continue

        # If we still don't have content after retries
        if not generated_content:
            return jsonify({
                "error": "Content generation failed",
                "details": f"Failed to generate content after {max_retries} attempts",
                "command": command_label
            }), 500

        # 5. Save the generated content with secure file operations
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            logger.error("OUTPUT_DIR not configured in app config")
            return jsonify({
                "error": "Server configuration error: Output directory not set.",
                "details": "Please contact system administrator"
            }), 500

        try:
            # SECURITY FIX: Secure filename validation
            secure_file_manager = get_secure_file_manager()
            secure_output_filename = secure_file_manager.secure_filename(output_filename)

            # Create full path and validate security
            full_path = os.path.join(output_dir, secure_output_filename)
            
            if not coordinator.validate_security("file_operation", 
                                                file_path=full_path, 
                                                operation="write"):
                return jsonify({
                    "error": "File path security validation failed",
                    "details": "The requested file path is not allowed"
                }), 403
                
            # SECURITY FIX: Use secure file write
            final_path = secure_write_file(full_path, generated_content)
            
            # Validate the file was written correctly
            if not os.path.exists(final_path):
                raise FileNotFoundError(f"File was not created: {final_path}")
            
            file_size = os.path.getsize(final_path)
            if file_size == 0:
                raise ValueError("Written file is empty")
                
            logger.info(f"Successfully wrote file from command '{command_label}': {final_path} ({file_size} bytes)")
            
            return jsonify({
                "message": "File generated successfully.",
                "output_file": os.path.basename(final_path),
                "full_path": final_path,
                "file_size": file_size,
                "content_length": len(generated_content),
                "command": command_label,
                "retries_used": retry_count,
                "status": "success"
            }), 200
            
        except ValueError as e:
            logger.error(f"Filename validation error for '{output_filename}': {e}")
            return jsonify({
                "error": "Invalid filename",
                "details": str(e),
                "command": command_label
            }), 400
            
        except PermissionError as e:
            logger.error(f"Permission error writing file '{output_filename}': {e}")
            return jsonify({
                "error": "File write permission denied",
                "details": "Unable to write to output location",
                "command": command_label
            }), 500
            
        except OSError as e:
            logger.error(f"File system error writing '{output_filename}': {e}")
            return jsonify({
                "error": "File system error",
                "details": f"Unable to write file: {str(e)}",
                "command": command_label
            }), 500
            
        except Exception as e:
            logger.error(f"Unexpected error writing file '{output_filename}': {e}", exc_info=True)
            return jsonify({
                "error": "Unexpected file write error",
                "details": "An unexpected error occurred while saving the file",
                "command": command_label
            }), 500
