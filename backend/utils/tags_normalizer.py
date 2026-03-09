"""
Tags normalization module for CSV generation
Fixes quotes, capitalization, multi-word locations, entity filtering, deduplication
"""

import logging

logger = logging.getLogger(__name__)

# Common person name prefixes
PERSON_PREFIXES = ['mr.', 'mrs.', 'ms.', 'dr.', 'prof.', 'rev.', 'hon.']

# Business suffixes to filter
BUSINESS_SUFFIXES = ['llc', 'inc', 'corp', 'ltd', 'company', 'co', 'group', 'pllc']


def normalize_quotes(text: str) -> str:
    '''
    Fix malformed quotes in tags.
    Examples: """tag1""" -> tag1, "tag" -> tag

    Args:
        text: Raw tags string with potential quote issues

    Returns:
        Tags string with normalized quotes
    '''
    if not text:
        return text

    # Remove triple/double quotes
    text = text.replace('"""', '').replace("'''", '')

    # Remove escaped quotes
    text = text.replace('\\"', '').replace("\\'", '')

    # Strip surrounding quotes
    text = text.strip('"').strip("'")

    return text


def to_title_case(tag: str) -> str:
    """
    Convert tag to Title Case, preserving acronyms

    Args:
        tag: Single tag string

    Returns:
        Title-cased tag
    """
    if not tag:
        return tag

    words = tag.split()
    result = []

    for word in words:
        # Preserve acronyms (all caps, length > 1)
        if word.isupper() and len(word) > 1:
            result.append(word)  # Keep SEO, USA, etc.
        else:
            result.append(word.capitalize())

    return ' '.join(result)


def preserve_multi_word_locations(tags_list: list) -> list:
    """
    Merge split location names
    Example: ['Cherry', 'Fork', 'OH'] → ['Cherry Fork', 'OH']

    Args:
        tags_list: List of individual tags

    Returns:
        List with multi-word locations preserved
    """
    if len(tags_list) < 2:
        return tags_list

    merged = []
    skip_next = False

    for i, tag in enumerate(tags_list):
        if skip_next:
            skip_next = False
            continue

        # Check if current tag and next tag should be merged
        if i < len(tags_list) - 1:
            next_tag = tags_list[i + 1]

            # Merge if both are:
            # 1. Single words (no spaces)
            # 2. Title case
            # 3. Combined length < 20 chars
            # 4. Not all caps (not acronyms)
            if (' ' not in tag and ' ' not in next_tag and
                tag.istitle() and next_tag.istitle() and
                len(tag) + len(next_tag) < 20 and
                not tag.isupper() and not next_tag.isupper()):
                merged.append(f"{tag} {next_tag}")
                skip_next = True
                logger.info(f"Merged location tags: '{tag}' + '{next_tag}' → '{tag} {next_tag}'")
                continue

        merged.append(tag)

    return merged


def filter_entity_names(tags_list: list, client_name: str = None) -> list:
    """
    Remove business and person names from tags

    Args:
        tags_list: List of tags
        client_name: Optional client name to filter out

    Returns:
        Filtered list without entity names
    """
    filtered = []

    for tag in tags_list:
        tag_lower = tag.lower()

        # Skip person names (Mr., Dr., etc.)
        has_person_prefix = any(tag_lower.startswith(prefix) for prefix in PERSON_PREFIXES)
        if has_person_prefix:
            logger.info(f"Filtered person name tag: '{tag}'")
            continue

        # Skip business suffixes
        has_business_suffix = any(tag_lower.endswith(suffix) for suffix in BUSINESS_SUFFIXES)
        if has_business_suffix:
            logger.info(f"Filtered business tag: '{tag}'")
            continue

        # Skip client name
        if client_name and client_name.lower() in tag_lower:
            logger.info(f"Filtered client name tag: '{tag}'")
            continue

        # Skip if tag contains business suffix anywhere
        has_business_word = any(f' {suffix}' in tag_lower for suffix in BUSINESS_SUFFIXES)
        if has_business_word:
            logger.info(f"Filtered business-related tag: '{tag}'")
            continue

        filtered.append(tag)

    return filtered


def deduplicate_tags(tags_list: list) -> list:
    """
    Remove duplicate tags (case-insensitive)

    Args:
        tags_list: List of tags potentially with duplicates

    Returns:
        Deduplicated list
    """
    seen = set()
    unique = []

    for tag in tags_list:
        tag_lower = tag.lower()

        if tag_lower not in seen:
            seen.add(tag_lower)
            unique.append(tag)
        else:
            logger.info(f"Removed duplicate tag: '{tag}'")

    return unique


def ensure_tag_count(tags_list: list, topic: str, min_count: int = 3, max_count: int = 7) -> list:
    """
    Ensure tags list has between min_count and max_count tags

    Args:
        tags_list: Current list of tags
        topic: Content topic (for generating additional tags)
        min_count: Minimum number of tags
        max_count: Maximum number of tags

    Returns:
        List with correct tag count
    """
    # If too many, truncate
    if len(tags_list) > max_count:
        logger.info(f"Truncating tags from {len(tags_list)} to {max_count}")
        tags_list = tags_list[:max_count]

    # If too few, add from topic
    if len(tags_list) < min_count:
        logger.info(f"Adding tags from topic (current: {len(tags_list)}, target: {min_count})")

        # Extract words from topic
        topic_words = [w.strip('.,!?:;') for w in topic.split() if len(w) > 4]

        # Add topic words as tags until we reach min_count
        for word in topic_words:
            if len(tags_list) >= min_count:
                break

            # Convert to Title Case
            tag = to_title_case(word)

            # Add if not already in list (case-insensitive)
            if tag.lower() not in [t.lower() for t in tags_list]:
                tags_list.append(tag)
                logger.info(f"Added tag from topic: '{tag}'")

    # Deduplicate again after adding
    tags_list = deduplicate_tags(tags_list)

    # Final truncation if still over max
    return tags_list[:max_count]


def normalize_tags(
    tags: str,
    topic: str,
    client_name: str = None,
    min_count: int = 3,
    max_count: int = 7
) -> str:
    """
    Main normalization function for tags field

    Args:
        tags: Raw tags string from LLM
        topic: Content topic
        client_name: Optional client name to filter
        min_count: Minimum tag count
        max_count: Maximum tag count

    Returns:
        Normalized tags string (comma-separated)
    """
    if not tags:
        logger.warning("Tags empty, generating from topic")
        # Generate from topic
        topic_words = [w.strip('.,!?:;') for w in topic.split() if len(w) > 4][:max_count]
        tags = ', '.join([to_title_case(w) for w in topic_words])
        return tags

    # Step 1: Fix malformed quotes
    tags = normalize_quotes(tags)

    # Step 2: Convert pipe separators to commas
    if '|' in tags:
        tags = tags.replace('|', ',')
        logger.info("Converted pipe separators to commas in tags")

    # Step 3: Split and clean
    tag_list = [t.strip() for t in tags.split(',') if t.strip()]

    # Step 4: Remove overly long tags (likely full sentences)
    original_count = len(tag_list)
    tag_list = [t for t in tag_list if len(t.split()) <= 4 and len(t) <= 40]
    if len(tag_list) < original_count:
        logger.info(f"Removed {original_count - len(tag_list)} overly long tags")

    # Step 5: Preserve multi-word locations
    tag_list = preserve_multi_word_locations(tag_list)

    # Step 6: Filter entity names (businesses, people)
    tag_list = filter_entity_names(tag_list, client_name)

    # Step 7: Apply Title Case to all tags
    tag_list = [to_title_case(tag) for tag in tag_list]

    # Step 8: Deduplicate
    tag_list = deduplicate_tags(tag_list)

    # Step 9: Ensure correct count (3-7 tags)
    tag_list = ensure_tag_count(tag_list, topic, min_count, max_count)

    # Join with commas
    result = ', '.join(tag_list)
    logger.info(f"Normalized tags: {result}")

    return result
