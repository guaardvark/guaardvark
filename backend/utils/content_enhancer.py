"""
Content enhancer module for CSV generation
Enhances LLM-generated content by adding missing HTML structure without rejecting content.
Philosophy: Preserve substance, enhance structure.
"""

import re
import logging
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

# Minimum thresholds (for logging, not rejection)
MIN_WORD_COUNT = 500
MIN_PARAGRAPH_COUNT = 3

# HTML tags we care about
BLOCK_TAGS = [
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "div",
    "ul",
    "ol",
    "li",
    "blockquote",
    "section",
]
INLINE_TAGS = ["strong", "b", "em", "i", "a", "span"]
ALL_TAGS = BLOCK_TAGS + INLINE_TAGS

# Patterns for detecting structure in plain text
LIST_BULLET_PATTERN = re.compile(r"^[\s]*[-•●○▪▸►]\s+(.+)$", re.MULTILINE)
LIST_NUMBER_PATTERN = re.compile(r"^[\s]*(\d+)[.)]\s+(.+)$", re.MULTILINE)
EMPHASIS_PATTERN = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*")


def has_html_tags(content: str) -> bool:
    """
    Check if content contains HTML tags

    Args:
        content: Raw content string

    Returns:
        True if HTML tags are present
    """
    if not content:
        return False

    # Look for any HTML tag pattern
    html_pattern = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", re.IGNORECASE)
    return bool(html_pattern.search(content))


def count_html_tags(content: str) -> dict:
    """
    Count occurrences of different HTML tags

    Args:
        content: Content string

    Returns:
        Dictionary with tag counts
    """
    counts = {}
    for tag in ALL_TAGS:
        pattern = re.compile(rf"<\s*{tag}\b[^>]*>", re.IGNORECASE)
        counts[tag] = len(pattern.findall(content))
    return counts


def get_word_count(content: str) -> int:
    """
    Get word count from content, stripping HTML

    Args:
        content: Content string (may contain HTML)

    Returns:
        Word count
    """
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", content)
    # Normalize whitespace
    text = " ".join(text.split())
    # Count words
    return len(text.split())


def split_into_paragraphs(text: str) -> List[str]:
    """
    Split plain text into logical paragraphs

    Args:
        text: Plain text content

    Returns:
        List of paragraph strings
    """
    # Split on double newlines or multiple spaces that indicate paragraph breaks
    paragraphs = re.split(r"\n\s*\n|\.\s{2,}", text)

    # If no clear paragraph breaks, split on sentences (roughly every 3-4 sentences)
    if len(paragraphs) <= 1 and len(text) > 500:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        paragraphs = []
        current = []
        for i, sentence in enumerate(sentences):
            current.append(sentence)
            # Group 3-4 sentences per paragraph
            if len(current) >= 3 or i == len(sentences) - 1:
                paragraphs.append(" ".join(current))
                current = []

    # Clean up paragraphs
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    return paragraphs


def detect_and_convert_lists(text: str) -> str:
    """
    Detect bullet/numbered lists in plain text and convert to HTML

    Args:
        text: Text that may contain list patterns

    Returns:
        Text with lists converted to HTML
    """
    result = text

    # Find bullet list items
    bullet_matches = LIST_BULLET_PATTERN.findall(text)
    if bullet_matches and len(bullet_matches) >= 2:
        # Replace bullet pattern with HTML list
        lines = text.split("\n")
        in_list = False
        new_lines = []

        for line in lines:
            bullet_match = LIST_BULLET_PATTERN.match(line)
            if bullet_match:
                if not in_list:
                    new_lines.append("<ul>")
                    in_list = True
                new_lines.append(f"<li>{bullet_match.group(1).strip()}</li>")
            else:
                if in_list:
                    new_lines.append("</ul>")
                    in_list = False
                new_lines.append(line)

        if in_list:
            new_lines.append("</ul>")

        result = "\n".join(new_lines)

    # Find numbered list items
    number_matches = LIST_NUMBER_PATTERN.findall(text)
    if number_matches and len(number_matches) >= 2:
        lines = result.split("\n")
        in_list = False
        new_lines = []

        for line in lines:
            number_match = LIST_NUMBER_PATTERN.match(line)
            if number_match:
                if not in_list:
                    new_lines.append("<ol>")
                    in_list = True
                new_lines.append(f"<li>{number_match.group(2).strip()}</li>")
            else:
                if in_list:
                    new_lines.append("</ol>")
                    in_list = False
                new_lines.append(line)

        if in_list:
            new_lines.append("</ol>")

        result = "\n".join(new_lines)

    return result


def convert_markdown_emphasis(text: str) -> str:
    """
    Convert markdown-style emphasis to HTML

    Args:
        text: Text with potential **bold** or *italic* patterns

    Returns:
        Text with HTML emphasis tags
    """
    # Convert **bold** to <strong>
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)

    # Convert *italic* to <em> (but not if already part of a tag)
    text = re.sub(r"(?<!<)\*(?!\*)(.+?)\*(?!\*)", r"<em>\1</em>", text)

    return text


def create_title_from_content(content: str, max_length: int = 60) -> Optional[str]:
    """
    Extract a potential title/heading from content's first sentence

    Args:
        content: Content text
        max_length: Maximum title length

    Returns:
        Potential title string or None
    """
    # Get first sentence
    first_sentence = re.split(r"[.!?]", content)[0].strip()

    if len(first_sentence) <= max_length and len(first_sentence) >= 20:
        return first_sentence

    return None


def wrap_plain_text_in_html(content: str) -> str:
    """
    Wrap plain text content in appropriate HTML tags

    Args:
        content: Plain text content without HTML

    Returns:
        Content wrapped in HTML structure
    """
    if not content or not content.strip():
        return content

    # First, detect and convert any markdown-style formatting
    content = convert_markdown_emphasis(content)

    # Detect and convert lists
    content = detect_and_convert_lists(content)

    # If lists were added, check if we now have HTML
    if has_html_tags(content):
        # Still need to wrap non-list parts in paragraphs
        pass

    # Split into paragraphs
    paragraphs = split_into_paragraphs(content)

    if not paragraphs:
        return f"<p>{content}</p>"

    result_parts = []

    for i, para in enumerate(paragraphs):
        para = para.strip()
        if not para:
            continue

        # Skip if already wrapped in HTML block tag
        if re.match(r"^<(p|h[1-6]|ul|ol|div|blockquote)", para, re.IGNORECASE):
            result_parts.append(para)
            continue

        # First paragraph could be a heading if short enough
        if i == 0 and len(para) < 80 and not para.endswith("."):
            result_parts.append(f"<h2>{para}</h2>")
        else:
            result_parts.append(f"<p>{para}</p>")

    return "".join(result_parts)


def fix_unclosed_tags(content: str) -> str:
    """
    Fix unclosed HTML tags

    Args:
        content: HTML content with potential unclosed tags

    Returns:
        Content with properly closed tags
    """
    # Track open tags
    open_tags = []

    # Find all tags
    tag_pattern = re.compile(r"<(/?)(\w+)(?:\s[^>]*)?\s*(/?)>")

    for match in tag_pattern.finditer(content):
        is_closing = match.group(1) == "/"
        tag_name = match.group(2).lower()
        is_self_closing = match.group(3) == "/"

        # Skip self-closing tags
        if is_self_closing or tag_name in ["br", "hr", "img", "input", "meta", "link"]:
            continue

        if is_closing:
            # Try to match with open tag
            if open_tags and open_tags[-1] == tag_name:
                open_tags.pop()
        else:
            open_tags.append(tag_name)

    # Close any remaining open tags (in reverse order)
    for tag in reversed(open_tags):
        content += f"</{tag}>"
        logger.info(f"Auto-closed unclosed <{tag}> tag")

    return content


def fix_attribute_quotes(content: str) -> str:
    """
    Convert double quotes in HTML attributes to single quotes

    Args:
        content: HTML content

    Returns:
        Content with single-quoted attributes
    """
    # Pattern to find attributes with double quotes
    # This is a simplified approach - matches class="value" style patterns
    pattern = re.compile(r'(\w+)="([^"]*)"')

    def replace_quotes(match):
        attr_name = match.group(1)
        attr_value = match.group(2)
        return f"{attr_name}='{attr_value}'"

    return pattern.sub(replace_quotes, content)


def ensure_single_line(content: str) -> str:
    """
    Ensure content is on a single line (required for CSV)

    Args:
        content: HTML content potentially with newlines

    Returns:
        Single-line content
    """
    # Replace newlines with spaces
    content = content.replace("\n", " ").replace("\r", " ")

    # Normalize multiple spaces to single space
    content = re.sub(r"\s+", " ", content)

    return content.strip()


def enhance_content(
    content: str, topic: str = None, target_word_count: int = MIN_WORD_COUNT
) -> Tuple[str, dict]:
    """
    Main enhancement function for content field

    Philosophy: Preserve substance, enhance structure.
    Never rejects content - only improves it.

    Args:
        content: Raw content from LLM
        topic: Content topic (for logging)
        target_word_count: Target word count (for logging, not rejection)

    Returns:
        Tuple of (enhanced_content, metrics_dict)
    """
    metrics = {
        "original_word_count": 0,
        "final_word_count": 0,
        "had_html": False,
        "html_added": False,
        "tags_fixed": False,
        "below_word_target": False,
    }

    if not content or not content.strip():
        logger.warning(f"Empty content received for topic: {topic}")
        return content, metrics

    # Get original word count
    metrics["original_word_count"] = get_word_count(content)

    # Check if content already has HTML
    metrics["had_html"] = has_html_tags(content)

    # Step 1: If no HTML, wrap in appropriate tags
    if not metrics["had_html"]:
        logger.info(f"Content lacks HTML structure, enhancing...")
        content = wrap_plain_text_in_html(content)
        metrics["html_added"] = True

    # Step 2: Fix any unclosed tags
    original_content = content
    content = fix_unclosed_tags(content)
    if content != original_content:
        metrics["tags_fixed"] = True

    # Step 3: Fix attribute quotes (double -> single for CSV compatibility)
    content = fix_attribute_quotes(content)

    # Step 4: Ensure single line for CSV
    content = ensure_single_line(content)

    # Get final word count
    metrics["final_word_count"] = get_word_count(content)

    # Log if below target (but don't reject)
    if metrics["final_word_count"] < target_word_count:
        metrics["below_word_target"] = True
        logger.warning(
            f"Content below target word count: {metrics['final_word_count']}/{target_word_count} "
            f"for topic: {topic}"
        )

    # Log summary
    if metrics["html_added"]:
        logger.info(f"Enhanced content: added HTML structure")
    if metrics["tags_fixed"]:
        logger.info(f"Enhanced content: fixed unclosed tags")

    return content, metrics


def get_content_quality_score(content: str) -> dict:
    """
    Generate a quality score for content (for analytics, not rejection)

    Args:
        content: HTML content

    Returns:
        Quality metrics dictionary
    """
    word_count = get_word_count(content)
    tag_counts = count_html_tags(content)

    # Calculate scores (0-100)
    word_score = min(100, (word_count / MIN_WORD_COUNT) * 100)

    # Structure score based on tag variety
    structure_tags = sum(
        1 for tag in ["p", "h2", "h3", "ul", "ol"] if tag_counts.get(tag, 0) > 0
    )
    structure_score = min(100, structure_tags * 25)

    # Emphasis score
    emphasis_tags = tag_counts.get("strong", 0) + tag_counts.get("em", 0)
    emphasis_score = min(100, emphasis_tags * 20)

    return {
        "word_count": word_count,
        "word_score": round(word_score, 1),
        "structure_score": round(structure_score, 1),
        "emphasis_score": round(emphasis_score, 1),
        "overall_score": round((word_score + structure_score + emphasis_score) / 3, 1),
        "tag_counts": tag_counts,
    }
