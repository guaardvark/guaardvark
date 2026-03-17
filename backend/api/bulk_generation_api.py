#!/usr/bin/env python3
"""
Bulk Generation API
High-performance API endpoints for bulk CSV content generation
Supports 1,000+ pages per day generation capability
Version 2.0: Enhanced with Context Variables System
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from backend.models import Task, db, get_active_model_name
from backend.utils.bulk_csv_generator import (
    BulkCSVGenerator,
    GenerationTask,
    create_tasks_from_topics,
    create_data_center_topics,
    create_demonstration_csv
)
from backend.utils.bulk_xml_generator import BulkXMLGenerator, generate_xml_file
from backend.utils.unified_progress_system import get_unified_progress, ProcessType
from backend.utils.context_variables import context_manager
from backend.utils.db_utils import ensure_db_session_cleanup
from backend.utils.tracking_analyzer import TrackingAnalyzer

bulk_gen_bp = Blueprint("bulk_generation_api", __name__, url_prefix="/api/bulk-generate")
logger = logging.getLogger(__name__)

def get_unique_filename(output_dir: str, filename: str) -> str:
    """
    Generate a unique filename by adding sequence number if file already exists
    Example: filename.csv -> filename-001.csv, filename-002.csv, etc.
    """
    base_path = os.path.join(output_dir, filename)

    # If file doesn't exist, return original filename
    if not os.path.exists(base_path):
        return filename

    # Split filename and extension
    name, ext = os.path.splitext(filename)

    # Find next available sequence number
    counter = 1
    while True:
        sequence_filename = f"{name}-{counter:03d}{ext}"
        sequence_path = os.path.join(output_dir, sequence_filename)

        if not os.path.exists(sequence_path):
            return sequence_filename

        counter += 1

        # Safety check to prevent infinite loop
        if counter > 999:
            import time
            timestamp = int(time.time())
            return f"{name}-{timestamp}{ext}"

@dataclass
class EnhancedGenerationTask:
    """Enhanced generation task with prompt rule integration"""
    item_id: str
    topic: str
    client: str
    project: str
    website: str
    prompt_rule: Optional[object] = None
    target_word_count: int = 500
    # BUG FIX: Add insert_content and insert_position support
    insert_content: Optional[str] = None
    insert_position: Optional[str] = None
    # ENHANCEMENT: Add client_notes, project_notes, competitor_url for full context
    client_notes: Optional[str] = None
    project_notes: Optional[str] = None
    competitor_url: Optional[str] = None
    # FIX: Add actual client metadata fields from database
    client_phone: Optional[str] = None
    client_email: Optional[str] = None
    client_location: Optional[str] = None
    contact_url: Optional[str] = None
    primary_service: Optional[str] = None
    secondary_service: Optional[str] = None
    brand_tone: Optional[str] = None
    business_hours: Optional[str] = None
    # RAG Enhancement fields
    industry: Optional[str] = None
    target_audience: Optional[str] = None
    unique_selling_points: Optional[str] = None
    content_goals: Optional[str] = None
    brand_voice_examples: Optional[str] = None
    regulatory_constraints: Optional[str] = None
    geographic_coverage: Optional[str] = None
    keywords: Optional[list] = None
    competitor_urls_list: Optional[list] = None
    # Pre-fetched RAG document context (populated in main thread before background generation)
    document_context: Optional[str] = None

    def get_context_variables(self) -> dict:
        """Get context variables for prompt substitution"""
        return {
            "client": self.client,
            "project": self.project,
            "website": self.website,
            "topic": self.topic,
            "word_count": self.target_word_count,
            "item_id": self.item_id,
            "row_id": self.item_id,  # Alternative placeholder
            "client_notes": self.client_notes or "",
            "project_notes": self.project_notes or "",
            "competitor": self.competitor_url or "",
            "competitor_url": self.competitor_url or "",
            "target_website": self.competitor_url or "",
            # Add real client metadata to context variables
            "phone": self.client_phone or "",
            "client_phone": self.client_phone or "",
            "email": self.client_email or "",
            "client_email": self.client_email or "",
            "location": self.client_location or "",
            "client_location": self.client_location or "",
            "city": (self.client_location or "").split(",")[0].strip() if self.client_location else "",
            "contact_url": self.contact_url or "",
            "primary_service": self.primary_service or "",
            "secondary_service": self.secondary_service or "",
            "brand_tone": self.brand_tone or "professional",
            "business_hours": self.business_hours or "",
            # RAG Enhancement fields
            "industry": self.industry or "",
            "target_audience": self.target_audience or "",
            "unique_selling_points": self.unique_selling_points or "",
            "content_goals": self.content_goals or "",
            "brand_voice_examples": self.brand_voice_examples or "",
            "regulatory_constraints": self.regulatory_constraints or "",
            "geographic_coverage": self.geographic_coverage or "",
            "keywords": ", ".join(self.keywords) if self.keywords else "",
            "competitor_urls": ", ".join(self.competitor_urls_list) if self.competitor_urls_list else "",
            "document_context": self.document_context or "",
        }

class EnhancedBulkCSVGenerator(BulkCSVGenerator):
    """Enhanced CSV generator with prompt rule integration and RAG support"""

    def __init__(self, output_dir: str, concurrent_workers: int = 5, batch_size: int = 50,
                 target_word_count: int = 500, prompt_rule: Optional[object] = None,
                 unified_progress_system=None, job_id=None, model_name: Optional[str] = None, **kwargs):
        # BUG FIX: Pass model_name to parent so it uses the correct LLM model
        super().__init__(output_dir, concurrent_workers, batch_size, target_word_count,
                        unified_progress_system=unified_progress_system, job_id=job_id,
                        model_name=model_name, **kwargs)
        self.prompt_rule = prompt_rule
        self._setup_rag_retriever()

    def _setup_rag_retriever(self):
        """Setup RAG retriever for enhanced content generation

        BUG FIX: Added explicit handling for Flask app context issues.
        When running in background threads, current_app may not be available.
        RAG retrieval is now optional and will gracefully skip if unavailable.
        """
        self.index = None
        self.retriever = None

        try:
            from flask import current_app, has_app_context

            # BUG FIX: Check if we have an app context before accessing current_app
            if not has_app_context():
                logger.info("No Flask app context - RAG retriever not available for background generation")
                return

            self.index = current_app.config.get("LLAMA_INDEX_INDEX")
            self.retriever = self.index.as_retriever() if self.index else None

            if self.retriever:
                logger.info("RAG retriever initialized for enhanced CSV generation")
            else:
                logger.info("No RAG index available - using standard generation (this is OK)")
        except RuntimeError as e:
            # RuntimeError: Working outside of application context
            logger.info(f"RAG retriever not available (no app context): {e}")
            self.retriever = None
        except Exception as e:
            logger.warning(f"Failed to setup RAG retriever: {e}")
            self.retriever = None
    
    def _generate_single_content(self, task):
        """Generate content using prompt rule and RAG context"""
        import time as _time
        try:
            logger.info(f"[DEBUG] _generate_single_content START for task {task.item_id}")

            if not self.llm:
                logger.error(f"LLM instance not available for task {task.item_id}")
                return None

            # Build enhanced prompt with rule and RAG context
            logger.info(f"[DEBUG] Building enhanced prompt for task {task.item_id}")
            _prompt_start = _time.time()
            enhanced_prompt = self._build_enhanced_prompt(task)
            logger.info(f"[DEBUG] Prompt built in {_time.time() - _prompt_start:.2f}s for task {task.item_id}")

            # Generate content using enhanced prompt
            from backend.utils.llm_service import ChatMessage, MessageRole

            system_prompt = "You are a professional content generator. Follow the specific prompt rules and use the provided context to generate high-quality CSV content."
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
                ChatMessage(role=MessageRole.USER, content=enhanced_prompt),
            ]

            logger.info(f"[DEBUG] Calling LLM.chat() for task {task.item_id} (prompt length: {len(enhanced_prompt)} chars)")
            _llm_start = _time.time()
            llm_response = self.llm.chat(messages)
            logger.info(f"[DEBUG] LLM.chat() completed in {_time.time() - _llm_start:.2f}s for task {task.item_id}")
            
            # Extract content from chat response
            if llm_response and hasattr(llm_response, 'message') and llm_response.message:
                try:
                    response_text = llm_response.message.content
                except (ValueError, AttributeError):
                    blocks = getattr(llm_response.message, 'blocks', [])
                    response_text = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
            else:
                response_text = str(llm_response) if llm_response else ""
                
            if not response_text or not response_text.strip():
                self._record_error(task.item_id, "empty_response", "Empty or null response", f"Response text is empty. LLM response: {llm_response}", can_retry=True)
                return None
            
            # Parse the response into a CSV row
            return self._parse_csv_response(response_text, task)
            
        except Exception as e:
            logger.error(f"Error generating enhanced content for task {task.item_id}: {e}", exc_info=True)
            return None
    
    def _build_enhanced_prompt(self, task):
        """Build enhanced prompt with rule text, RAG context, and placeholders"""
        import time as _time
        logger.info(f"[DEBUG] _build_enhanced_prompt START for task {task.item_id}")

        # Start with rule text if available
        _tmpl_start = _time.time()
        base_prompt = self._get_enhanced_fallback_template()  # Default fallback
        logger.info(f"[DEBUG] Fallback template loaded in {_time.time() - _tmpl_start:.2f}s")

        if self.prompt_rule:
            try:
                # Safely extract rule information to avoid DetachedInstanceError
                from backend.models import Rule, db
                
                # First try to get rule_id from the object safely
                rule_id = None
                rule_text = None
                rule_name = None
                
                # Handle detached instance safely - access attributes directly since we expunged it
                if hasattr(self.prompt_rule, 'id'):
                    try:
                        # Access attributes directly since the object was expunged from session
                        rule_id = self.prompt_rule.id
                        rule_text = self.prompt_rule.rule_text if hasattr(self.prompt_rule, 'rule_text') else None
                        rule_name = self.prompt_rule.name if hasattr(self.prompt_rule, 'name') else None
                    except Exception as direct_error:
                        logger.debug(f"Could not access rule attributes directly: {direct_error}")
                        # Try to reload from DB if we at least got the ID
                        try:
                            rule_id = getattr(self.prompt_rule, 'id', None)
                            if rule_id:
                                fresh_rule = db.session.get(Rule, rule_id)
                                if fresh_rule:
                                    rule_text = fresh_rule.rule_text
                                    rule_name = fresh_rule.name
                        except Exception as reload_error:
                            logger.debug(f"Could not reload rule from DB: {reload_error}")
                            rule_id = None
                            rule_text = None
                            rule_name = None
                
                # Use the rule text if we successfully got it
                if rule_text:
                    base_prompt = rule_text
                    logger.info(f"Using prompt rule: {rule_name or 'Unknown'}")
                else:
                    logger.info("No rule text available, using fallback template")
                    
            except Exception as e:
                logger.warning(f"Error processing prompt rule: {e}")
                logger.info("Using enhanced fallback template due to rule processing error")
        
        # Get RAG context
        logger.info(f"[DEBUG] Calling _get_rag_context for task {task.item_id}")
        _rag_start = _time.time()
        rag_context = self._get_rag_context(task)
        logger.info(f"[DEBUG] _get_rag_context returned in {_time.time() - _rag_start:.2f}s")
        
        # Replace placeholders with task context
        context_vars = task.get_context_variables() if hasattr(task, 'get_context_variables') else {
            "client": getattr(task, 'client', 'Professional Services'),
            "project": getattr(task, 'project', 'Content Generation'),
            "website": getattr(task, 'website', 'website.com'),
            "topic": getattr(task, 'topic', 'content topic'),
            "word_count": getattr(task, 'target_word_count', self.target_word_count),
            "item_id": getattr(task, 'item_id', 'unknown')
        }
        
        # Add RAG context to variables
        context_vars["rag_context"] = rag_context
        context_vars["context"] = rag_context  # Alternative placeholder
        
        # Replace placeholders in prompt
        enhanced_prompt = base_prompt
        for key, value in context_vars.items():
            placeholder = "{" + key.upper() + "}"
            enhanced_prompt = enhanced_prompt.replace(placeholder, str(value))
            placeholder_lower = "{" + key.lower() + "}"
            enhanced_prompt = enhanced_prompt.replace(placeholder_lower, str(value))
        
        return enhanced_prompt
    
    def _get_rag_context(self, task) -> str:
        """Get relevant RAG context and entity relationships for the task

        BUG FIX: Added timeout protection and thread-safety checks to prevent
        blocking during background CSV generation. RAG retrieval can use Ollama
        embeddings which may block indefinitely if there are GPU contention issues.
        """
        import threading
        import concurrent.futures

        context_parts = []

        # Use pre-fetched context if available (populated in main thread before background spawn)
        if getattr(task, 'document_context', None):
            logger.info(f"Using pre-fetched RAG context for task {task.item_id}")
            return task.document_context

        # BUG FIX: Skip RAG retrieval in background threads to prevent blocking
        # The retriever uses Ollama embeddings which can cause GPU contention
        # and block indefinitely when running alongside LLM generation
        is_background_thread = threading.current_thread() is not threading.main_thread()

        if is_background_thread:
            logger.info(f"Skipping RAG context retrieval for task {task.item_id} (background thread, no pre-fetch)")
            return "Generate content based on business profile above."

        # Get RAG indexed document context with timeout protection
        if self.retriever:
            try:
                # Build context query from task details
                query_parts = [
                    f"Generate content about: {task.topic}",
                    f"Client: {task.client}",
                    f"Project: {task.project}",
                    f"Website: {task.website}"
                ]
                context_query = ". ".join(query_parts)

                # BUG FIX: Add timeout protection to retrieval
                # RAG retrieval can block if embedding model is Ollama-based
                def retrieve_with_timeout():
                    return self.retriever.retrieve(context_query)

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(retrieve_with_timeout)
                    try:
                        context_nodes = future.result(timeout=30)  # 30 second timeout
                        if context_nodes:
                            rag_context = "\n".join([
                                f"Document Context {i+1}: {node.node.get_content(metadata_mode='all').strip()}"
                                for i, node in enumerate(context_nodes[:3])  # Limit to 3 snippets
                            ])
                            context_parts.append(rag_context)
                            logger.info(f"Retrieved {len(context_nodes)} RAG context nodes for task {task.item_id}")
                    except concurrent.futures.TimeoutError:
                        logger.warning(f"RAG context retrieval timed out for task {task.item_id}")
            except Exception as e:
                logger.warning(f"RAG context retrieval failed: {e}")

        # BUG FIX: Skip entity context enhancement in background threads
        # EntityContextEnhancer uses db.session which can fail/block without Flask app context
        if not is_background_thread:
            try:
                from backend.utils.entity_context_enhancer import EntityContextEnhancer
                enhancer = EntityContextEnhancer()

                # Build query for entity extraction
                entity_query = f"{task.client} {task.project} {task.topic}"
                enhanced_data = enhancer.enhance_query_context(entity_query, [])

                if enhanced_data and enhanced_data.get('relationship_summary'):
                    entity_context = f"Entity Relationships:\n{enhanced_data['relationship_summary']}"
                    context_parts.append(entity_context)
                    logger.info(f"Added entity relationship context for task {task.item_id}")
            except Exception as e:
                logger.debug(f"Entity context enhancement skipped: {e}")

        # Return combined context or fallback message
        if context_parts:
            return "\n\n".join(context_parts)
        else:
            return "Generate content based on business profile above."
    
    def _get_enhanced_fallback_template(self) -> str:
        """Enhanced fallback template with full business context support"""
        return """Generate exactly ONE CSV row with 7 comma-separated fields.

**Business Context:**
- Company: {client}
- Project: {project}
- Website: {website}
- Primary Service: {primary_service}
- Brand Tone: {brand_tone}

**Content Topic:** {topic}

**CSV FORMAT - CRITICAL INSTRUCTIONS:**
Output format: "ID","Title","Content","Excerpt","Category","Tags","slug"

**CSV ESCAPING RULES - MUST FOLLOW:**
1. Each field MUST be wrapped in double quotes
2. Inside the Content field, use SINGLE quotes for HTML attributes
3. DO NOT use double quotes inside any field
4. DO NOT use commas inside field values (use semicolons or dashes instead)
5. Keep content on ONE line - no line breaks

**CORRECT Example:**
"{row_id}","Title Here","<p><strong>Content here</strong> More text.</p>","Short excerpt","Category Name","tag1|tag2|tag3","slug-here"

**WRONG Examples (DO NOT DO THIS):**
- "task_001","Title with "quotes" inside" - WRONG (hardcoded ID + quotes inside field)
- Using "task_001" or "task_002" etc - WRONG (MUST use {row_id} placeholder)
- Content field with class="foo" - WRONG (use class='foo' instead)
- Multi-line content - WRONG (keep on one line) DO NOT use line breaks in the content.
- Using the client name in the title - WRONG (DO NOT use the client name in the title)



**Field Requirements:**
- ID: "{row_id}" (exact value)
- Title: 50-70 characters, SEO-optimized about {topic}. DO NOT use the client name in the title.
  * IMPORTANT: Write about {topic} ONLY - do NOT write about legal services, law firms, litigation, or legal topics
- Content: MINIMUM {word_count} words about {topic}.
  * Use HTML: <h2>, <h3>, <p>, <strong>, <em>, <ul>, <li>
  * Use SINGLE quotes for all HTML attributes: class='foo' NOT class="foo"
  * Keep all content on ONE continuous line. DO NOT use line breaks in the content.
  * Focus EXCLUSIVELY on {topic} and {primary_service}
  * FORBIDDEN: Do NOT write about legal, law, attorney, lawyer, litigation, legal services, or any related terms
- Excerpt: 150-165 characters (plain text, no HTML) about {topic}.
- Category: ONE simple category based on {topic} (e.g. "SEO Services" or "Web Development")
  * Do not include special characters in the category.
- Tags: 3-5 specific keywords separated by commas about {topic}.
  * Focus on specific topics related to {topic}
  * Example: "search optimization, website ranking, SEO strategy"
- slug: COMPLETE lowercase-with-hyphens (full title, no truncation)
- DO NOT use the client name in the title, excerpt, category, tags, or slug.


**Output ONLY the CSV row - nothing else. No explanations, no extra text.**"""

@bulk_gen_bp.route("/csv-proven", methods=["POST"])
def generate_bulk_csv_proven():
    """
    Generate bulk CSV content using the proven batch generation method.
    Uses the same approach as the successful large-scale generation pipeline.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON."}), 400

        # Extract parameters with proper validation
        output_filename = data.get("output_filename")
        client_name = data.get("client_name", data.get("client", "Professional Services"))
        project_name = data.get("project_name", data.get("project", "Content Generation"))
        client_website = data.get("client_website", data.get("website", "website.com"))
        target_website = data.get("target_website", None)  # Competitor site for reference (optional)
        topics = data.get("topics", [])
        num_items = data.get("num_items", len(topics) if topics else 10)
        target_word_count = data.get("target_word_count", 500)
        model_name = data.get("model_name")  # Model from FileGenerationPage

        if not output_filename:
            return jsonify({"error": "output_filename is required"}), 400

        if not topics and num_items <= 0:
            return jsonify({"error": "Either topics list or num_items > 0 is required"}), 400
            
        logger.info(f"Proven CSV generation: {num_items} items for {client_name}, target: {target_website}")

        # Secure filename
        secure_output_filename = secure_filename(output_filename)
        if not secure_output_filename.lower().endswith(".csv"):
            secure_output_filename += ".csv"

        # Get output directory
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            return jsonify({"error": "Server configuration error: Output directory not set"}), 500

        # Generate unique filename with sequence number if needed
        secure_output_filename = get_unique_filename(output_dir, secure_output_filename)

        # Create job ID
        import time
        job_id = f"proven_gen_{int(time.time())}"
        
        # Start async generation using our proven method
        from backend.celery_app import celery
        
        task_result = celery.send_task('backend.tasks.proven_csv_generation.generate_proven_csv_task', [
            topics,
            secure_output_filename,
            client_name,
            project_name,
            client_website,
            target_website,
            target_word_count,
            job_id,
            model_name
        ])
        
        return jsonify({
            "message": "Proven CSV generation started successfully",
            "job_id": job_id,
            "task_id": task_result.id,
            "output_filename": secure_output_filename,
            "items_count": num_items,
            "details": f"Generating {num_items} pages for {client_name} using proven method"
        })

    except Exception as e:
        logger.error(f"Error in proven CSV generation: {e}", exc_info=True)
        return jsonify({
            "error": "CSV generation failed",
            "details": str(e)
        }), 500

@bulk_gen_bp.route("/csv", methods=["POST"])
def generate_bulk_csv():
    """
    Generate bulk CSV content based on topics and specifications
    Database-free version to avoid transaction issues
    """

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON."}), 400

        # Extract parameters
        output_filename = data.get("output_filename")
        client = data.get("client", "Professional Services")
        project = data.get("project", "Content Generation")
        website = data.get("website") or "website.com"
        # FIX BUG #10: Validate and handle None values for optional parameters
        competitor_url = data.get("competitor_url") or ""  # Competitor website for analysis
        client_notes = data.get("client_notes") or ""  # Client business description
        project_notes = data.get("project_notes") or ""  # Project-specific notes/context
        topics = data.get("topics", [])
        num_items = data.get("num_items", 10)
        concurrent_workers = data.get("concurrent_workers", 5)
        target_word_count = data.get("target_word_count", 500)
        batch_size = data.get("batch_size", 25)
        prompt_rule_id = data.get("prompt_rule_id")  # From FileGenerationPage
        model_name = data.get("model_name") or get_active_model_name()  # Use provided model or active model
        existing_task_id = data.get("existing_task_id")  # Optional existing task to update instead of creating new one

        # BUG FIX #1: Add missing enhanced context parameters
        context_mode = data.get("context_mode", "basic")  # 'basic', 'enhanced', or 'auto'
        use_entity_context = data.get("use_entity_context", False)
        use_competitor_analysis = data.get("use_competitor_analysis", False)
        use_document_intelligence = data.get("use_document_intelligence", False)
        client_id = data.get("client_id")  # Required for enhanced context
        project_id = data.get("project_id")  # Project ID for task linking
        website_id = data.get("website_id")  # Website ID for task linking

        # BUG FIX: Add missing insert_content and insert_position parameters
        insert_content = data.get("insert_content")  # Content to insert into each page
        insert_position = data.get("insert_position")  # Position: 'top', 'bottom', 'none'

        # FIX: Fetch real client metadata from database instead of using placeholders
        client_phone = None
        client_email = None
        client_location = None
        contact_url = None
        primary_service = None
        secondary_service = None
        brand_tone = "professional"
        business_hours = None

        # RAG Enhancement fields - Initialize
        industry = None
        target_audience = None
        unique_selling_points = None
        content_goals = None
        brand_voice_examples = None
        regulatory_constraints = None
        geographic_coverage = None
        keywords = []
        competitor_urls = []

        # Add Project and Website metadata variables
        project_description = None
        website_url = None

        try:
            from backend.models import Client, Project, Website, db
            # Try to find client by name (exact match first)
            client_obj = db.session.query(Client).filter_by(name=client).first()

            # FIX: Try case-insensitive search if exact match fails
            if not client_obj:
                client_obj = db.session.query(Client).filter(Client.name.ilike(client)).first()
                if client_obj:
                    logger.info(f"Found client with case-insensitive match: '{client}' -> '{client_obj.name}'")

            if client_obj:
                client_phone = client_obj.phone
                client_email = client_obj.email
                client_location = client_obj.location
                contact_url = client_obj.contact_url
                primary_service = client_obj.primary_service
                secondary_service = client_obj.secondary_service
                brand_tone = client_obj.brand_tone or "professional"
                business_hours = client_obj.business_hours
                # Use client notes from database if not provided
                if not client_notes and client_obj.notes:
                    client_notes = client_obj.notes

                # RAG Enhancement fields
                industry = client_obj.industry
                target_audience = client_obj.target_audience
                unique_selling_points = client_obj.unique_selling_points
                content_goals = client_obj.content_goals
                brand_voice_examples = client_obj.brand_voice_examples
                regulatory_constraints = client_obj.regulatory_constraints
                geographic_coverage = client_obj.geographic_coverage

                # Parse JSON fields
                import json
                keywords = []
                if client_obj.keywords:
                    try:
                        keywords = json.loads(client_obj.keywords)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        keywords = []

                competitor_urls = []
                if client_obj.competitor_urls:
                    try:
                        competitor_urls = json.loads(client_obj.competitor_urls)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        competitor_urls = []

                logger.info(f"Loaded client metadata for '{client}': industry={industry}, services={primary_service}, {secondary_service}, keywords={len(keywords)}")

                # Load Project metadata
                try:
                    project_obj = db.session.query(Project).filter_by(name=project, client_id=client_obj.id).first()
                    if not project_obj:
                        project_obj = db.session.query(Project).filter(Project.name.ilike(project), Project.client_id == client_obj.id).first()
                    if project_obj:
                        project_description = project_obj.description
                        if not project_notes and project_description:
                            project_notes = project_description
                        logger.info(f"Loaded project: '{project}'")
                except Exception as proj_err:
                    logger.debug(f"No project found: {proj_err}")

                # Load Website metadata
                try:
                    if website and website != "Unknown":
                        website_obj = db.session.query(Website).filter(Website.url.ilike(f"%{website}%")).first()
                        if website_obj:
                            website_url = website_obj.url
                            logger.info(f"Loaded website: {website_url}")
                except Exception as web_err:
                    logger.debug(f"No website found: {web_err}")
            else:
                logger.warning(f"Client '{client}' not found in database (tried exact and case-insensitive) - using defaults")
        except Exception as e:
            logger.error(f"Error fetching entity metadata: {e}")
            logger.info("Using default metadata")

        if not output_filename:
            return jsonify({"error": "output_filename is required"}), 400

        if not topics and num_items <= 0:
            return jsonify({"error": "Either topics list or num_items > 0 is required"}), 400
            
        # Log generation parameters
        logger.info(f"Enhanced CSV generation: {num_items} items, {len(topics)} topics, prompt_rule_id: {prompt_rule_id}")

        # Secure filename
        secure_output_filename = secure_filename(output_filename)
        if not secure_output_filename:
            return jsonify({"error": "Invalid filename after security processing"}), 400
        if not secure_output_filename.lower().endswith(".csv"):
            secure_output_filename += ".csv"

        # Get output directory
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            return jsonify({"error": "Server configuration error: Output directory not set"}), 500

        # Generate unique filename with sequence number if needed
        secure_output_filename = get_unique_filename(output_dir, secure_output_filename)

        # Create enhanced generation tasks using prompt rule system
        tasks = []
        logger.info(f"Creating {num_items} enhanced generation tasks")
        
        # Get prompt rule - use WordPress rule by default if not specified
        prompt_rule = None
        try:
            from backend.models import db, Rule
            if prompt_rule_id:
                prompt_rule = db.session.query(Rule).filter_by(id=prompt_rule_id, is_active=True).first()
                if prompt_rule:
                    # Detach from session to avoid session binding issues
                    db.session.expunge(prompt_rule)
                    logger.info(f"Using specified prompt rule: {prompt_rule.name} (ID: {prompt_rule_id})")
                else:
                    logger.warning(f"Prompt rule ID {prompt_rule_id} not found or inactive")
            else:
                # Use Enhanced WordPress rule by default (Rule #19)
                prompt_rule = db.session.query(Rule).filter_by(name="Enhanced WordPress CSV Generation", is_active=True).first()
                if prompt_rule:
                    # Detach from session to avoid session binding issues
                    db.session.expunge(prompt_rule)
                    logger.info(f"Using enhanced WordPress rule: {prompt_rule.name} (ID: {prompt_rule.id})")
                else:
                    # Fallback to old WordPress rule if enhanced not found
                    prompt_rule = db.session.query(Rule).filter_by(name="WordPress CSV Generation", is_active=True).first()
                    if prompt_rule:
                        # Detach from session to avoid session binding issues
                        db.session.expunge(prompt_rule)
                        logger.info(f"Using fallback WordPress rule: {prompt_rule.name} (ID: {prompt_rule.id})")
                    else:
                        logger.warning("No WordPress rules found")
        except Exception as e:
            logger.error(f"Error fetching prompt rule: {e}")
        
        # Generate website-scoped IDs
        website_key = website.replace('http://', '').replace('https://', '').split('/')[0]
        website_prefix = website_key.replace('.', '-').replace('_', '-')[:20]

        for i in range(num_items):
            # Use topics if provided, otherwise generate contextual topics
            if topics and len(topics) > 0:
                topic_index = i % len(topics)
                base_topic = topics[topic_index]
            else:
                base_topic = f"{project} content item {i+1}"

            # Generate proper website-scoped ID
            page_id = f"{website_prefix}-{i+1:03d}"

            # Create enhanced task with prompt rule context
            task = EnhancedGenerationTask(
                item_id=page_id,
                topic=base_topic,
                client=client,
                project=project,
                website=website,
                prompt_rule=prompt_rule,
                target_word_count=target_word_count,
                # BUG FIX: Pass insert_content and insert_position parameters
                insert_content=insert_content,
                insert_position=insert_position,
                # ENHANCEMENT: Pass client_notes, project_notes, competitor_url for full context
                client_notes=client_notes,
                project_notes=project_notes,
                competitor_url=competitor_url,
                # FIX: Pass real client metadata instead of using placeholders
                client_phone=client_phone,
                client_email=client_email,
                client_location=client_location,
                contact_url=contact_url,
                primary_service=primary_service,
                secondary_service=secondary_service,
                brand_tone=brand_tone,
                business_hours=business_hours,
                # RAG Enhancement fields
                industry=industry,
                target_audience=target_audience,
                unique_selling_points=unique_selling_points,
                content_goals=content_goals,
                brand_voice_examples=brand_voice_examples,
                regulatory_constraints=regulatory_constraints,
                geographic_coverage=geographic_coverage,
                keywords=keywords,
                competitor_urls_list=competitor_urls
            )
            # BUG FIX #2: Add context_mode attribute to task for template selection
            task.context_mode = context_mode
            task.use_entity_context = use_entity_context
            task.use_competitor_analysis = use_competitor_analysis
            task.use_document_intelligence = use_document_intelligence
            tasks.append(task)

        # Pre-fetch RAG document context in main thread before spawning background generation.
        # This avoids GPU contention that occurs when embeddings run alongside LLM generation.
        if use_document_intelligence:
            try:
                index = current_app.config.get("LLAMA_INDEX_INDEX")
                if index:
                    retriever = index.as_retriever(similarity_top_k=3)
                    logger.info(f"Pre-fetching RAG context for {len(tasks)} tasks in main thread")
                    for task in tasks:
                        try:
                            query = " ".join(filter(None, [task.topic, task.client, task.primary_service]))
                            nodes = retriever.retrieve(query)
                            if nodes:
                                task.document_context = "\n\n".join([
                                    f"Reference Document {i+1}:\n{node.node.get_content(metadata_mode='all').strip()}"
                                    for i, node in enumerate(nodes[:3])
                                ])
                                logger.info(f"Pre-fetched {len(nodes)} RAG nodes for task {task.item_id}")
                        except Exception as e:
                            logger.warning(f"RAG pre-fetch failed for task {task.item_id}: {e}")
                else:
                    logger.info("RAG index not available for pre-fetch, skipping document context")
            except Exception as e:
                logger.warning(f"RAG pre-fetch setup failed: {e}")

        # Log task creation
        logger.info(f"Created {len(tasks)} enhanced generation tasks with prompt rule integration")

        # Simple job ID without database
        import time
        job_id = f"bulk_gen_{int(time.time())}"
        logger.info(f"Starting bulk CSV generation. Job ID: {job_id}")
        
        # Use existing task or create new database Task for job management
        database_task = None
        try:
            if existing_task_id:
                # Update existing task instead of creating new one
                database_task = db.session.get(Task, existing_task_id)
                if database_task:
                    database_task.job_id = job_id
                    database_task.status = "pending"
                    database_task.model_name = get_active_model_name()
                    database_task.workflow_config = json.dumps({
                        "type": "bulk_csv_generation",
                        "items_count": len(tasks),
                        "client": client,
                        "project": project,
                        "website": website,
                        "target_word_count": target_word_count
                    })
                    db.session.commit()
                    logger.info(f"Updated existing Task {database_task.id} for bulk CSV job {job_id}")
                else:
                    logger.warning(f"Existing task {existing_task_id} not found, creating new task")
                    existing_task_id = None  # Fall through to create new task
            
            if not existing_task_id:
                # Create new task if no existing task provided or found
                # FIX BUG #17: Use model_name parameter if provided, otherwise get active
                # BUG FIX: Populate all Task fields for proper display on TaskPage
                database_task = Task(
                    name=f"Bulk CSV Generation - {client}",
                    description=f"Generating {len(tasks)} CSV items for {project} ({website})",
                    status="pending",
                    type="file_generation",
                    job_id=job_id,  # Link the unified progress job_id
                    output_filename=secure_output_filename,
                    model_name=model_name if model_name else get_active_model_name(),
                    # BUG FIX: Populate entity IDs and metadata fields for TaskPage display
                    client_id=client_id,
                    project_id=project_id,
                    website_id=website_id,
                    client_name=client,
                    target_website=website,
                    competitor_url=competitor_url if competitor_url else None,
                    prompt_text=f"Bulk CSV generation for {client} - {num_items} items",
                    workflow_config=json.dumps({
                        "type": "bulk_csv_generation",
                        "items_count": len(tasks),
                        "client": client,
                        "project": project,
                        "website": website,
                        "target_word_count": target_word_count,
                        "concurrent_workers": concurrent_workers,
                        "batch_size": batch_size,
                        "competitor_url": competitor_url,
                        "context_mode": context_mode
                    })
                )
                
                db.session.add(database_task)
                db.session.commit()
                logger.info(f"Created new database Task {database_task.id} for bulk CSV job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to create/update database Task: {e}")
            # Try to rollback and continue without database task
            try:
                db.session.rollback()
            except Exception as rollback_error:
                logger.warning(f"Failed to rollback database session: {rollback_error}")
                # Remove the session to prevent further issues
                try:
                    db.session.remove()
                except Exception:
                    pass
            # Continue without database task - don't fail the generation

        # Initialize unified progress system
        progress_system = get_unified_progress()

        # Prepare job parameters for tracking metadata
        job_params = {
            'prompt_rule_id': prompt_rule_id,
            'model_name': model_name,
            'client': client,
            'project': project,
            'website': website,
            'client_notes': client_notes,
            'target_word_count': target_word_count,
            'concurrent_workers': concurrent_workers,
            'batch_size': batch_size,
            'insert_content': insert_content,
            'insert_position': insert_position
        }

        # Create enhanced generator with prompt rule integration and progress tracking
        # BUG FIX: Pass model_name to use the user-specified model instead of default
        generator = EnhancedBulkCSVGenerator(
            output_dir=output_dir,
            concurrent_workers=concurrent_workers,
            batch_size=batch_size,
            target_word_count=target_word_count,
            prompt_rule=prompt_rule,
            unified_progress_system=progress_system,
            job_id=job_id,
            job_params=job_params,
            model_name=model_name  # Use the model specified by user
        )

        # Start generation using Celery task with GPU monitoring
        try:
            from backend.celery_tasks_isolated import generate_bulk_csv_v2_task

            # FIX: Use the job_id that was stored in the database Task instead of creating a new one
            # This ensures Task status updates and progress tracking use the same ID
            actual_process_id = progress_system.create_process(
                process_type=ProcessType.CSV_PROCESSING,
                description=f"Bulk CSV generation of {len(tasks)} items",
                additional_data={
                    "client": client,
                    "project": project,
                    "website": website,
                    "target_word_count": target_word_count,
                    "concurrent_workers": concurrent_workers,
                    "batch_size": batch_size,
                    "task_id": database_task.id if database_task else None  # Link to Task record
                },
                process_id=job_id  # Use the existing job_id instead of generating a new one
            )

            # The generator already has the correct job_id from initialization (line 818)
            logger.info(f"Using job_id for progress tracking: {job_id} (linked to Task {database_task.id if database_task else 'N/A'})")

            # Use threading directly for CSV generation (Celery task signature mismatch resolved)
            logger.info(f"Starting CSV generation with threading for {len(tasks)} tasks")
            import threading
            import traceback
            # FIX BUG #18: Handle tuple return from generate_bulk_csv properly
            # BUG FIX: Added comprehensive error logging for thread debugging
            def process_csv():
                thread_name = threading.current_thread().name
                logger.info(f"[{thread_name}] CSV generation thread started for job {job_id}")
                try:
                    # Log thread entry point for debugging
                    logger.info(f"[{thread_name}] Calling generator.generate_bulk_csv() with {len(tasks)} tasks")

                    result = generator.generate_bulk_csv(tasks, secure_output_filename)

                    # Handle both tuple and single value returns
                    if isinstance(result, tuple) and len(result) == 2:
                        output_path, stats = result
                        logger.info(f"[{thread_name}] Database-free CSV generation completed: {output_path}, Stats: {stats}")
                        progress_system.complete_process(job_id, f"CSV generation completed: {output_path}")
                        # Note: Database Task status updates removed to avoid application context errors in background thread
                    else:
                        logger.info(f"[{thread_name}] Database-free CSV generation completed: {result}")
                        progress_system.complete_process(job_id, f"CSV generation completed")
                        # Note: Database Task status updates removed to avoid application context errors in background thread
                except Exception as e:
                    # BUG FIX: Log full traceback for debugging
                    logger.error(f"[{thread_name}] Database-free CSV generation failed: {e}")
                    logger.error(f"[{thread_name}] Full traceback:\n{traceback.format_exc()}")
                    # Mark progress as error on failure
                    progress_system.error_process(job_id, f"CSV generation failed: {str(e)}")
                    # Note: Database Task status updates removed to avoid application context errors in background thread
                finally:
                    logger.info(f"[{thread_name}] CSV generation thread exiting for job {job_id}")

            thread = threading.Thread(target=process_csv, daemon=True, name=f"csv_gen_{job_id}")
            thread.start()
            logger.info(f"Started CSV generation thread: {thread.name}")

            return jsonify({
                "message": f"Bulk CSV generation started for {len(tasks)} items",
                "job_id": job_id,  # FIX: Return the same job_id stored in Task, not a new one
                "task_id": database_task.id if database_task else None,
                "status": "processing",
                "output_filename": secure_output_filename,
                "num_items": len(tasks),
                "concurrent_workers": concurrent_workers,
                "target_word_count": target_word_count
            }), 200
        
        except Exception as e:
            logger.error(f"Error in CSV generation setup: {e}")
            return jsonify({"error": f"CSV generation setup error: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"Error in bulk CSV generation: {e}")
        return jsonify({"error": f"CSV generation error: {str(e)}"}), 500


@bulk_gen_bp.route("/csv-original", methods=["POST"])
def generate_bulk_csv_original():
    """
    Generate bulk CSV content based on topics and specifications
    Version 2.0: Enhanced with Context Variables System
    
    Expected payload (Legacy Support):
    {
        "output_filename": "my_content.csv",
        "client": "Professional Services",
        "project": "Legal Services Marketing", 
        "website": "datacenterknowledge.com/business",
        "topics": ["topic1", "topic2", ...] or "auto",
        "num_items": 100,
        "concurrent_workers": 10,
        "target_word_count": 500,
        "batch_size": 50,
        "resume_from_id": null
    }
    
    Expected payload (Context Variables):
    {
        "output_filename": "my_content.csv",
        "context_template": "legal_services" | "data_center" | "healthcare" | "generic",
        "context_variables": {
            "client": "My Company",
            "project": "SEO Campaign",
            "website": "mycompany.com",
            "quantity": 50
        },
        "natural_language": "Generate 50 legal service pages for Smith & Associates law firm at smithlaw.com"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON."}), 400

        output_filename = data.get("output_filename")
        if not output_filename:
            return jsonify({"error": "output_filename is required."}), 400

        # NEW: Context Variables System Support
        natural_language = data.get("natural_language")
        context_template_name = data.get("context_template")
        context_variables = data.get("context_variables", {})
        
        if natural_language:
            # Extract context from natural language
            logger.info(f"Processing natural language request: {natural_language[:100]}...")
            context_template = context_manager.create_context_from_text(natural_language, context_template_name)
            
            # Merge provided context variables
            context_template.variables.update(context_variables)
            
            # Generate payload from context
            generation_payload = context_manager.get_bulk_generation_payload(
                context_template,
                output_filename=output_filename
            )
            
            client = generation_payload["client"]
            project = generation_payload["project"]
            website = generation_payload["website"]
            topics_input = generation_payload["topics"]
            num_items = generation_payload["num_items"]
            concurrent_workers = generation_payload["concurrent_workers"]
            target_word_count = generation_payload["target_word_count"]
            batch_size = generation_payload["batch_size"]
            resume_from_id = data.get("resume_from_id")
            
            logger.info(f"Context Variables - Client: {client}, Project: {project}, Items: {num_items}")
            
        elif context_template_name or context_variables:
            # Use provided context template/variables
            logger.info(f"Using context template: {context_template_name}")
            
            if context_template_name:
                context_template = context_manager.templates.get(context_template_name)
                if not context_template:
                    return jsonify({"error": f"Unknown context template: {context_template_name}"}), 400
                context_template.variables.update(context_variables)
            else:
                # Create generic template with provided variables
                context_template = context_manager.templates['generic']
                context_template.variables.update(context_variables)
            
            generation_payload = context_manager.get_bulk_generation_payload(
                context_template,
                output_filename=output_filename
            )
            
            client = generation_payload["client"]
            project = generation_payload["project"] 
            website = generation_payload["website"]
            topics_input = generation_payload["topics"]
            num_items = generation_payload["num_items"]
            concurrent_workers = generation_payload["concurrent_workers"]
            target_word_count = generation_payload["target_word_count"]
            batch_size = generation_payload["batch_size"]
            resume_from_id = data.get("resume_from_id")
            
        else:
            # LEGACY: Extract parameters directly (backward compatibility)
            logger.info("Using legacy parameter extraction")
            client = data.get("client", "Professional Services")  # Removed hardcoded "Professional Services"
            project = data.get("project", "Content Generation")
            website = data.get("website", "professional-website.com")  # Removed hardcoded datacenter site
            topics_input = data.get("topics")
            num_items = data.get("num_items", 20)  # Reduced default from 100
            concurrent_workers = data.get("concurrent_workers", 10)
            target_word_count = data.get("target_word_count", 500)
            batch_size = data.get("batch_size", 20)  # Reduced default from 50
            resume_from_id = data.get("resume_from_id")
            # FIX BUG #6: Define client_notes for legacy path
            client_notes = data.get("client_notes")

        # BUG FIX: Add missing insert_content and insert_position parameters for /csv-original endpoint
        insert_content = data.get("insert_content")  # Content to insert into each page
        insert_position = data.get("insert_position")  # Position: 'top', 'bottom', 'none'

        # Additional parameters for context (may be used in job_params)
        prompt_rule_id = data.get("prompt_rule_id")
        model_name = data.get("model_name") or get_active_model_name()

        # Get output directory
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            return jsonify({"error": "Server configuration error: Output directory not set."}), 500

        # Secure filename
        secure_output_filename = secure_filename(output_filename)
        if not secure_output_filename.lower().endswith(".csv"):
            secure_output_filename += ".csv"

        # Generate unique filename with sequence number if needed
        secure_output_filename = get_unique_filename(output_dir, secure_output_filename)

        # Generate or use provided topics
        if topics_input == "auto" or not topics_input:
            logger.info(f"Auto-generating topics for {client} in {project}")
            
            # NEW: Use context-aware topic generation
            if 'context_template' in locals() and hasattr(context_template, 'suggested_topics') and context_template.suggested_topics:
                # Use template-specific topics
                topics = context_template.suggested_topics[:num_items]
                
                # If we need more topics than the template provides, expand
                if len(topics) < num_items:
                    additional_needed = num_items - len(topics)
                    if "data center" in client.lower() or "data center" in project.lower():
                        additional_topics = create_data_center_topics(additional_needed)
                    else:
                        # Generate generic topics based on client/project
                        additional_topics = [
                            f"{client} Professional Services",
                            f"{client} Expert Solutions",
                            f"{client} Industry Leadership",
                            f"{client} Quality Standards",
                            f"{client} Customer Success"
                        ] * (additional_needed // 5 + 1)
                        additional_topics = additional_topics[:additional_needed]
                    
                    topics.extend(additional_topics)
                    
                logger.info(f"Using {len(topics)} context-aware topics for {context_template.name}")
            else:
                # Fallback to data center topics if no context available
                topics = create_data_center_topics(num_items)
                logger.info(f"Using {len(topics)} auto-generated data center topics")
                
        elif isinstance(topics_input, list):
            topics = topics_input
            num_items = len(topics)
            logger.info(f"Using {len(topics)} provided topics")
        else:
            return jsonify({"error": "topics must be an array or 'auto'"}), 400

        # Create generation tasks
        tasks = create_tasks_from_topics(
            topics=topics,
            client=client,
            project=project,
            website=website,
            client_notes=client_notes
        )

        # Simple job ID without database progress system
        import time
        job_id = f"bulk_gen_{int(time.time())}"
        logger.info(f"Starting direct bulk generation. Job ID: {job_id}")

        # Skip database operations for direct processing to avoid transaction issues
        # Generate unique task ID without database
        import time
        task_id = f"direct_{int(time.time())}"
        logger.info(f"Using direct processing mode, bypassing database. Task ID: {task_id}")

        # Direct Celery processing without database dependencies
        from backend.utils.bulk_csv_generator import BulkCSVGenerator
        
        try:
            # Create generator and process directly
            # Prepare job parameters for tracking metadata
            job_params = {
                'prompt_rule_id': prompt_rule_id,
                'model_name': model_name,
                'client': client,
                'project': project,
                'website': website,
                'client_notes': client_notes,
                'target_word_count': target_word_count,
                'concurrent_workers': concurrent_workers,
                'batch_size': batch_size,
                'insert_content': insert_content,
                'insert_position': insert_position
            }
            
            generator = BulkCSVGenerator(
                output_dir=output_dir,
                concurrent_workers=concurrent_workers,
                batch_size=batch_size,
                target_word_count=target_word_count,
                job_params=job_params
            )
            
            # Process in background thread to avoid blocking
            import threading
            def process_csv():
                try:
                    output_path, stats = generator.generate_bulk_csv(tasks, secure_output_filename)
                    logger.info(f"Direct CSV generation completed: {output_path}, Stats: {stats}")
                except Exception as e:
                    logger.error(f"Direct CSV generation failed: {e}")
            
            # Start processing in background
            thread = threading.Thread(target=process_csv)
            thread.start()
            
        except Exception as e:
            logger.error(f"Failed to start direct CSV generation: {e}")
            return jsonify({"error": f"Failed to start generation: {str(e)}"}), 500

        logger.info(f"Bulk CSV generation v2 task dispatched. Job ID: {job_id}, Tasks: {len(tasks)}")

        # Enhanced response with context information
        response_data = {
            "message": "Bulk CSV generation dispatched to background worker.",
            "job_id": job_id,
            "task_id": task_id,
            "estimated_duration_minutes": len(tasks) * target_word_count / (concurrent_workers * 100),  # Rough estimate
            "context_used": {
                "client": client,
                "project": project,
                "website": website,
                "num_items": len(tasks),
                "template": context_template.name if 'context_template' in locals() else "legacy"
            }
        }

        return jsonify(response_data), 202

    except Exception as e:
        logger.error(f"Error in bulk CSV generation: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@bulk_gen_bp.route("/demo", methods=["POST"])
def generate_demo_csv():
    """
    Generate a demonstration CSV file with Professional Services content
    This is a simplified endpoint for testing and demonstrations
    
    Expected payload:
    {
        "num_items": 20,
        "concurrent_workers": 5
    }
    """
    try:
        data = request.get_json() or {}
        num_items = data.get("num_items", 20)
        concurrent_workers = data.get("concurrent_workers", 5)

        # Get output directory
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            return jsonify({"error": "Server configuration error: Output directory not set."}), 500

        # Create demo CSV synchronously (for immediate results)
        logger.info(f"Creating demo CSV with {num_items} items, {concurrent_workers} workers")
        
        output_path, stats = create_demonstration_csv(
            output_dir=output_dir,
            num_items=num_items,
            concurrent_workers=concurrent_workers
        )

        return jsonify({
            "message": "Demo CSV generated successfully.",
            "output_file": os.path.basename(output_path),
            "output_path": output_path,
            "statistics": stats
        }), 200

    except Exception as e:
        logger.error(f"Error in demo CSV generation: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@bulk_gen_bp.route("/estimate", methods=["POST"])
def estimate_generation_time():
    """
    Estimate generation time and resource requirements for a bulk job
    
    Expected payload:
    {
        "num_items": 1000,
        "concurrent_workers": 10,
        "target_word_count": 500
    }
    """
    try:
        data = request.get_json() or {}
        num_items = data.get("num_items", 100)
        concurrent_workers = data.get("concurrent_workers", 10)
        target_word_count = data.get("target_word_count", 500)

        # Estimate based on typical performance metrics
        # These estimates are based on testing with various models
        base_time_per_item_seconds = 30  # Conservative estimate
        
        # Adjust for concurrency
        effective_time_per_item = base_time_per_item_seconds / concurrent_workers
        
        # Adjust for word count (longer content takes more time)
        word_count_multiplier = target_word_count / 500  # 500 words as baseline
        effective_time_per_item *= word_count_multiplier
        
        # Calculate estimates
        total_time_seconds = num_items * effective_time_per_item
        total_time_minutes = total_time_seconds / 60
        total_time_hours = total_time_minutes / 60
        
        # Calculate throughput estimates
        items_per_hour = 3600 / effective_time_per_item
        items_per_day = items_per_hour * 24
        
        # Estimate file size (rough)
        estimated_content_size_per_item = target_word_count * 6  # ~6 bytes per word
        estimated_total_file_size_mb = (num_items * estimated_content_size_per_item) / (1024 * 1024)

        estimates = {
            "input_parameters": {
                "num_items": num_items,
                "concurrent_workers": concurrent_workers,
                "target_word_count": target_word_count
            },
            "time_estimates": {
                "total_seconds": round(total_time_seconds, 2),
                "total_minutes": round(total_time_minutes, 2),
                "total_hours": round(total_time_hours, 2),
                "per_item_seconds": round(effective_time_per_item, 2)
            },
            "throughput_estimates": {
                "items_per_hour": round(items_per_hour, 2),
                "items_per_day": round(items_per_day, 2),
                "can_achieve_1000_per_day": items_per_day >= 1000
            },
            "resource_estimates": {
                "estimated_file_size_mb": round(estimated_total_file_size_mb, 2),
                "recommended_concurrent_workers": min(20, max(5, num_items // 50)),
                "memory_usage_estimate_mb": concurrent_workers * 100  # Rough estimate
            },
            "recommendations": []
        }

        # Add recommendations
        if items_per_day < 1000:
            estimates["recommendations"].append(
                f"To achieve 1000+ items per day, increase concurrent_workers to {max(10, int(1000 * concurrent_workers / items_per_day))}"
            )
        
        if concurrent_workers > 20:
            estimates["recommendations"].append(
                "Consider using concurrent_workers <= 20 to avoid overwhelming the LLM service"
            )
        
        if estimated_total_file_size_mb > 1000:
            estimates["recommendations"].append(
                "Large file size expected. Consider splitting into multiple smaller files."
            )

        return jsonify(estimates), 200

    except Exception as e:
        logger.error(f"Error in generation estimation: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@bulk_gen_bp.route("/status", methods=["GET"])
def get_bulk_generation_status():
    """Get overall bulk generation system status"""
    try:
        # Get unified progress system
        unified_progress = get_unified_progress()
        active_processes = unified_progress.get_active_processes()
        
        # Filter for bulk generation processes
        bulk_processes = {
            process_id: process for process_id, process in active_processes.items()
            if process.process_type == ProcessType.CSV_PROCESSING
        }
        
        return jsonify({
            "status": "available",
            "active_processes": len(bulk_processes),
            "processes": [
                {
                    "process_id": process_id,
                    "progress": process.progress,
                    "message": process.message,
                    "status": process.status.value,
                    "timestamp": process.timestamp.isoformat()
                }
                for process_id, process in bulk_processes.items()
            ],
            "capabilities": {
                "max_concurrent_workers": 20,
                "max_daily_capacity": 43200,
                "supported_formats": ["csv"],
                "context_variables": True,
                "natural_language": True
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting bulk generation status: {e}")
        return jsonify({"error": str(e)}), 500

@bulk_gen_bp.route("/status/<job_id>", methods=["GET"])
def get_generation_status(job_id):
    """Get the status of a bulk generation job"""
    try:
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            return jsonify({"error": "Output directory not configured"}), 500

        # Get job metadata from unified progress system
        progress_system = get_unified_progress()
        process = progress_system.get_process(job_id)
        
        if not process:
            return jsonify({"error": "Job not found"}), 404

        # Get associated task from database
        task = db.session.query(Task).filter_by(job_id=job_id).first()
        
        response = {
            "job_id": job_id,
            "progress_status": {
                "progress": process.progress,
                "message": process.message,
                "status": process.status.value,
                "process_type": process.process_type.value,
                "timestamp": process.timestamp.isoformat()
            },
            "task_status": {
                "id": task.id if task else None,
                "status": task.status if task else "UNKNOWN",
                "description": task.description if task else None
            } if task else None
        }

        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Error getting generation status: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@bulk_gen_bp.route("/topics/generate", methods=["POST"])
def generate_topic_list():
    """
    Generate a list of topics for a specific domain/client
    
    Expected payload:
    {
        "domain": "data center legal services",
        "client": "Professional Services", 
        "count": 100,
        "base_topics": ["optional", "seed", "topics"]
    }
    """
    try:
        data = request.get_json() or {}
        domain = data.get("domain", "data center legal services")
        client = data.get("client", "Professional Services")
        count = data.get("count", 100)
        base_topics = data.get("base_topics", [])

        if "data center" in domain.lower() or "data center" in client.lower():
            # Use the built-in data center topic generator
            topics = create_data_center_topics(count)
        else:
            # For other domains, create a basic topic list
            # This could be enhanced with LLM-generated topics in the future
            generic_topics = [
                f"{client} Services Overview",
                f"{client} Best Practices",
                f"{client} Industry Standards",
                f"{client} Compliance Requirements",
                f"{client} Professional Solutions",
                f"{client} Expert Guidance",
                f"{client} Strategic Planning",
                f"{client} Risk Management",
                f"{client} Quality Assurance",
                f"{client} Industry Insights"
            ]
            
            # Expand with variations
            topics = []
            base_list = base_topics if base_topics else generic_topics
            
            while len(topics) < count:
                for base_topic in base_list:
                    if len(topics) >= count:
                        break
                    topics.append(base_topic)

        return jsonify({
            "domain": domain,
            "client": client,
            "topic_count": len(topics),
            "topics": topics[:count]
        }), 200

    except Exception as e:
        logger.error(f"Error generating topic list: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@bulk_gen_bp.route("/retry", methods=["POST"])
def retry_failed_rows():
    """
    Retry failed rows from a previous bulk generation job.
    
    Expected payload:
    {
        "tracking_file": "bulk_gen_1760567624_tracking_20251015_190305.json",
        "retry_mode": "failed_only" | "all_inactive" | "zero_attempts" | "partial_failures",
        "output_filename": "bulk_gen_retry_output.csv",
        "max_retries": 5,
        "model_name": "gpt-4",
        "prompt_rule_id": 19,
        "concurrent_workers": 10,
        "batch_size": 50
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON."}), 400
        
        # Extract parameters
        tracking_file = data.get("tracking_file")
        retry_mode = data.get("retry_mode", "all_inactive")
        output_filename = data.get("output_filename", "retry_output.csv")
        max_retries = data.get("max_retries", 5)
        model_name = data.get("model_name")
        prompt_rule_id = data.get("prompt_rule_id")
        concurrent_workers = data.get("concurrent_workers", 10)
        batch_size = data.get("batch_size", 50)
        
        if not tracking_file:
            return jsonify({"error": "tracking_file is required"}), 400
        
        # Validate tracking file exists
        tracking_path = os.path.join(
            os.path.dirname(__file__), '..', '..', 'data', 'outputs', 'tracking', tracking_file
        )
        
        if not os.path.exists(tracking_path):
            return jsonify({"error": f"Tracking file not found: {tracking_file}"}), 404
        
        logger.info(f"Starting retry operation for tracking file: {tracking_file}")
        logger.info(f"Retry mode: {retry_mode}")
        
        # Analyze tracking file
        analyzer = TrackingAnalyzer(tracking_path)
        analysis = analyzer.analyze_tracking_file()
        
        # Extract failed topics
        failed_topics = analyzer.extract_failed_topics(retry_mode)
        if not failed_topics:
            return jsonify({
                "error": f"No failed topics found for retry mode '{retry_mode}'",
                "analysis": analysis
            }), 400
        
        # Extract job context
        job_context = analyzer.extract_job_context()
        
        logger.info(f"Found {len(failed_topics)} failed topics to retry")
        logger.info(f"Original job context: {job_context}")
        
        # Create output directory
        output_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'outputs')
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate unique filename
        unique_filename = get_unique_filename(output_dir, output_filename)
        output_path = os.path.join(output_dir, unique_filename)
        
        # Create retry job ID
        import time
        retry_job_id = f"retry_{int(time.time())}"
        
        # Create generation tasks from failed topics
        tasks = []
        for i, topic_data in enumerate(failed_topics):
            task = GenerationTask(
                item_id=topic_data['task_id'],
                topic=topic_data['topic'],
                client=job_context.get('client', 'Professional Services'),
                project=job_context.get('project', 'Content Generation'),
                website=job_context.get('website', 'website.com'),
                client_notes=job_context.get('client_notes', ''),
                target_keywords=[],
                phone=job_context.get('client_phone', ''),
                contact_url=job_context.get('contact_url', ''),
                client_location=job_context.get('client_location', ''),
                company_name=job_context.get('company_name', ''),
                primary_service=job_context.get('primary_service', ''),
                secondary_service=job_context.get('secondary_service', ''),
                brand_tone=job_context.get('brand_tone', 'neutral'),
                hours=job_context.get('business_hours', ''),
                social_links=[],
                structured_html=False,
                include_h1=True,
                csv_delimiter=',',
                insert_content=job_context.get('insert_content'),
                insert_position=job_context.get('insert_position'),
                prompt_rule_id=prompt_rule_id
            )
            tasks.append(task)
        
        logger.info(f"Created {len(tasks)} retry tasks")
        
        # Create bulk generator for retry
        from backend.utils.bulk_csv_generator import BulkCSVGenerator
        
        generator = BulkCSVGenerator(
            output_dir=output_dir,
            concurrent_workers=concurrent_workers,
            batch_size=batch_size,
            target_word_count=job_context.get('target_word_count', 500),
            unified_progress_system=None,
            job_id=retry_job_id,
            csv_delimiter=','
        )
        
        # Set retry context
        generator.original_job_id = job_context.get('original_job_id', 'unknown')
        generator.retry_mode = retry_mode
        generator.retry_source_file = tracking_file
        
        # Generate content for failed rows using optimized retry method
        logger.info(f"Starting optimized retry generation for {len(tasks)} tasks")
        
        try:
            # OPTIMIZATION: Use the retry-specific CSV generation method
            output_path, statistics = generator.generate_retry_csv(
                tasks=tasks,
                output_filename=unique_filename
            )
            
            logger.info(f"Retry generation completed successfully: {output_path}")
            
            # Link retry tracking to original tracking file
            retry_tracking_file = statistics.get('tracking_file', '')
            if retry_tracking_file:
                try:
                    # Update original tracking file to reference retry
                    original_tracking_path = tracking_path
                    with open(original_tracking_path, 'r', encoding='utf-8') as f:
                        original_data = json.load(f)
                    
                    # Add retry reference to metadata
                    if 'retry_files' not in original_data['metadata']:
                        original_data['metadata']['retry_files'] = []
                    
                    retry_filename = os.path.basename(retry_tracking_file)
                    if retry_filename not in original_data['metadata']['retry_files']:
                        original_data['metadata']['retry_files'].append(retry_filename)
                    
                    # Write updated original tracking file
                    with open(original_tracking_path, 'w', encoding='utf-8') as f:
                        json.dump(original_data, f, indent=2, ensure_ascii=False)
                    
                    logger.info(f"Linked retry tracking {retry_filename} to original tracking")
                    
                except Exception as e:
                    logger.warning(f"Failed to link retry tracking to original: {e}")
            
            return jsonify({
                "success": True,
                "message": f"Retry generation completed for {len(failed_topics)} failed rows",
                "retry_job_id": retry_job_id,
                "original_job_id": job_context.get('original_job_id'),
                "retry_mode": retry_mode,
                "output_file": output_path,
                "tracking_file": statistics.get('tracking_file', ''),
                "statistics": {
                    "failed_topics_retried": len(failed_topics),
                    "retry_job_id": retry_job_id,
                    "original_analysis": analysis,
                    "generation_stats": statistics
                }
            })
                
        except Exception as e:
            logger.error(f"Error during retry generation: {e}", exc_info=True)
            return jsonify({
                "success": False,
                "error": f"Retry generation failed: {str(e)}"
            }), 500
            
    except Exception as e:
        logger.error(f"Error in retry failed rows: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Retry operation failed: {str(e)}"
        }), 500


@bulk_gen_bp.route("/progress/test", methods=["GET"])
def test_endpoint():
    """Test endpoint to verify route registration"""
    logger.info("=== TEST ENDPOINT CALLED ===")
    return jsonify({"status": "test_works", "timestamp": time.time()}), 200

@bulk_gen_bp.route("/progress/current", methods=["GET"])
def get_current_progress():
    """Get current job progress from metadata file - for simple polling (ProgressFooterBar)"""
    logger.info("=== PROGRESS ENDPOINT CALLED ===")
    try:
        output_dir = current_app.config.get("OUTPUT_DIR", "data/outputs")
        progress_dir = os.path.join(output_dir, ".progress_jobs")

        logger.info(f"Progress check: output_dir={output_dir}, progress_dir={progress_dir}")

        if not os.path.exists(progress_dir):
            logger.warning(f"Progress directory does not exist: {progress_dir}")
            return jsonify(None), 200  # No progress directory

        # Find the most recently updated job directory
        most_recent_job = None
        most_recent_time = 0

        for job_dir_name in os.listdir(progress_dir):
            job_path = os.path.join(progress_dir, job_dir_name)

            # Skip files, only look at directories
            if not os.path.isdir(job_path):
                continue

            metadata_path = os.path.join(job_path, "metadata.json")

            if os.path.exists(metadata_path):
                # Get file modification time
                mtime = os.path.getmtime(metadata_path)

                # Only consider jobs updated in the last 30 minutes (or completed jobs in last 10 minutes)
                age_seconds = time.time() - mtime
                
                # Read metadata to check if it's complete
                try:
                    with open(metadata_path, 'r') as f:
                        metadata = json.load(f)
                    is_complete = metadata.get('is_complete', False)
                    
                    # Show active jobs for 30 minutes, completed jobs for 10 minutes
                    max_age = 600 if is_complete else 1800  # 10 or 30 minutes
                    
                    if age_seconds < max_age:
                        if mtime > most_recent_time:
                            most_recent_time = mtime
                            most_recent_job = metadata_path
                except Exception as e:
                    logger.warning(f"Failed to read metadata {metadata_path}: {e}")
                    # Fallback to time-based check only
                    if age_seconds < 1800:  # 30 minutes
                        if mtime > most_recent_time:
                            most_recent_time = mtime
                            most_recent_job = metadata_path

        if most_recent_job is None:
            logger.info("No active progress jobs found")
            return jsonify(None), 200  # No active jobs

        # Read the most recent job's metadata
        logger.info(f"Found most recent job: {most_recent_job}")
        with open(most_recent_job, 'r') as f:
            data = json.load(f)
        
        logger.info(f"Returning progress data: job_id={data.get('job_id')}, progress={data.get('progress')}%")
        return jsonify(data), 200

    except Exception as e:
        logger.error(f"Error reading progress metadata: {e}", exc_info=True)
        return jsonify(None), 200  # Fail gracefully


@bulk_gen_bp.route("/xml", methods=["POST"])
def generate_bulk_xml():
    """
    Generate bulk XML content for WordPress imports via Llamanator plugin.
    Mirrors CSV generation workflow but outputs XML format.

    Expected payload:
    {
        "output_filename": "my_content.xml",
        "client": "Professional Services",
        "project": "Content Generation",
        "website": "website.com",
        "client_notes": "Business description for context",
        "topics": ["topic1", "topic2", ...],
        "num_items": 10,
        "concurrent_workers": 5,
        "target_word_count": 500,
        "batch_size": 25,
        "prompt_rule_id": 19,
        "model_name": "gpt-4",
        "insert_content": "Contact info or shortcodes",
        "insert_position": "top" | "bottom" | "none"
    }
    """

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON."}), 400

        # Extract parameters
        output_filename = data.get("output_filename")
        client = data.get("client", "Professional Services")
        project = data.get("project", "Content Generation")
        website = data.get("website") or "website.com"
        # FIX BUG #10: Validate and handle None values for notes parameters
        client_notes = data.get("client_notes") or ""
        project_notes = data.get("project_notes") or ""
        competitor_url = data.get("competitor_url") or ""
        topics = data.get("topics", [])
        num_items = data.get("num_items", 10)
        concurrent_workers = data.get("concurrent_workers", 5)
        target_word_count = data.get("target_word_count", 500)
        batch_size = data.get("batch_size", 25)
        prompt_rule_id = data.get("prompt_rule_id")
        model_name = data.get("model_name") or get_active_model_name()
        insert_content = data.get("insert_content")
        insert_position = data.get("insert_position")
        
        # FIX: Fetch real client metadata from database instead of using placeholders
        client_phone = None
        client_email = None
        client_location = None
        contact_url = None
        primary_service = None
        secondary_service = None
        brand_tone = "professional"
        business_hours = None
        
        try:
            from backend.models import Client, db
            # Try to find client by name (exact match first)
            client_obj = db.session.query(Client).filter_by(name=client).first()
            
            # FIX: Try case-insensitive search if exact match fails
            if not client_obj:
                client_obj = db.session.query(Client).filter(Client.name.ilike(client)).first()
                if client_obj:
                    logger.info(f"[XML] Found client with case-insensitive match: '{client}' -> '{client_obj.name}'")
            
            if client_obj:
                client_phone = client_obj.phone
                client_email = client_obj.email
                client_location = client_obj.location
                primary_service = client_obj.primary_service
                secondary_service = client_obj.secondary_service
                brand_tone = client_obj.brand_tone or "professional"
                business_hours = client_obj.business_hours
                # Use client notes from database if not provided
                if not client_notes and client_obj.notes:
                    client_notes = client_obj.notes
                logger.info(f"[XML] Loaded client metadata for '{client}': phone={client_phone}, email={client_email}, location={client_location}")
            else:
                logger.warning(f"[XML] Client '{client}' not found in database (tried exact and case-insensitive) - using defaults")
        except Exception as e:
            logger.error(f"[XML] Error fetching client metadata: {e}")
            logger.info("[XML] Using default client metadata")

        if not output_filename:
            return jsonify({"error": "output_filename is required"}), 400

        if not topics and num_items <= 0:
            return jsonify({"error": "Either topics list or num_items > 0 is required"}), 400

        logger.info(f"XML generation: {num_items} items, prompt_rule_id: {prompt_rule_id}")

        # Secure filename
        secure_output_filename = secure_filename(output_filename)
        if not secure_output_filename:
            return jsonify({"error": "Invalid filename after security processing"}), 400
        if not secure_output_filename.lower().endswith(".xml"):
            secure_output_filename += ".xml"

        # Get output directory
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            return jsonify({"error": "Server configuration error: Output directory not set"}), 500

        # Generate unique filename if needed
        secure_output_filename = get_unique_filename(output_dir, secure_output_filename)

        # Get prompt rule
        prompt_rule = None
        try:
            from backend.models import db, Rule
            if prompt_rule_id:
                prompt_rule = db.session.query(Rule).filter_by(id=prompt_rule_id, is_active=True).first()
                if prompt_rule:
                    db.session.expunge(prompt_rule)
                    logger.info(f"Using prompt rule: {prompt_rule.name} (ID: {prompt_rule_id})")
            else:
                # Default to WordPress rule
                prompt_rule = db.session.query(Rule).filter_by(name="Enhanced WordPress CSV Generation", is_active=True).first()
                if prompt_rule:
                    db.session.expunge(prompt_rule)
                    logger.info(f"Using default WordPress rule: {prompt_rule.name}")
        except Exception as e:
            logger.warning(f"Error loading prompt rule: {e}")

        # Create generation tasks
        # FIX BUG #7: create_data_center_topics only takes num_items parameter
        if not topics or len(topics) == 0:
            topics = create_data_center_topics(num_items)

        # Generate website-scoped IDs
        website_key = website.replace('http://', '').replace('https://', '').split('/')[0]
        website_prefix = website_key.replace('.', '-').replace('_', '-')[:20]

        tasks = []
        for i, topic in enumerate(topics[:num_items], 1):
            # Generate proper website-scoped ID
            page_id = f"{website_prefix}-{i:03d}"

            task = EnhancedGenerationTask(
                item_id=page_id,
                topic=topic,
                client=client,
                project=project,
                website=website,
                prompt_rule=prompt_rule,
                target_word_count=target_word_count,
                insert_content=insert_content,
                insert_position=insert_position,
                # ENHANCEMENT: Pass full context
                client_notes=client_notes,
                project_notes=project_notes,
                competitor_url=competitor_url,
                # FIX: Pass real client metadata instead of using placeholders
                client_phone=client_phone,
                client_email=client_email,
                client_location=client_location,
                contact_url=contact_url,
                primary_service=primary_service,
                secondary_service=secondary_service,
                brand_tone=brand_tone,
                business_hours=business_hours
            )
            tasks.append(task)

        logger.info(f"Created {len(tasks)} XML generation tasks")

        # Create job ID for progress tracking
        import time
        job_id = f"xml_gen_{int(time.time())}"

        # Get unified progress system
        unified_progress = get_unified_progress()

        # Initialize generator
        # FIX BUG #8: BulkXMLGenerator doesn't accept model_name parameter
        generator = BulkXMLGenerator(
            output_dir=output_dir,
            concurrent_workers=concurrent_workers,
            batch_size=batch_size,
            target_word_count=target_word_count,
            prompt_rule=prompt_rule,
            unified_progress_system=unified_progress,
            job_id=job_id
        )

        # Start generation (synchronous for now - can be made async later)
        logger.info(f"Starting XML generation for {len(tasks)} tasks")
        result = generator.generate_bulk_content(
            tasks,
            secure_output_filename,
            insert_content=insert_content,
            insert_position=insert_position
        )

        if result.get('success'):
            return jsonify({
                "message": f"XML generation completed: {result.get('message')}",
                "job_id": job_id,
                "output_filename": secure_output_filename,
                "statistics": result.get('statistics'),
                "file_path": result.get('file_path')
            }), 200
        else:
            return jsonify({
                "error": "XML generation failed",
                "details": result.get('error'),
                "message": result.get('message')
            }), 500

    except Exception as e:
        logger.error(f"Error in XML generation: {e}", exc_info=True)
        return jsonify({
            "error": "XML generation failed",
            "details": str(e)
        }), 500 