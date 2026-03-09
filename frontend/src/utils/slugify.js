/**
 * Slugify utility with new rules - no length limits, proper Unicode handling
 * Ensures consistency between frontend and backend slug generation
 */

/**
 * Normalize Unicode characters and remove diacritics
 * @param {string} str - Input string
 * @returns {string} - Normalized string
 */
const removeDiacritics = (str) => {
  return str
    .normalize('NFD') // Decompose Unicode characters
    .replace(/[\u0300-\u036f]/g, '') // Remove diacritics/accents
    .normalize('NFC'); // Recompose
};

/**
 * Generate URL-friendly slug from title
 * Rules: lowercase, a-z0-9 only, dash-separated, no spaces, strip diacritics,
 * collapse repeating dashes, trim edges, NO length cutoffs
 *
 * @param {string} title - Input title
 * @param {object} options - Configuration options
 * @param {number} options.maxLength - Maximum length (default: unlimited)
 * @returns {string} - URL-friendly slug
 */
export const slugify = (title, options = {}) => {
  if (!title || typeof title !== 'string') {
    return '';
  }

  const { maxLength = null } = options;

  let slug = title;

  // Step 1: Convert to lowercase
  slug = slug.toLowerCase();

  // Step 2: Remove diacritics and normalize Unicode
  slug = removeDiacritics(slug);

  // Step 3: Replace non a-z0-9 characters with dashes
  slug = slug.replace(/[^a-z0-9\s]/g, '-');

  // Step 4: Replace whitespace with dashes
  slug = slug.replace(/\s+/g, '-');

  // Step 5: Collapse multiple consecutive dashes
  slug = slug.replace(/-+/g, '-');

  // Step 6: Trim leading and trailing dashes
  slug = slug.replace(/^-+|-+$/g, '');

  // Step 7: Apply length limit if specified (but not by default)
  if (maxLength && typeof maxLength === 'number' && maxLength > 0) {
    slug = slug.substring(0, maxLength);
    // Re-trim trailing dashes after truncation
    slug = slug.replace(/-+$/, '');
  }

  return slug;
};

/**
 * Validate if a string is a valid slug
 * @param {string} slug - Slug to validate
 * @returns {boolean} - True if valid slug
 */
export const isValidSlug = (slug) => {
  if (!slug || typeof slug !== 'string') {
    return false;
  }

  // Check if slug matches the pattern: lowercase a-z0-9 with dashes
  const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  return slugPattern.test(slug);
};

/**
 * Ensure slug uniqueness by appending number if needed
 * @param {string} baseSlug - Base slug
 * @param {function} checkExists - Function that returns true if slug exists
 * @returns {Promise<string>} - Unique slug
 */
export const ensureUniqueSlug = async (baseSlug, checkExists) => {
  let slug = baseSlug;
  let counter = 1;

  while (await checkExists(slug)) {
    slug = `${baseSlug}-${counter}`;
    counter++;
  }

  return slug;
};

/**
 * Create slug from title with validation
 * @param {string} title - Title to slugify
 * @param {object} options - Options
 * @returns {object} - {slug, isValid, errors}
 */
export const createSlugWithValidation = (title, options = {}) => {
  const slug = slugify(title, options);
  const isValid = isValidSlug(slug);
  const errors = [];

  if (!slug) {
    errors.push('Title produces empty slug');
  } else if (!isValid) {
    errors.push('Generated slug contains invalid characters');
  }

  if (title && title.length > 100 && !slug) {
    errors.push('Title too complex to generate valid slug');
  }

  return {
    slug,
    isValid,
    errors,
    original: title
  };
};

// Test cases for validation
export const testSlugify = () => {
  const testCases = [
    {
      input: 'Simple Title',
      expected: 'simple-title'
    },
    {
      input: 'Title with Spéciàl Chäräctërs',
      expected: 'title-with-special-characters'
    },
    {
      input: 'Title!!! with @#$ symbols & stuff',
      expected: 'title-with-symbols-stuff'
    },
    {
      input: 'Multiple    Spaces   Between',
      expected: 'multiple-spaces-between'
    },
    {
      input: '---Leading and Trailing Dashes---',
      expected: 'leading-and-trailing-dashes'
    },
    {
      input: 'Ñoñó Español & François Français',
      expected: 'nono-espanol-francois-francais'
    },
    {
      input: '123 Numbers and UPPERCASE',
      expected: '123-numbers-and-uppercase'
    },
    {
      input: 'Very-Long-Title-That-Would-Previously-Be-Truncated-But-Now-Should-Remain-Complete',
      expected: 'very-long-title-that-would-previously-be-truncated-but-now-should-remain-complete'
    }
  ];

  const results = testCases.map(({ input, expected }) => {
    const result = slugify(input);
    const passed = result === expected;
    return {
      input,
      expected,
      result,
      passed
    };
  });

  const allPassed = results.every(test => test.passed);

  return {
    allPassed,
    results,
    summary: `${results.filter(t => t.passed).length}/${results.length} tests passed`
  };
};

export default slugify;