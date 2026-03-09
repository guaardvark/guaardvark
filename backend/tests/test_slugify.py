"""
Tests for Python slugify utility
Validates that Python implementation matches JavaScript behavior exactly
"""

import pytest
from backend.utils.slugify import (
    slugify,
    is_valid_slug,
    create_slug_with_validation,
    test_slugify,
    remove_diacritics
)


class TestSlugifyUtility:
    """Test suite for slugify utility functions"""

    def test_slugify_simple_titles(self):
        """Test basic slugification"""
        assert slugify('Simple Title') == 'simple-title'
        assert slugify('Another Test') == 'another-test'

    def test_slugify_special_characters(self):
        """Test handling of special characters and diacritics"""
        assert slugify('Title with Spéciàl Chäräctërs') == 'title-with-special-characters'
        assert slugify('Café résumé naïve') == 'cafe-resume-naive'

    def test_slugify_symbols_and_punctuation(self):
        """Test handling of symbols and punctuation"""
        assert slugify('Title!!! with @#$ symbols & stuff') == 'title-with-symbols-stuff'
        assert slugify('Hello, World!') == 'hello-world'

    def test_slugify_multiple_spaces(self):
        """Test handling of multiple spaces"""
        assert slugify('Multiple    Spaces   Between') == 'multiple-spaces-between'
        assert slugify('  Leading and trailing spaces  ') == 'leading-and-trailing-spaces'

    def test_slugify_leading_trailing_dashes(self):
        """Test handling of leading and trailing dashes"""
        assert slugify('---Leading and Trailing Dashes---') == 'leading-and-trailing-dashes'
        assert slugify('-Start with dash') == 'start-with-dash'

    def test_slugify_non_english_characters(self):
        """Test handling of non-English characters"""
        assert slugify('Ñoñó Español & François Français') == 'nono-espanol-francois-francais'
        assert slugify('München Zürich') == 'munchen-zurich'

    def test_slugify_numbers_and_uppercase(self):
        """Test handling of numbers and uppercase"""
        assert slugify('123 Numbers and UPPERCASE') == '123-numbers-and-uppercase'
        assert slugify('MiXeD CaSe 123') == 'mixed-case-123'

    def test_slugify_very_long_titles(self):
        """Test that long titles are not truncated by default"""
        long_title = 'Very-Long-Title-That-Would-Previously-Be-Truncated-But-Now-Should-Remain-Complete'
        expected = 'very-long-title-that-would-previously-be-truncated-but-now-should-remain-complete'
        assert slugify(long_title) == expected

    def test_slugify_empty_and_none_inputs(self):
        """Test handling of empty and None inputs"""
        assert slugify('') == ''
        assert slugify(None) == ''
        assert slugify('   ') == ''

    def test_slugify_with_max_length(self):
        """Test slugification with maximum length limit"""
        result = slugify('This is a very long title that should be truncated', max_length=20)
        assert len(result) <= 20
        assert not result.endswith('-')

    def test_slugify_complex_unicode(self):
        """Test complex Unicode normalization"""
        assert slugify('café résumé naïve') == 'cafe-resume-naive'
        # Non-Latin characters should be removed
        result = slugify('北京 москва')
        assert result == '' or len(result) == 0

    def test_is_valid_slug_correct_slugs(self):
        """Test validation of correct slugs"""
        assert is_valid_slug('valid-slug') is True
        assert is_valid_slug('another-valid-slug-123') is True
        assert is_valid_slug('simple') is True
        assert is_valid_slug('123-test') is True

    def test_is_valid_slug_incorrect_slugs(self):
        """Test validation of incorrect slugs"""
        assert is_valid_slug('invalid_slug') is False  # underscore
        assert is_valid_slug('invalid slug') is False  # space
        assert is_valid_slug('Invalid-Slug') is False  # uppercase
        assert is_valid_slug('-leading-dash') is False  # leading dash
        assert is_valid_slug('trailing-dash-') is False  # trailing dash
        assert is_valid_slug('') is False  # empty
        assert is_valid_slug(None) is False  # None

    def test_create_slug_with_validation_valid_input(self):
        """Test slug creation with validation for valid input"""
        result = create_slug_with_validation('Good Title')

        assert result['slug'] == 'good-title'
        assert result['is_valid'] is True
        assert len(result['errors']) == 0
        assert result['original'] == 'Good Title'

    def test_create_slug_with_validation_invalid_input(self):
        """Test slug creation with validation for invalid input"""
        result = create_slug_with_validation('')

        assert result['slug'] == ''
        assert result['is_valid'] is False
        assert 'Title produces empty slug' in result['errors']

    def test_create_slug_with_validation_complex_invalid(self):
        """Test slug creation with complex invalid input"""
        result = create_slug_with_validation('!!!@@@###')

        assert result['is_valid'] is False
        assert len(result['errors']) > 0

    def test_remove_diacritics(self):
        """Test diacritics removal function"""
        assert remove_diacritics('café') == 'cafe'
        assert remove_diacritics('résumé') == 'resume'
        assert remove_diacritics('naïve') == 'naive'
        assert remove_diacritics('Zürich') == 'Zurich'

    def test_test_slugify_function(self):
        """Test the built-in test function"""
        test_results = test_slugify()

        assert test_results['all_passed'] is True
        assert '8/8 tests passed' in test_results['summary']
        assert len(test_results['results']) == 8

    def test_edge_cases(self):
        """Test various edge cases"""
        # Extremely long input
        very_long_title = 'A' * 1000 + ' Title'
        result = slugify(very_long_title)
        assert result == 'a' * 1000 + '-title'
        assert len(result) > 999

        # Only special characters
        assert slugify('!@#$%^&*()') == ''

        # Mixed valid and invalid characters
        assert slugify('Good!@#Bad$%^Words') == 'good-bad-words'

        # Consecutive separators
        assert slugify('Word---With---Many---Dashes') == 'word-with-many-dashes'

    def test_javascript_compatibility(self):
        """Test cases that ensure compatibility with JavaScript implementation"""
        test_cases = [
            ('Simple Title', 'simple-title'),
            ('Title with Spéciàl Chäräctërs', 'title-with-special-characters'),
            ('Title!!! with @#$ symbols & stuff', 'title-with-symbols-stuff'),
            ('Multiple    Spaces   Between', 'multiple-spaces-between'),
            ('---Leading and Trailing Dashes---', 'leading-and-trailing-dashes'),
            ('Ñoñó Español & François Français', 'nono-espanol-francois-francais'),
            ('123 Numbers and UPPERCASE', '123-numbers-and-uppercase'),
            ('Very-Long-Title-That-Would-Previously-Be-Truncated-But-Now-Should-Remain-Complete',
             'very-long-title-that-would-previously-be-truncated-but-now-should-remain-complete')
        ]

        for input_title, expected in test_cases:
            result = slugify(input_title)
            assert result == expected, f"Failed for input '{input_title}': expected '{expected}', got '{result}'"

    def test_performance_with_large_input(self):
        """Test performance with large input"""
        # This is more of a smoke test to ensure the function doesn't crash
        large_input = 'Test ' * 10000
        result = slugify(large_input)
        assert result.startswith('test-')
        assert result.endswith('-test')

    def test_type_safety(self):
        """Test type safety and error handling"""
        # Should handle non-string inputs gracefully
        assert slugify(123) == ''
        assert slugify(['list']) == ''
        assert slugify({'dict': 'value'}) == ''

    def test_consistency_across_multiple_calls(self):
        """Test that multiple calls with same input produce same output"""
        title = 'Consistency Test Title'
        first_result = slugify(title)
        second_result = slugify(title)
        third_result = slugify(title)

        assert first_result == second_result == third_result
        assert first_result == 'consistency-test-title'


if __name__ == '__main__':
    # Run the built-in test when executed directly
    test_results = test_slugify()
    print(f"Built-in tests: {test_results['summary']}")

    if not test_results['all_passed']:
        print("\nFailed tests:")
        for test in test_results['results']:
            if not test['passed']:
                print(f"Input: '{test['input']}'")
                print(f"Expected: '{test['expected']}'")
                print(f"Got: '{test['result']}'")
                print()

    # Run pytest
    pytest.main([__file__])