"""
HTML structure validation module for CSV content generation
Validates and auto-fixes HTML structure issues
"""

import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Non-semantic class names to remove
FORBIDDEN_CLASSES = [
    "wrapper",
    "wrapper1",
    "wrapper2",
    "wrapper3",
    "div1",
    "div2",
    "div3",
    "section1",
    "section2",
    "content-block",
    "content-wrapper",
    "container1",
]


def validate_html_structure(content: str) -> tuple:
    """
    Validate HTML structure against template rules

    Rules checked:
    - No H1 tags (reserved for WordPress post title)
    - Proper heading hierarchy (no skips: H2 → H3 → H4)
    - Lists only contain <li> elements as direct children
    - Tags are balanced and properly closed

    Args:
        content: HTML content string

    Returns:
        Tuple of (is_valid, violations_list)
    """
    violations = []

    if not content:
        violations.append("ERROR: Content is empty")
        return False, violations

    try:
        soup = BeautifulSoup(content, "html.parser")

        # Check 1: No H1 tags (WordPress post title uses H1)
        h1_tags = soup.find_all("h1")
        if h1_tags:
            violations.append(
                f"ERROR: Found {len(h1_tags)} H1 tag(s) - use H2 for main sections"
            )

        # Check 2: Heading hierarchy (no skips)
        headings = soup.find_all(["h2", "h3", "h4", "h5", "h6"])
        prev_level = 1

        for heading in headings:
            level = int(heading.name[1])

            # Check for level skips (e.g., H2 → H4 without H3)
            if level > prev_level + 1:
                violations.append(
                    f"ERROR: Heading hierarchy skip: {heading.name.upper()} after H{prev_level} "
                    f"(text: '{heading.get_text()[:30]}...')"
                )

            prev_level = level

        # Check 3: List structure (only <li> elements as direct children)
        for list_tag in soup.find_all(["ul", "ol"]):
            direct_children = [child for child in list_tag.children if child.name]

            non_li_children = [child for child in direct_children if child.name != "li"]
            if non_li_children:
                violations.append(
                    f"ERROR: List contains non-<li> elements: {[c.name for c in non_li_children]}"
                )

        # Check 4: Unbalanced tags
        if content.count("<") != content.count(">"):
            violations.append("ERROR: Unbalanced HTML tags (< and > count mismatch)")

        # Check 5: Unclosed tags (basic check)
        # BeautifulSoup auto-closes tags, so check for malformed content
        if "</p<" in content or "</h" in content or "</li<" in content:
            violations.append("ERROR: Malformed HTML tags detected")

    except Exception as e:
        violations.append(f"ERROR: HTML parsing failed: {str(e)}")

    # Determine if valid
    error_count = len([v for v in violations if v.startswith("ERROR")])
    is_valid = error_count == 0

    return is_valid, violations


def auto_fix_html_structure(content: str) -> str:
    """
    Auto-fix common HTML structure issues

    Fixes applied:
    - Convert H1 → H2
    - Remove non-semantic classes
    - Fix list structure
    - Clean up malformed tags

    Args:
        content: Original HTML content

    Returns:
        Fixed HTML content
    """
    if not content:
        return content

    try:
        soup = BeautifulSoup(content, "html.parser")

        # Fix 1: Convert H1 to H2
        for h1 in soup.find_all("h1"):
            h1.name = "h2"
            logger.info(f"Converted H1 to H2: '{h1.get_text()[:50]}...'")

        # Fix 2: Remove non-semantic classes
        for tag in soup.find_all(True):  # All tags
            if tag.get("class"):
                # Filter out forbidden classes
                original_classes = tag["class"]
                tag["class"] = [
                    c for c in tag["class"] if c.lower() not in FORBIDDEN_CLASSES
                ]

                # Remove class attribute if empty
                if not tag["class"]:
                    del tag["class"]
                    logger.info(
                        f"Removed non-semantic classes from <{tag.name}>: {original_classes}"
                    )

        # Fix 3: Clean up list structure
        for list_tag in soup.find_all(["ul", "ol"]):
            # Remove non-<li> direct children
            for child in list(list_tag.children):
                if child.name and child.name != "li":
                    # Wrap non-li content in <li>
                    li = soup.new_tag("li")
                    child.wrap(li)
                    logger.info(f"Wrapped non-<li> element in list: <{child.name}>")

        # Return cleaned HTML
        return str(soup)

    except Exception as e:
        logger.error(f"HTML auto-fix failed: {e}")
        return content  # Return original if fixing fails


def validate_and_fix_html(content: str, strict: bool = True) -> tuple:
    """
    Main validation and fixing function for HTML content

    Args:
        content: Raw HTML content
        strict: If True, require validation to pass after auto-fix

    Returns:
        Tuple of (fixed_content, is_valid, violations)
    """
    # Step 1: Initial validation
    is_valid, violations = validate_html_structure(content)

    if is_valid:
        logger.info("HTML structure valid, no fixes needed")
        return content, True, []

    # Step 2: Log violations
    logger.warning(f"HTML structure violations found: {violations}")

    # Step 3: Apply auto-fixes
    fixed_content = auto_fix_html_structure(content)

    # Step 4: Re-validate after auto-fix
    is_valid_after, remaining_violations = validate_html_structure(fixed_content)

    if is_valid_after:
        logger.info("HTML structure auto-fix successful")
        return fixed_content, True, []
    else:
        logger.warning(
            f"HTML structure issues remain after auto-fix: {remaining_violations}"
        )

        if strict:
            # In strict mode, return None to trigger regeneration
            return None, False, remaining_violations
        else:
            # In flexible mode, return fixed content with warnings
            return fixed_content, False, remaining_violations


def check_heading_hierarchy(content: str) -> bool:
    """
    Quick check for proper heading hierarchy

    Args:
        content: HTML content

    Returns:
        True if heading hierarchy is valid
    """
    try:
        soup = BeautifulSoup(content, "html.parser")
        headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])

        # H1 should not exist
        if soup.find("h1"):
            return False

        # Check for level skips
        prev_level = 1
        for heading in headings:
            level = int(heading.name[1])
            if level > prev_level + 1:
                return False
            prev_level = level

        return True

    except Exception:
        return False
