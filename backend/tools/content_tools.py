#!/usr/bin/env python3
"""
Content Generation Tools
Executable tools for content generation, converted from legacy command rules.
These tools wrap existing generation services and expose them to the agent system.
"""

import csv
import io
import logging
import re
from typing import Dict, Any, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

# The enhanced tool's grounding: how many knowledge-base passages the model gets,
# and how much of each.
GROUNDING_PASSAGES = 6
GROUNDING_CHARS = 700


def _reply_text(response) -> str:
    """The text of an LLM chat response, whether content or blocks carry it."""
    if not response.message:
        return ""
    try:
        return str(response.message.content).strip()
    except (ValueError, AttributeError):
        blocks = getattr(response.message, 'blocks', [])
        return next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "").strip()


def _csv_row(reply: str, columns: int, row_id: Any) -> Optional[str]:
    """The first record in the model's reply with exactly ``columns`` fields and a
    numeric first field, re-quoted as one CSV row whose ID is ``row_id``; None when
    the reply holds no such record."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    def quoted(fields: list) -> str:
        out = io.StringIO()
        csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="").writerow([str(row_id)] + fields[1:])
        return out.getvalue()

    def plausible(fields: list) -> bool:
        # A page row has an ID, a title and HTML content where the prompt put them;
        # content that does not end in a tag was split at a comma inside it. HTML or
        # a line break in any other column, or a slug with spaces or quotes, means
        # the fields shifted (content split in two, or prose after the row).
        if not (len(fields) == columns and fields[0].strip().isdigit()
                and bool(fields[1].strip()) and "<" in fields[2]
                and fields[2].rstrip().endswith(">")):
            return False
        others = [fields[1]] + fields[3:]
        if any("<" in f or "\n" in f.strip() for f in others):
            return False
        return re.fullmatch(r'[^\s"<>]+', fields[-1].strip()) is not None

    # The prompt asks for double-quoted fields; an unquoted row cannot be split safely.
    if not re.search(r'"\s*\d+\s*"\s*,', text):
        return None

    try:
        # strict: a stray unescaped quote inside a field is an error, not a silently
        # mangled field.
        for record in csv.reader(io.StringIO(text), strict=True, skipinitialspace=True):
            if plausible(record):
                return quoted(record)
    except csv.Error:
        pass
    # Models often leave a bare double quote inside HTML (class="x"). When the
    # reply still splits into exactly `columns` fields on the "," separators, those
    # quotes are content, and re-quoting the fields escapes them.
    match = re.search(r'"\s*\d+\s*",', text)
    if match:
        body = text[match.start():].strip()
        if body.endswith('"'):
            fields = [_undoubled(f) for f in re.split(r'"\s*,\s*"', body[1:-1])]
            if plausible(fields):
                return quoted(fields)
    return None


def _undoubled(field: str) -> str:
    """A field the model escaped the CSV way ("" for each quote) with its quotes
    single again; any other field as it is.

    A reply can mix the two styles: bare quotes in the title, escaped ones in the
    HTML. Re-quoting an escaped field as it stands would double its quotes again.
    A field is escaped when every run of quotes in it has an even length. An
    empty attribute written bare (alt="") looks the same and is left alone.
    """
    runs = re.findall(r'"+', field)
    if not runs or any(len(run) % 2 for run in runs):
        return field
    if re.search(r'=""(?:\s|/?>|$)', field):
        return field
    return field.replace('""', '"')


def _generate_row(llm, prompt: str, columns: int, row_id: Any) -> tuple[Optional[str], str]:
    """Ask for one CSV row; on a malformed reply ask once more with the reason.
    Returns (row or None, the last reply)."""
    from backend.utils.llm_service import ChatMessage, MessageRole

    messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
    reply = _reply_text(llm.chat(messages))
    row = _csv_row(reply, columns, row_id)
    if row is None:
        messages += [
            ChatMessage(role=MessageRole.ASSISTANT, content=reply),
            ChatMessage(role=MessageRole.USER, content=(
                f"That was not one valid CSV row. Reply with exactly one row of {columns} "
                "double-quoted fields and nothing else. Inside a field, write every double "
                'quote as two double quotes ("").'
            )),
        ]
        reply = _reply_text(llm.chat(messages))
        row = _csv_row(reply, columns, row_id)
    return row, reply


class WordPressContentTool(BaseTool):
    """
    Generate WordPress-compatible CSV content for a client.
    Converted from /wordpress command rule (rule ID: 16).

    Generates a single CSV row with: ID, Title, Content, Meta Description, Keywords, Slug
    """

    name = "generate_wordpress_content"
    read_only = True
    # Changes nothing, but the page it writes is the deliverable: plan it, then run it.
    plan_research = False
    description = (
        "Compose one WordPress page with Guaardvark's local LLM and return it as a single CSV row of "
        "text, six double-quoted columns with no header: ID, title, HTML content, meta description, "
        "keywords, slug. Nothing is written to disk. The page comes from the parameters alone, with no "
        "document lookup; to ground it in the user's indexed documents and keep it to named services "
        "use generate_enhanced_wordpress_content, and for many pages in one import file use "
        "generate_bulk_csv. The ID column is always row_id; a reply without six fields, a title and "
        "HTML content, or with HTML or line breaks outside the content column, after one retry is an "
        "error. Each model call is cut off after 180 s, and over MCP a call still running at the "
        "timeout (120 s by default) returns an error."
    )

    parameters = {
        "client": ToolParameter(
            name="client",
            type="string",
            required=True,
            description="Company the page is written for; the model uses the name in the copy."
        ),
        "website": ToolParameter(
            name="website",
            type="string",
            required=False,
            description="Company website URL, given to the model as context only; it is not fetched.",
            default=""
        ),
        "topic": ToolParameter(
            name="topic",
            type="string",
            required=True,
            description="What the page is about, e.g. 'spring lawn care checklist'."
        ),
        "row_id": ToolParameter(
            name="row_id",
            type="int",
            required=True,
            minimum=0,
            description="Value written to the ID column. Use increasing numbers when building an import file one page at a time."
        ),
        "word_count": ToolParameter(
            name="word_count",
            type="int",
            required=False,
            minimum=50,
            description="Minimum length of the HTML content the model is asked for, in words (default 500). It is an instruction, not checked.",
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

            row, reply = _generate_row(llm, prompt, 6, row_id)
            if row is None:
                return ToolResult(
                    success=False,
                    error=(
                        "The model did not return a valid 6-column CSV row after a retry. "
                        f"Its reply began: {reply[:300]!r}"
                    ),
                )
            csv_content = row

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
    read_only = True
    # Changes nothing, but the page it writes is the deliverable: plan it, then run it.
    plan_research = False
    description = (
        "Compose one WordPress page grounded in the user's own indexed documents: it searches the whole "
        "knowledge base for the client, topic and services, gives Guaardvark's local LLM up to 6 "
        "passages the reranker rates related (check the sources: two clients' documents in one index "
        "can mix), asks for 500+ words of HTML content, and tells it to take specific facts only from "
        "the passages and, when services are given, to write only about primary_service and "
        "secondary_service. Returns a "
        "single CSV row of text, seven double-quoted columns with no header: ID, title, HTML content, "
        "excerpt, category, tags, slug; over MCP it comes as JSON {row, sources} naming the files the "
        "passages came from. Nothing is written to disk. The ID column is always row_id; a reply "
        "without seven fields, a title and HTML content, or with HTML or line breaks outside the "
        "content column, after one retry is an error, and so is a knowledge base that cannot be "
        "searched or has fallen back to an empty index. Each model call is cut off after 180 s, and "
        "over MCP a call still running at the timeout (120 s by default) returns an error. "
        "Without grounding or service limits use generate_wordpress_content; for many pages in one "
        "import file, generate_bulk_csv."
    )

    parameters = {
        "client": ToolParameter(
            name="client",
            type="string",
            required=True,
            description="Company the page is written for; part of the knowledge-base search and used in the copy."
        ),
        "website": ToolParameter(
            name="website",
            type="string",
            required=False,
            description="Company website URL, given to the model as context only; it is not fetched.",
            default=""
        ),
        "topic": ToolParameter(
            name="topic",
            type="string",
            required=True,
            description="What the page is about, e.g. 'emergency repair response times'; part of the knowledge-base search."
        ),
        "row_id": ToolParameter(
            name="row_id",
            type="int",
            required=True,
            minimum=0,
            description="Value written to the ID column. Use increasing numbers when building an import file one page at a time."
        ),
        "industry": ToolParameter(
            name="industry",
            type="string",
            required=False,
            description="The company's industry; the model is told all content must fit it.",
            default=""
        ),
        "primary_service": ToolParameter(
            name="primary_service",
            type="string",
            required=False,
            description="Main service the page may cover; part of the search. With secondary_service, the only services the model is told to write about; if both are empty there is no service limit.",
            default=""
        ),
        "secondary_service": ToolParameter(
            name="secondary_service",
            type="string",
            required=False,
            description="Second service the page may cover; part of the search.",
            default=""
        ),
        "target_audience": ToolParameter(
            name="target_audience",
            type="string",
            required=False,
            description="Who the page is written for, e.g. 'first-time homeowners'.",
            default=""
        ),
        "brand_tone": ToolParameter(
            name="brand_tone",
            type="string",
            required=False,
            description="Voice for the copy, e.g. 'friendly' or 'formal' (default 'professional').",
            default="professional"
        ),
        "location": ToolParameter(
            name="location",
            type="string",
            required=False,
            description="Town or region to name for local search, e.g. 'Erie, PA'.",
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

    def _grounding(self, query: str) -> tuple[list, Optional[str]]:
        """Passages from the user's indexed documents about this page, as
        ([{"source", "text"}], None), or ([], why the search could not run)."""
        from backend.tools.rag_tools import KnowledgeSearchTool
        from backend.utils.backend_http import is_mcp_transport

        search = KnowledgeSearchTool()
        if is_mcp_transport(self):
            search.set_context({"transport": "mcp"})
        result = search.execute(query=query, top_k=GROUNDING_PASSAGES)
        if not result.success:
            return [], result.error or "the knowledge-base search failed"
        # A retrieval error, or the empty in-memory store standing in for the
        # persisted index, is a broken index, not "nothing matched". Other degraded
        # modes (keyword-only or vector-only under pressure) still return real passages.
        trace = (result.metadata or {}).get("retrieval") or {}
        if trace.get("error"):
            return [], str(trace["error"])
        if trace.get("vector_store") == "simple_fallback":
            return [], trace.get("degraded_reason") or "the persisted vector index is not in use"
        from backend.utils.reranker import drop_unrelated
        hits, _dropped = drop_unrelated((result.metadata or {}).get("results") or [])
        passages = []
        for hit in hits[:GROUNDING_PASSAGES]:
            meta = hit.get("metadata") or {}
            text = (hit.get("text") or "").strip()
            if text:
                passages.append({
                    "source": meta.get("source_filename") or meta.get("file_path") or "unknown source",
                    "text": text[:GROUNDING_CHARS],
                })
        return passages, None

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
            query = " ".join(p for p in (client, topic, primary_service, secondary_service) if p)
            passages, search_error = self._grounding(query)
            if search_error:
                return ToolResult(
                    success=False,
                    error=(
                        f"Could not search the knowledge base: {search_error}. "
                        "generate_wordpress_content writes a page without it."
                    ),
                )
            if passages:
                facts = "\n\n".join(f"[{i}] ({p['source']}) {p['text']}" for i, p in enumerate(passages, 1))
                grounding = (
                    "**FACTS FROM THE USER'S INDEXED DOCUMENTS** (the only source for specific claims; "
                    "use only those that concern this company; do not invent prices, dates, awards, "
                    "certifications, numbers or other specifics):\n" + facts
                )
            else:
                grounding = (
                    "**FACTS:** none of the indexed documents matched. Write only general content and do "
                    "not invent prices, dates, awards, certifications, numbers or other specifics."
                )

            services = [s for s in (primary_service, secondary_service) if s]
            rules = []
            if services:
                rules += [
                    f"ONLY write about services explicitly listed: {' and '.join(services)}",
                    "FORBIDDEN: Do NOT write about ANY service not listed in Primary/Secondary Service",
                ]
            if industry:
                rules.append(f'Industry Context: ALL content MUST align with "{industry}"')
            rules.append("Take specific facts only from the FACTS section above")
            constraints = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))

            llm = self._get_llm()

            # Build enhanced prompt with business intelligence
            prompt = f"""CSV GENERATION GROUNDED IN THE CLIENT'S DOCUMENTS

TASK: Generate professional CSV content for {client}: {website}

**BUSINESS INTELLIGENCE:**
- Company: {client}
- Industry: {industry}
- Primary Service: {primary_service}
- Secondary Service: {secondary_service}
- Target Audience: {target_audience}
- Brand Tone: {brand_tone}
- Location: {location}

{grounding}

**CRITICAL CONSTRAINTS:**
{constraints}

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
- Focus on {topic}{f" within {primary_service} context" if primary_service else ""}
- Location for SEO: {location}

Generate the single CSV row now:"""

            row, reply = _generate_row(llm, prompt, 7, row_id)
            if row is None:
                return ToolResult(
                    success=False,
                    error=(
                        "The model did not return a valid 7-column CSV row after a retry. "
                        f"Its reply began: {reply[:300]!r}"
                    ),
                )
            sources = list(dict.fromkeys(p["source"] for p in passages))
            from backend.utils.backend_http import is_mcp_transport
            return ToolResult(
                success=True,
                # Chat treats this output as CSV; an MCP client also needs to know
                # which documents the facts came from.
                output={"row": row, "sources": sources} if is_mcp_transport(self) else row,
                metadata={
                    "client": client,
                    "topic": topic,
                    "row_id": row_id,
                    "industry": industry,
                    "primary_service": primary_service,
                    "sources": sources,
                    "format": "csv_row_enhanced"
                }
            )

        except Exception as e:
            logger.error(f"Enhanced WordPress content generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Enhanced content generation failed: {str(e)}"
            )
