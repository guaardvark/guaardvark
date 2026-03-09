"""
Slugify utility with new rules - no length limits, proper Unicode handling
Ensures consistency between frontend and backend slug generation
Mirrors the JavaScript implementation exactly
"""

import re
import unicodedata
from typing import Optional, Dict, List, Any


def remove_diacritics(text: str) -> str:
    """
    Remove diacritics/accents from Unicode characters

    Args:
        text: Input string

    Returns:
        String with diacritics removed
    """
    # Normalize to NFD (decomposed form)
    normalized = unicodedata.normalize('NFD', text)
    # Remove combining characters (diacritics)
    without_diacritics = ''.join(
        char for char in normalized
        if unicodedata.category(char) != 'Mn'
    )
    # Normalize back to NFC (composed form)
    return unicodedata.normalize('NFC', without_diacritics)


def slugify(title: str, max_length: Optional[int] = None) -> str:
    """
    Generate URL-friendly slug from title
    Rules: lowercase, a-z0-9 only, dash-separated, no spaces, strip diacritics,
    collapse repeating dashes, trim edges, NO length cutoffs by default

    Args:
        title: Input title
        max_length: Maximum length (default: unlimited)

    Returns:
        URL-friendly slug
    """
    if not title or not isinstance(title, str):
        return ''

    slug = title

    # Step 1: Convert to lowercase
    slug = slug.lower()

    # Step 2: Remove diacritics and normalize Unicode
    slug = remove_diacritics(slug)

    # Step 3: Replace non a-z0-9 characters with dashes
    slug = re.sub(r'[^a-z0-9\s]', '-', slug)

    # Step 4: Replace whitespace with dashes
    slug = re.sub(r'\s+', '-', slug)

    # Step 5: Collapse multiple consecutive dashes
    slug = re.sub(r'-+', '-', slug)

    # Step 6: Trim leading and trailing dashes
    slug = slug.strip('-')

    # Step 7: Apply length limit if specified (but not by default)
    if max_length and isinstance(max_length, int) and max_length > 0:
        slug = slug[:max_length]
        # Re-trim trailing dashes after truncation
        slug = slug.rstrip('-')

    return slug


def is_valid_slug(slug: str) -> bool:
    """
    Validate if a string is a valid slug

    Args:
        slug: Slug to validate

    Returns:
        True if valid slug
    """
    if not slug or not isinstance(slug, str):
        return False

    # Check if slug matches the pattern: lowercase a-z0-9 with dashes
    slug_pattern = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    return bool(slug_pattern.match(slug))


async def ensure_unique_slug(base_slug: str, check_exists_func) -> str:
    """
    Ensure slug uniqueness by appending number if needed

    Args:
        base_slug: Base slug
        check_exists_func: Async function that returns True if slug exists

    Returns:
        Unique slug
    """
    slug = base_slug
    counter = 1

    while await check_exists_func(slug):
        slug = f"{base_slug}-{counter}"
        counter += 1

    return slug


def create_slug_with_validation(title: str, max_length: Optional[int] = None) -> Dict[str, Any]:
    """
    Create slug from title with validation

    Args:
        title: Title to slugify
        max_length: Maximum length

    Returns:
        Dictionary with slug, validity, and errors
    """
    slug = slugify(title, max_length)
    is_valid = is_valid_slug(slug)
    errors = []

    if not slug:
        errors.append('Title produces empty slug')
    elif not is_valid:
        errors.append('Generated slug contains invalid characters')

    if title and len(title) > 100 and not slug:
        errors.append('Title too complex to generate valid slug')

    return {
        'slug': slug,
        'is_valid': is_valid,
        'errors': errors,
        'original': title
    }


def test_slugify() -> Dict[str, Any]:
    """
    Test cases for validation - mirrors JavaScript tests

    Returns:
        Test results dictionary
    """
    test_cases = [
        {
            'input': 'Simple Title',
            'expected': 'simple-title'
        },
        {
            'input': 'Title with Spéciàl Chäräctërs',
            'expected': 'title-with-special-characters'
        },
        {
            'input': 'Title!!! with @#$ symbols & stuff',
            'expected': 'title-with-symbols-stuff'
        },
        {
            'input': 'Multiple    Spaces   Between',
            'expected': 'multiple-spaces-between'
        },
        {
            'input': '---Leading and Trailing Dashes---',
            'expected': 'leading-and-trailing-dashes'
        },
        {
            'input': 'Ñoñó Español & François Français',
            'expected': 'nono-espanol-francois-francais'
        },
        {
            'input': '123 Numbers and UPPERCASE',
            'expected': '123-numbers-and-uppercase'
        },
        {
            'input': 'Very-Long-Title-That-Would-Previously-Be-Truncated-But-Now-Should-Remain-Complete',
            'expected': 'very-long-title-that-would-previously-be-truncated-but-now-should-remain-complete'
        }
    ]

    results = []
    for test_case in test_cases:
        input_title = test_case['input']
        expected = test_case['expected']
        result = slugify(input_title)
        passed = result == expected

        results.append({
            'input': input_title,
            'expected': expected,
            'result': result,
            'passed': passed
        })

    all_passed = all(test['passed'] for test in results)
    passed_count = sum(1 for test in results if test['passed'])

    return {
        'all_passed': all_passed,
        'results': results,
        'summary': f"{passed_count}/{len(results)} tests passed"
    }


if __name__ == '__main__':
    # Run tests when executed directly
    test_results = test_slugify()
    print(f"Slugify Tests: {test_results['summary']}")

    if not test_results['all_passed']:
        print("\nFailed tests:")
        for test in test_results['results']:
            if not test['passed']:
                print(f"Input: '{test['input']}'")
                print(f"Expected: '{test['expected']}'")
                print(f"Got: '{test['result']}'")
                print()