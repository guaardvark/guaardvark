"""
Category validation module for CSV generation
Detects and fixes common category issues: JSON formatting, city names, business names, concatenated words
"""

import re
import logging

logger = logging.getLogger(__name__)

# Top 500 US cities blacklist (selected major cities)
CITY_BLACKLIST = {
    # Major cities
    "tampa",
    "orlando",
    "miami",
    "jacksonville",
    "tallahassee",
    "stockton",
    "sacramento",
    "san francisco",
    "los angeles",
    "san diego",
    "san jose",
    "new york",
    "brooklyn",
    "manhattan",
    "queens",
    "bronx",
    "chicago",
    "houston",
    "phoenix",
    "philadelphia",
    "san antonio",
    "dallas",
    "austin",
    "fort worth",
    "columbus",
    "charlotte",
    "indianapolis",
    "seattle",
    "denver",
    "boston",
    "detroit",
    "nashville",
    "memphis",
    "portland",
    "las vegas",
    "baltimore",
    "milwaukee",
    "albuquerque",
    "tucson",
    "fresno",
    "mesa",
    "atlanta",
    "raleigh",
    "miami gardens",
    "long beach",
    "virginia beach",
    "oakland",
    "minneapolis",
    "tulsa",
    "bakersfield",
    "wichita",
    "arlington",
    "aurora",
    "tampa bay",
    "new orleans",
    "cleveland",
    # Ohio cities (relevant to the example CSV)
    "bentonville",
    "manchester",
    "blue creek",
    "cherry fork",
    "lynx",
    "peebles",
    "seaman",
    "west union",
    "winchester",
    "lima",
    "beaverdam",
    "gomer",
    "bluffton",
    "cairo",
    "delphos",
    # Additional major cities
    "cincinnati",
    "toledo",
    "akron",
    "dayton",
    "parma",
    "canton",
    "youngstown",
    "lorain",
    "hamilton",
    "springfield",
    "kettering",
    "elyria",
    "newark",
    "cuyahoga falls",
    "middletown",
    "euclid",
}

# Business suffix patterns
BUSINESS_SUFFIXES = [
    "llc",
    "inc",
    "corp",
    "ltd",
    "limited",
    "corporation",
    "company",
    "co",
    "group",
    "enterprises",
    "associates",
    "law firm",
    "pllc",
    "pa",
    "pc",
    "professional association",
]


def is_city_name(text: str) -> bool:
    """
    Check if text is a city name

    Args:
        text: Category text to check

    Returns:
        True if text appears to be a city name
    """
    if not text:
        return False

    normalized = text.lower().strip()

    # Check against blacklist
    if normalized in CITY_BLACKLIST:
        return True

    # Check for state abbreviations appended (e.g., "Tampa FL")
    words = normalized.split()
    if len(words) == 2 and len(words[1]) == 2 and words[1].isupper():
        return words[0] in CITY_BLACKLIST

    return False


def is_business_name(text: str, client_name: str = None) -> bool:
    """
    Detect if text contains a business name

    Args:
        text: Category text to check
        client_name: Optional client name to check against

    Returns:
        True if text appears to be a business name
    """
    if not text:
        return False

    lower_text = text.lower().strip()

    # Check for business suffixes
    for suffix in BUSINESS_SUFFIXES:
        if lower_text.endswith(suffix):
            return True
        if f" {suffix}" in lower_text:
            return True

    # Check if client name is in the text
    if client_name:
        client_lower = client_name.lower()
        if client_lower in lower_text:
            return True

        # Check for partial client name match (first word)
        client_words = client_lower.split()
        if client_words and client_words[0] in lower_text:
            return True

    # Check for common business prefixes
    business_prefixes = ["the ", "dr. ", "dr ", "doctor "]
    for prefix in business_prefixes:
        if lower_text.startswith(prefix):
            return True

    return False


def fix_concatenated_category(text: str) -> str:
    """
    Fix concatenated words in category by adding spaces between capital letters
    Example: "DigitalMarketingServicesDenver" → "Digital Marketing Services Denver"

    Args:
        text: Concatenated category text

    Returns:
        Fixed category with proper spacing
    """
    if not text or " " in text:
        return text

    # Add space before capital letters (except first character)
    result = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)

    # Remove city names from the result
    words = result.split()
    filtered_words = [w for w in words if not is_city_name(w)]

    fixed = " ".join(filtered_words).strip()

    logger.info(f"Fixed concatenated category: '{text}' → '{fixed}'")
    return fixed


def generate_category_from_topic(
    topic: str, primary_service: str = None, industry: str = None
) -> str:
    """
    Generate a proper category from topic and available metadata

    Args:
        topic: The content topic
        primary_service: Optional primary service from client metadata
        industry: Optional industry from client metadata

    Returns:
        Generated category string
    """
    # Priority 1: Use primary service if available
    if primary_service and len(primary_service.strip()) >= 3:
        return primary_service.strip().title()

    # Priority 2: Use industry if available
    if industry and len(industry.strip()) >= 3:
        return industry.strip().title()

    # Priority 3: Extract from topic
    # Look for service keywords
    service_keywords = {
        "seo": "SEO Services",
        "search engine": "SEO Services",
        "web": "Web Development",
        "website": "Web Development",
        "legal": "Legal Services",
        "law": "Legal Services",
        "attorney": "Legal Services",
        "lawyer": "Legal Services",
        "marketing": "Marketing Services",
        "consulting": "Consulting Services",
        "health": "Health Services",
        "fitness": "Health & Fitness",
        "nutrition": "Health & Fitness",
        "diet": "Health & Fitness",
        "training": "Training Services",
        "education": "Education Services",
        "technology": "Technology Services",
        "software": "Software Development",
        "finance": "Financial Services",
        "accounting": "Financial Services",
    }

    topic_lower = topic.lower()
    for keyword, category in service_keywords.items():
        if keyword in topic_lower:
            return category

    # Priority 4: Extract first 2-3 significant words from topic
    words = [w for w in topic.split() if len(w) > 3][:3]
    if words:
        return " ".join(words).title()

    # Fallback
    return "Professional Services"


def clean_category(category: str) -> str:
    """
    Clean category by removing extra quotes, brackets, and formatting

    Args:
        category: Raw category string

    Returns:
        Cleaned category string
    """
    if not category:
        return category

    # Remove JSON array formatting: [""Health""] or ["Health"]
    if category.startswith("[") and category.endswith("]"):
        category = category.strip("[]").strip('"').strip("'").strip()
        logger.info(f"Removed JSON array formatting from category")

    # Fix escaped/multiple quotes
    if '""' in category or '\\"' in category:
        category = category.replace('""', "").replace('\\"', "")
        logger.info(f"Removed escaped quotes from category")

    return category.strip()


def validate_and_fix_category(
    category: str,
    topic: str,
    primary_service: str = None,
    industry: str = None,
    client_name: str = None,
) -> str:
    """
    Main validation and fixing function for category field

    Args:
        category: Raw category from LLM
        topic: Content topic
        primary_service: Optional primary service
        industry: Optional industry
        client_name: Optional client name

    Returns:
        Validated and fixed category string
    """
    # Step 1: Clean formatting
    category = clean_category(category)

    # Step 2: Check if empty
    if not category or len(category.strip()) < 3:
        logger.warning(f"Category empty or too short, generating from topic")
        return generate_category_from_topic(topic, primary_service, industry)

    # Step 3: Check for city names
    if is_city_name(category):
        logger.warning(f"Category '{category}' is a city name, regenerating")
        return generate_category_from_topic(topic, primary_service, industry)

    # Step 4: Check for business names
    if is_business_name(category, client_name):
        logger.warning(f"Category '{category}' is a business name, regenerating")
        return generate_category_from_topic(topic, primary_service, industry)

    # Step 5: Fix concatenated words
    if " " not in category and len(category) > 20:
        capital_count = sum(1 for c in category if c.isupper())
        if capital_count >= 3:
            logger.warning(f"Category '{category}' appears concatenated, fixing")
            fixed = fix_concatenated_category(category)

            # Revalidate after fixing
            if is_city_name(fixed) or is_business_name(fixed, client_name):
                logger.warning(f"Fixed category still invalid, regenerating")
                return generate_category_from_topic(topic, primary_service, industry)

            category = fixed

    # Step 6: Ensure Title Case
    category = category.title()

    # Step 7: Remove excessive punctuation
    if "," in category or len(category) > 50:
        category = category.split(",")[0].strip()[:50]
        logger.info(f"Simplified category to: {category}")

    return category
