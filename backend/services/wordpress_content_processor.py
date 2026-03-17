# backend/services/wordpress_content_processor.py
"""
WordPress Content Processor Service
Uses LLM models to improve and optimize WordPress content
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

from flask import current_app

from backend.models import WordPressPage, WordPressSite, Client, Project, Rule, db
from backend.utils.llm_service import (
    get_llm_instance,
    run_llm_chat_prompt,
    ChatMessage,
    MessageRole,
    _safe_content,
)
from backend.utils.unified_progress_system import get_unified_progress, ProcessType
from backend.services.seo_content_processor import SEOContentProcessor

logger = logging.getLogger(__name__)


class WordPressContentProcessor:
    """Service for processing and improving WordPress content using LLM"""

    def __init__(self, page_id: int, job_id: Optional[str] = None):
        """
        Initialize content processor for a WordPress page

        Args:
            page_id: WordPressPage ID
            job_id: Optional job ID for progress tracking
        """
        self.page_id = page_id
        self.wp_page = db.session.get(WordPressPage, page_id)

        if not self.wp_page:
            raise ValueError(f"WordPress page {page_id} not found")

        self.wp_site = db.session.get(WordPressSite, self.wp_page.wordpress_site_id)
        self.job_id = job_id or str(uuid.uuid4())
        self.progress_system = get_unified_progress()

        # Get LLM instance
        self.llm = get_llm_instance()
        if not self.llm:
            logger.warning(
                "LLM instance not available - content processing will be limited"
            )

        # Get client/project context if available
        self.client_context = None
        self.project_context = None
        if self.wp_site.client_id:
            self.client_context = db.session.get(Client, self.wp_site.client_id)
        if self.wp_site.project_id:
            self.project_context = db.session.get(Project, self.wp_site.project_id)

    def _update_progress(
        self, progress: int, message: str, additional_data: Optional[Dict] = None
    ):
        """Update progress tracking"""
        try:
            self.progress_system.update_process(
                self.job_id,
                progress,
                message,
                {
                    "page_id": self.page_id,
                    "wordpress_post_id": self.wp_page.wordpress_post_id,
                    **(additional_data or {}),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to update progress: {e}")

    def _get_client_context(self) -> str:
        """Build client context string for prompts"""
        if not self.client_context:
            return ""

        context_parts = []

        if self.client_context.name:
            context_parts.append(f"Client: {self.client_context.name}")
        if self.client_context.primary_service:
            context_parts.append(
                f"Primary Service: {self.client_context.primary_service}"
            )
        if self.client_context.secondary_service:
            context_parts.append(
                f"Secondary Service: {self.client_context.secondary_service}"
            )
        if self.client_context.brand_tone:
            context_parts.append(f"Brand Tone: {self.client_context.brand_tone}")
        if self.client_context.location:
            context_parts.append(f"Location: {self.client_context.location}")
        if self.client_context.industry:
            try:
                industry = (
                    json.loads(self.client_context.industry)
                    if isinstance(self.client_context.industry, str)
                    else self.client_context.industry
                )
                if isinstance(industry, list) and industry:
                    context_parts.append(f"Industry: {', '.join(industry)}")
            except (ValueError, TypeError):
                pass

        if self.client_context.target_audience:
            try:
                audience = (
                    json.loads(self.client_context.target_audience)
                    if isinstance(self.client_context.target_audience, str)
                    else self.client_context.target_audience
                )
                if isinstance(audience, list) and audience:
                    context_parts.append(f"Target Audience: {', '.join(audience)}")
            except (ValueError, TypeError):
                pass

        return "\n".join(context_parts)

    def _get_project_context(self) -> str:
        """Build project context string for prompts"""
        if not self.project_context:
            return ""

        context_parts = []

        if self.project_context.name:
            context_parts.append(f"Project: {self.project_context.name}")
        if self.project_context.description:
            context_parts.append(f"Description: {self.project_context.description}")
        if self.project_context.project_type:
            context_parts.append(f"Project Type: {self.project_context.project_type}")
        if self.project_context.content_strategy:
            context_parts.append(
                f"Content Strategy: {self.project_context.content_strategy}"
            )
        if self.project_context.seo_strategy:
            context_parts.append(f"SEO Strategy: {self.project_context.seo_strategy}")

        return "\n".join(context_parts)

    def _get_processing_rule(
        self, rule_type: str = "content_improvement"
    ) -> Optional[Rule]:
        """Get processing rule from database"""
        try:
            # Try to get rule specific to client/project
            rule = None

            if self.project_context:
                # Try project-specific rule
                rule = (
                    db.session.query(Rule)
                    .filter(
                        Rule.project_id == self.project_context.id,
                        Rule.type == "PROMPT_TEMPLATE",
                        Rule.is_active == True,
                        Rule.name.ilike(f"%{rule_type}%"),
                    )
                    .first()
                )

            if not rule and self.client_context:
                # Try client-specific rule
                rule = (
                    db.session.query(Rule)
                    .filter(
                        Rule.level == "CLIENT",
                        Rule.type == "PROMPT_TEMPLATE",
                        Rule.is_active == True,
                        Rule.name.ilike(f"%{rule_type}%"),
                    )
                    .first()
                )

            if not rule:
                # Try global rule
                rule = (
                    db.session.query(Rule)
                    .filter(
                        Rule.level == "SYSTEM",
                        Rule.type == "PROMPT_TEMPLATE",
                        Rule.is_active == True,
                        Rule.name.ilike(f"%{rule_type}%"),
                    )
                    .first()
                )

            return rule
        except Exception as e:
            logger.warning(f"Error fetching processing rule: {e}")
            return None

    def _get_seo_context(self) -> str:
        """Build SEO context string for prompts"""
        seo_context_parts = []

        # Focus keywords
        focus_keywords = []
        if self.wp_page.focus_keywords:
            try:
                focus_keywords = json.loads(self.wp_page.focus_keywords)
            except (ValueError, TypeError):
                pass

        if focus_keywords:
            keywords_str = (
                ", ".join(focus_keywords)
                if isinstance(focus_keywords, list)
                else str(focus_keywords)
            )
            seo_context_parts.append(f"Focus Keywords: {keywords_str}")

        # Current SEO metadata
        if self.wp_page.seo_title:
            seo_context_parts.append(f"Current SEO Title: {self.wp_page.seo_title}")
        if self.wp_page.seo_description:
            seo_context_parts.append(
                f"Current SEO Description: {self.wp_page.seo_description}"
            )

        # SEO score and recommendations
        if self.wp_page.seo_score is not None:
            seo_context_parts.append(f"Current SEO Score: {self.wp_page.seo_score}/100")

            # Get recommendations from SEO processor
            try:
                seo_processor = SEOContentProcessor(self.page_id)
                recommendations = seo_processor.get_recommendations_summary()
                if recommendations.get("recommendations"):
                    seo_context_parts.append("\nSEO Recommendations:")
                    for rec in recommendations["recommendations"][:5]:  # Top 5
                        seo_context_parts.append(f"- {rec}")
            except Exception as e:
                logger.debug(f"Could not get SEO recommendations: {e}")

        # Analytics insights
        analytics_data = {}
        if self.wp_page.analytics_data:
            try:
                analytics_data = json.loads(self.wp_page.analytics_data)
            except (ValueError, TypeError):
                pass

        if analytics_data:
            if analytics_data.get("avg_position"):
                seo_context_parts.append(
                    f"Average Search Position: {analytics_data.get('avg_position')}"
                )
            if analytics_data.get("clicks"):
                seo_context_parts.append(
                    f"Monthly Clicks: {analytics_data.get('clicks')}"
                )

        return "\n".join(seo_context_parts) if seo_context_parts else ""

    def _build_content_improvement_prompt(self) -> str:
        """Build prompt for content improvement"""
        rule = self._get_processing_rule("content_improvement")

        if rule and rule.rule_text:
            base_prompt = rule.rule_text
            logger.info(f"Using content improvement rule: {rule.name}")
        else:
            base_prompt = """You are an expert content editor. Your task is to improve the provided WordPress content while maintaining its core message and intent.

**Improvement Guidelines:**
- Enhance readability and engagement
- Improve clarity and flow
- Maintain the original tone and voice
- Ensure content is well-structured with proper headings
- Add relevant subheadings if needed
- Improve transitions between paragraphs
- Keep the core message intact
- Make the content more engaging and professional
- Naturally incorporate focus keywords when provided

**Original Content:**
Title: {title}
Content: {content}
Excerpt: {excerpt}

**Context:**
{context}

{seo_context}

Please provide improved versions of:
1. Title (make it more compelling while keeping the core message)
2. Content (enhanced version with better structure and flow)
3. Excerpt (improved summary, 150-200 words)

Format your response as JSON:
{{
  "improved_title": "...",
  "improved_content": "...",
  "improved_excerpt": "...",
  "improvement_summary": "Brief explanation of key improvements made"
}}"""

        # Replace placeholders
        context = self._get_client_context()
        project_context = self._get_project_context()
        if project_context:
            context += "\n\n" + project_context

        seo_context = self._get_seo_context()
        seo_section = f"\n**SEO Context:**\n{seo_context}" if seo_context else ""

        prompt = base_prompt.replace("{title}", self.wp_page.title or "")
        prompt = prompt.replace("{content}", self.wp_page.content or "")
        prompt = prompt.replace("{excerpt}", self.wp_page.excerpt or "")
        prompt = prompt.replace(
            "{context}", context or "No additional context provided"
        )
        prompt = prompt.replace("{seo_context}", seo_section)

        return prompt

    def _build_seo_optimization_prompt(self) -> str:
        """Build prompt for SEO optimization"""
        rule = self._get_processing_rule("seo_optimization")

        if rule and rule.rule_text:
            base_prompt = rule.rule_text
            logger.info(f"Using SEO optimization rule: {rule.name}")
        else:
            base_prompt = """You are an SEO expert. Your task is to optimize the provided WordPress content for search engines.

**SEO Optimization Guidelines:**
- Create compelling meta title (50-60 characters, includes primary keyword)
- Write engaging meta description (150-160 characters, includes call-to-action)
- Ensure title is SEO-friendly while remaining natural
- Optimize content for target keywords naturally
- Improve heading structure (H1, H2, H3 hierarchy)
- Ensure keyword density is appropriate (2-3%)
- Use focus keywords strategically throughout content
- Consider current search performance when optimizing

**Current Content:**
Title: {title}
Content: {content}
Excerpt: {excerpt}
Current Meta Title: {meta_title}
Current Meta Description: {meta_description}

**Context:**
{context}

{seo_context}

Please provide optimized versions:
1. SEO-optimized Title
2. Meta Title (50-60 chars)
3. Meta Description (150-160 chars)
4. Improved Content (with better SEO structure)

Format your response as JSON:
{{
  "improved_title": "...",
  "improved_meta_title": "...",
  "improved_meta_description": "...",
  "improved_content": "...",
  "improvement_summary": "Brief explanation of SEO improvements made"
}}"""

        # Replace placeholders
        context = self._get_client_context()
        project_context = self._get_project_context()
        if project_context:
            context += "\n\n" + project_context

        # Get current meta data
        meta_title = self.wp_page.seo_title or ""
        meta_description = self.wp_page.seo_description or ""

        # Fallback to meta_data if seo fields are empty
        if not meta_title or not meta_description:
            meta_data = {}
            if self.wp_page.meta_data:
                try:
                    meta_data = json.loads(self.wp_page.meta_data)
                except (ValueError, TypeError):
                    pass

            if not meta_title:
                meta_title = (
                    meta_data.get("meta_title", "")
                    or meta_data.get("title", "")
                    or meta_data.get("rank_math_title", "")
                    or meta_data.get("seo_concept_title", "")
                )
            if not meta_description:
                meta_description = (
                    meta_data.get("meta_description", "")
                    or meta_data.get("description", "")
                    or meta_data.get("rank_math_description", "")
                    or meta_data.get("seo_concept_description", "")
                )

        seo_context = self._get_seo_context()
        seo_section = f"\n**SEO Context:**\n{seo_context}" if seo_context else ""

        prompt = base_prompt.replace("{title}", self.wp_page.title or "")
        prompt = prompt.replace("{content}", self.wp_page.content or "")
        prompt = prompt.replace("{excerpt}", self.wp_page.excerpt or "")
        prompt = prompt.replace("{meta_title}", meta_title)
        prompt = prompt.replace("{meta_description}", meta_description)
        prompt = prompt.replace(
            "{context}", context or "No additional context provided"
        )
        prompt = prompt.replace("{seo_context}", seo_section)

        return prompt

    def _build_schema_markup_prompt(self) -> str:
        """Build prompt for schema markup generation"""
        rule = self._get_processing_rule("schema_markup")

        if rule and rule.rule_text:
            base_prompt = rule.rule_text
            logger.info(f"Using schema markup rule: {rule.name}")
        else:
            base_prompt = """You are a schema.org markup expert. Generate appropriate structured data (JSON-LD) for the provided content.

**Content:**
Title: {title}
Content: {content}
Type: {post_type}

**Context:**
{context}

Determine the most appropriate schema type (Article, WebPage, BlogPosting, LocalBusiness, etc.) and generate valid JSON-LD schema markup.

Format your response as JSON:
{{
  "schema_type": "Article",
  "schema_markup": {{...}},
  "improvement_summary": "Explanation of schema choice and key properties"
}}"""

        context = self._get_client_context()
        project_context = self._get_project_context()
        if project_context:
            context += "\n\n" + project_context

        prompt = base_prompt.replace("{title}", self.wp_page.title or "")
        prompt = prompt.replace(
            "{content}", self.wp_page.content[:1000] or ""
        )  # Limit content length
        prompt = prompt.replace("{post_type}", self.wp_page.post_type or "post")
        prompt = prompt.replace(
            "{context}", context or "No additional context provided"
        )

        return prompt

    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """Parse LLM response, handling both JSON and plain text"""
        try:
            # Try to parse as JSON first
            if response_text.strip().startswith("{"):
                return json.loads(response_text)

            # Try to extract JSON from markdown code blocks
            import re

            json_match = re.search(
                r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL
            )
            if json_match:
                return json.loads(json_match.group(1))

            json_match = re.search(r"```\s*(\{.*?\})\s*```", response_text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))

            # If no JSON found, try to extract key-value pairs
            result = {}
            lines = response_text.split("\n")
            current_key = None
            current_value = []

            for line in lines:
                line = line.strip()
                if ":" in line and not line.startswith("-"):
                    if current_key:
                        result[current_key] = "\n".join(current_value).strip()
                    parts = line.split(":", 1)
                    current_key = parts[0].strip().lower().replace(" ", "_")
                    current_value = [parts[1].strip()] if len(parts) > 1 else []
                elif current_key:
                    current_value.append(line)

            if current_key:
                result[current_key] = "\n".join(current_value).strip()

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
            return {"raw_response": response_text}

    def process_content_improvement(self) -> Dict[str, Any]:
        """
        Process and improve content using LLM

        Returns:
            Dict with processing results
        """
        try:
            if not self.llm:
                return {"success": False, "error": "LLM instance not available"}

            # Update status
            self.wp_page.process_status = "processing"
            self.wp_page.updated_at = datetime.now()
            db.session.commit()

            # Initialize progress tracking
            self.progress_system.create_process(
                ProcessType.WORDPRESS_PROCESSING,
                f"Improving content for: {self.wp_page.title[:50]}",
            )

            self._update_progress(10, "Building improvement prompt...")

            # Build prompt
            prompt = self._build_content_improvement_prompt()

            self._update_progress(30, "Sending to LLM for content improvement...")

            # Call LLM
            system_prompt = "You are an expert content editor specializing in improving WordPress content. Always respond with valid JSON format."
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
                ChatMessage(role=MessageRole.USER, content=prompt),
            ]

            llm_response = self.llm.chat(messages)
            response_text = _safe_content(llm_response.message) or ""

            if not response_text:
                raise ValueError("Empty response from LLM")

            self._update_progress(70, "Parsing LLM response...")

            # Parse response
            improvements = self._parse_llm_response(response_text)

            # Extract improvements
            improved_title = improvements.get("improved_title") or improvements.get(
                "title"
            )
            improved_content = improvements.get("improved_content") or improvements.get(
                "content"
            )
            improved_excerpt = improvements.get("improved_excerpt") or improvements.get(
                "excerpt"
            )
            improvement_summary = improvements.get(
                "improvement_summary"
            ) or improvements.get("summary")

            self._update_progress(90, "Saving improvements...")

            # Save improvements
            if improved_title:
                self.wp_page.improved_title = improved_title
            if improved_content:
                self.wp_page.improved_content = improved_content
            if improved_excerpt:
                self.wp_page.improved_excerpt = improved_excerpt
            if improvement_summary:
                self.wp_page.improvement_summary = improvement_summary

            self.wp_page.process_status = "completed"
            self.wp_page.processed_at = datetime.now()
            self.wp_page.updated_at = datetime.now()

            db.session.commit()

            self.progress_system.complete_process(
                self.job_id,
                f"Content improvement completed for: {self.wp_page.title[:50]}",
            )

            logger.info(f"Content improvement completed for page {self.page_id}")

            return {
                "success": True,
                "data": {
                    "page_id": self.page_id,
                    "improved_title": improved_title,
                    "improved_content": (
                        improved_content[:500] if improved_content else None
                    ),
                    "improved_excerpt": improved_excerpt,
                    "improvement_summary": improvement_summary,
                },
                "job_id": self.job_id,
            }

        except Exception as e:
            logger.error(f"Error processing content improvement: {e}", exc_info=True)
            db.session.rollback()

            self.wp_page.process_status = "pending"  # Reset to pending on error
            self.wp_page.updated_at = datetime.now()
            db.session.commit()

            self.progress_system.error_process(self.job_id, str(e))

            return {"success": False, "error": str(e), "job_id": self.job_id}

    def process_seo_optimization(self) -> Dict[str, Any]:
        """
        Process and optimize SEO metadata using LLM

        Returns:
            Dict with SEO optimization results
        """
        try:
            if not self.llm:
                return {"success": False, "error": "LLM instance not available"}

            self.wp_page.process_status = "processing"
            self.wp_page.updated_at = datetime.now()
            db.session.commit()

            self.progress_system.create_process(
                ProcessType.WORDPRESS_PROCESSING,
                f"SEO optimization for: {self.wp_page.title[:50]}",
            )

            self._update_progress(10, "Building SEO optimization prompt...")

            prompt = self._build_seo_optimization_prompt()

            self._update_progress(30, "Sending to LLM for SEO optimization...")

            system_prompt = "You are an SEO expert specializing in WordPress content optimization. Always respond with valid JSON format."
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
                ChatMessage(role=MessageRole.USER, content=prompt),
            ]

            llm_response = self.llm.chat(messages)
            response_text = _safe_content(llm_response.message) or ""

            if not response_text:
                raise ValueError("Empty response from LLM")

            self._update_progress(70, "Parsing SEO improvements...")

            improvements = self._parse_llm_response(response_text)

            # Extract SEO improvements
            improved_title = improvements.get("improved_title") or improvements.get(
                "title"
            )
            improved_meta_title = improvements.get(
                "improved_meta_title"
            ) or improvements.get("meta_title")
            improved_meta_description = improvements.get(
                "improved_meta_description"
            ) or improvements.get("meta_description")
            improved_content = improvements.get("improved_content") or improvements.get(
                "content"
            )
            improvement_summary = improvements.get(
                "improvement_summary"
            ) or improvements.get("summary")

            self._update_progress(90, "Saving SEO improvements...")

            # Save improvements
            if improved_title:
                self.wp_page.improved_title = improved_title
            if improved_meta_title:
                self.wp_page.improved_meta_title = improved_meta_title
            if improved_meta_description:
                self.wp_page.improved_meta_description = improved_meta_description
            if improved_content:
                self.wp_page.improved_content = improved_content
            if improvement_summary:
                self.wp_page.improvement_summary = improvement_summary

            # Recalculate SEO score after optimization
            try:
                seo_processor = SEOContentProcessor(self.page_id)
                seo_processor.analyze_seo_score()
            except Exception as e:
                logger.debug(f"Could not recalculate SEO score: {e}")

            self.wp_page.process_status = "completed"
            self.wp_page.processed_at = datetime.now()
            self.wp_page.updated_at = datetime.now()

            db.session.commit()

            self.progress_system.complete_process(
                self.job_id,
                f"SEO optimization completed for: {self.wp_page.title[:50]}",
            )

            logger.info(f"SEO optimization completed for page {self.page_id}")

            return {
                "success": True,
                "data": {
                    "page_id": self.page_id,
                    "improved_title": improved_title,
                    "improved_meta_title": improved_meta_title,
                    "improved_meta_description": improved_meta_description,
                    "improvement_summary": improvement_summary,
                },
                "job_id": self.job_id,
            }

        except Exception as e:
            logger.error(f"Error processing SEO optimization: {e}", exc_info=True)
            db.session.rollback()

            self.wp_page.process_status = "pending"
            self.wp_page.updated_at = datetime.now()
            db.session.commit()

            self.progress_system.error_process(self.job_id, str(e))

            return {"success": False, "error": str(e), "job_id": self.job_id}

    def process_full_improvement(self) -> Dict[str, Any]:
        """
        Process full improvement: content + SEO + schema

        Returns:
            Dict with full processing results
        """
        try:
            # Step 1: Content improvement
            content_result = self.process_content_improvement()
            if not content_result["success"]:
                return content_result

            # Step 2: SEO optimization
            seo_result = self.process_seo_optimization()
            if not seo_result["success"]:
                logger.warning(
                    f"SEO optimization failed, but content improvement succeeded: {seo_result.get('error')}"
                )

            # Step 3: Schema markup (optional)
            try:
                schema_result = self.process_schema_generation()
                if not schema_result["success"]:
                    logger.warning(
                        f"Schema generation failed: {schema_result.get('error')}"
                    )
            except Exception as e:
                logger.warning(f"Schema generation skipped: {e}")

            return {
                "success": True,
                "content_improvement": content_result["success"],
                "seo_optimization": seo_result["success"],
                "data": {
                    "page_id": self.page_id,
                    **content_result.get("data", {}),
                    **seo_result.get("data", {}),
                },
                "job_id": self.job_id,
            }

        except Exception as e:
            logger.error(f"Error in full improvement: {e}", exc_info=True)
            return {"success": False, "error": str(e), "job_id": self.job_id}

    def process_schema_generation(self) -> Dict[str, Any]:
        """
        Generate schema markup for the content

        Returns:
            Dict with schema generation results
        """
        try:
            if not self.llm:
                return {"success": False, "error": "LLM instance not available"}

            self._update_progress(10, "Building schema generation prompt...")

            prompt = self._build_schema_markup_prompt()

            self._update_progress(30, "Generating schema markup...")

            system_prompt = "You are a schema.org expert. Generate valid JSON-LD structured data. Always respond with valid JSON format."
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
                ChatMessage(role=MessageRole.USER, content=prompt),
            ]

            llm_response = self.llm.chat(messages)
            response_text = _safe_content(llm_response.message) or ""

            if not response_text:
                raise ValueError("Empty response from LLM")

            self._update_progress(70, "Parsing schema markup...")

            schema_data = self._parse_llm_response(response_text)

            schema_markup = (
                schema_data.get("schema_markup")
                or schema_data.get("schema")
                or schema_data
            )

            self._update_progress(90, "Saving schema markup...")

            # Save schema
            if isinstance(schema_markup, dict):
                self.wp_page.improved_schema = json.dumps(schema_markup)
            else:
                self.wp_page.improved_schema = str(schema_markup)

            self.wp_page.updated_at = datetime.now()
            db.session.commit()

            self.progress_system.complete_process(
                self.job_id, f"Schema generation completed"
            )

            return {
                "success": True,
                "data": {"page_id": self.page_id, "schema_markup": schema_markup},
                "job_id": self.job_id,
            }

        except Exception as e:
            logger.error(f"Error generating schema: {e}", exc_info=True)
            return {"success": False, "error": str(e), "job_id": self.job_id}
