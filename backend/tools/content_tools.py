#!/usr/bin/env python3
"""
Content Generation Tools
Executable tools for content generation, converted from legacy command rules.
These tools wrap existing generation services and expose them to the agent system.
"""

import logging
from typing import Dict, Any, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


class WordPressContentTool(BaseTool):
    """
    Generate WordPress-compatible CSV content for a client.
    Converted from /wordpress command rule (rule ID: 16).

    Generates a single CSV row with: ID, Title, Content, Meta Description, Keywords, Slug
    """

    name = "generate_wordpress_content"
    description = "Generate a WordPress-compatible CSV row with SEO-optimized content for a specific topic and client"

    parameters = {
        "client": ToolParameter(
            name="client",
            type="string",
            required=True,
            description="Client/company name for content personalization"
        ),
        "website": ToolParameter(
            name="website",
            type="string",
            required=False,
            description="Client website URL for context",
            default=""
        ),
        "topic": ToolParameter(
            name="topic",
            type="string",
            required=True,
            description="Content topic or subject to write about"
        ),
        "row_id": ToolParameter(
            name="row_id",
            type="int",
            required=True,
            description="Unique row ID for the CSV entry"
        ),
        "word_count": ToolParameter(
            name="word_count",
            type="int",
            required=False,
            description="Target word count for the content section",
            default=500
        )
    }

    def __init__(self):
        super().__init__()
        self._llm = None

    def _get_llm(self):
        """Lazy load LLM service"""
        if self._llm is None:
            try:
                from backend.utils.llm_service import get_default_llm
                self._llm = get_default_llm()
            except Exception as e:
                logger.error(f"Failed to initialize LLM: {e}")
                raise
        return self._llm

    def execute(self, **kwargs) -> ToolResult:
        """Generate WordPress CSV content"""
        client = kwargs.get("client")
        website = kwargs.get("website", "")
        topic = kwargs.get("topic")
        row_id = kwargs.get("row_id")
        word_count = kwargs.get("word_count", 500)

        try:
            llm = self._get_llm()

            # Build the generation prompt (based on /wordpress rule)
            prompt = f"""TASK: Generate comprehensive CSV content for {client}: {website}

REQUIREMENTS:
- Generate EXACTLY ONE CSV DATA ROW (no headers)
- Do NOT include any explanations, disclaimers, or meta-text
- Create unique, professional content for {client}
- Each page: {word_count}+ words minimum PER CONTENT SECTION
- SEO optimized for #1 ranking
- Topic: {topic}

CSV FORMAT (EXACTLY 6 COLUMNS - DATA ROW ONLY):
"{row_id}","[TITLE]","[HTML_CONTENT]","[META_DESCRIPTION]","[KEYWORDS]","[SLUG]"

COLUMN SPECIFICATIONS:
- TITLE: Professional, SEO-optimized title (50-70 characters)
- HTML_CONTENT: {word_count}+ word professional content with HTML formatting (h2, h3, p, b, br tags)
- META_DESCRIPTION: SEO meta description (150-160 characters)
- KEYWORDS: Comma-separated keywords for SEO targeting
- SLUG: URL-friendly slug (lowercase, hyphens, no spaces)

GENERATE THE SINGLE CSV DATA ROW NOW:"""

            # Call LLM
            from backend.utils.llm_service import ChatMessage, MessageRole
            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            response = llm.chat(messages)

            if response.message:
                try:
                    csv_content = str(response.message.content).strip()
                except (ValueError, AttributeError):
                    blocks = getattr(response.message, 'blocks', [])
                    csv_content = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                    csv_content = csv_content.strip()
            else:
                csv_content = ""

            # Validate response looks like CSV
            if not csv_content.startswith('"'):
                logger.warning("Generated content doesn't look like CSV, attempting to extract")
                # Try to find CSV-like content
                import re
                csv_match = re.search(r'"[^"]*","[^"]*","[^"]*","[^"]*","[^"]*","[^"]*"', csv_content)
                if csv_match:
                    csv_content = csv_match.group(0)

            return ToolResult(
                success=True,
                output=csv_content,
                metadata={
                    "client": client,
                    "topic": topic,
                    "row_id": row_id,
                    "word_count_target": word_count,
                    "format": "csv_row"
                }
            )

        except Exception as e:
            logger.error(f"WordPress content generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Content generation failed: {str(e)}"
            )


class EnhancedWordPressContentTool(BaseTool):
    """
    RAG-enhanced WordPress CSV generation with business intelligence.
    Converted from /wordpress_enhanced command rule (rule ID: 19).

    Uses client profile, industry context, and forbidden topic constraints.
    """

    name = "generate_enhanced_wordpress_content"
    description = "Generate WordPress CSV content with RAG-enhanced business intelligence and topic constraints"

    parameters = {
        "client": ToolParameter(
            name="client",
            type="string",
            required=True,
            description="Client/company name"
        ),
        "website": ToolParameter(
            name="website",
            type="string",
            required=False,
            description="Client website URL",
            default=""
        ),
        "topic": ToolParameter(
            name="topic",
            type="string",
            required=True,
            description="Content topic"
        ),
        "row_id": ToolParameter(
            name="row_id",
            type="int",
            required=True,
            description="Unique row ID"
        ),
        "industry": ToolParameter(
            name="industry",
            type="string",
            required=False,
            description="Client industry for context",
            default=""
        ),
        "primary_service": ToolParameter(
            name="primary_service",
            type="string",
            required=False,
            description="Client's primary service offering",
            default=""
        ),
        "secondary_service": ToolParameter(
            name="secondary_service",
            type="string",
            required=False,
            description="Client's secondary service offering",
            default=""
        ),
        "target_audience": ToolParameter(
            name="target_audience",
            type="string",
            required=False,
            description="Target audience for content",
            default=""
        ),
        "brand_tone": ToolParameter(
            name="brand_tone",
            type="string",
            required=False,
            description="Brand voice/tone",
            default="professional"
        ),
        "location": ToolParameter(
            name="location",
            type="string",
            required=False,
            description="Geographic location for local SEO",
            default=""
        )
    }

    def __init__(self):
        super().__init__()
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from backend.utils.llm_service import get_default_llm
            self._llm = get_default_llm()
        return self._llm

    def execute(self, **kwargs) -> ToolResult:
        """Generate enhanced WordPress CSV content with business context"""
        client = kwargs.get("client")
        website = kwargs.get("website", "")
        topic = kwargs.get("topic")
        row_id = kwargs.get("row_id")
        industry = kwargs.get("industry", "")
        primary_service = kwargs.get("primary_service", "")
        secondary_service = kwargs.get("secondary_service", "")
        target_audience = kwargs.get("target_audience", "")
        brand_tone = kwargs.get("brand_tone", "professional")
        location = kwargs.get("location", "")

        try:
            llm = self._get_llm()

            # Build enhanced prompt with business intelligence
            prompt = f"""COMPREHENSIVE CSV GENERATION WITH RAG INTELLIGENCE

TASK: Generate professional CSV content for {client}: {website}

**BUSINESS INTELLIGENCE:**
- Company: {client}
- Industry: {industry}
- Primary Service: {primary_service}
- Secondary Service: {secondary_service}
- Target Audience: {target_audience}
- Brand Tone: {brand_tone}
- Location: {location}

**CRITICAL CONSTRAINTS:**
1. ONLY write about services explicitly listed: {primary_service} and {secondary_service}
2. FORBIDDEN: Do NOT write about ANY service not listed in Primary/Secondary Service
3. Industry Context: ALL content MUST align with "{industry}"

**CSV OUTPUT FORMAT (7 COLUMNS):**
"{row_id}","[TITLE]","[CONTENT]","[EXCERPT]","[CATEGORY]","[TAGS]","[SLUG]"

**COLUMN SPECIFICATIONS:**
1. ID: Use {row_id} exactly
2. TITLE: SEO-optimized title (50-70 chars) about {topic}
3. CONTENT: 500+ words with HTML formatting, single quotes for attributes
4. EXCERPT: Plain text summary (150-180 chars)
5. CATEGORY: Single category (2 words max, NO brackets, NO company name)
6. TAGS: 3-5 keywords, comma-separated
7. SLUG: lowercase-with-hyphens from title

**CONTENT QUALITY:**
- Professional tone matching {brand_tone}
- Target audience: {target_audience}
- Focus on {topic} within {primary_service} context
- Location for SEO: {location}

Generate the single CSV row now:"""

            from backend.utils.llm_service import ChatMessage, MessageRole
            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            response = llm.chat(messages)

            if response.message:
                try:
                    csv_content = str(response.message.content).strip()
                except (ValueError, AttributeError):
                    blocks = getattr(response.message, 'blocks', [])
                    csv_content = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                    csv_content = csv_content.strip()
            else:
                csv_content = ""

            return ToolResult(
                success=True,
                output=csv_content,
                metadata={
                    "client": client,
                    "topic": topic,
                    "row_id": row_id,
                    "industry": industry,
                    "primary_service": primary_service,
                    "format": "csv_row_enhanced"
                }
            )

        except Exception as e:
            logger.error(f"Enhanced WordPress content generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Enhanced content generation failed: {str(e)}"
            )
