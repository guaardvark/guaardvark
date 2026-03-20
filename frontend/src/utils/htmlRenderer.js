/**
 * HTML structure renderer for content generation
 * Creates structured HTML content from basic outlines
 */

import { HTML_STRUCTURE_DEFAULTS } from '../config/defaults.js';

/**
 * Render content outline into structured HTML
 * @param {string} content - Plain text content outline
 * @param {Object} siteMeta - Site metadata for context
 * @param {Object} options - Rendering options
 * @returns {string} - Structured HTML content
 */
export const renderStructuredHTML = (content, siteMeta = {}, options = {}) => {
  const merged = { ...HTML_STRUCTURE_DEFAULTS, ...options };
  const { includeH1, allowedTags } = merged;

  if (!content || typeof content !== 'string') {
    return '';
  }

  let html = content.trim();

  // Skip if content already contains HTML tags
  if (/<[^>]+>/.test(html)) {
    return html; // Return as-is if already contains HTML
  }

  // Convert outline structure to HTML
  html = convertOutlineToHTML(html, { includeH1, siteMeta, allowedTags });

  // Add contact links if contact URL is available
  if (siteMeta.contactUrl) {
    html = addContactLinks(html, siteMeta.contactUrl);
  }

  return html;
};

/**
 * Convert plain text outline to HTML structure
 * @param {string} text - Plain text content
 * @param {Object} options - Conversion options
 * @returns {string} - HTML content
 */
const convertOutlineToHTML = (text, options = {}) => {
  const { includeH1 } = options;
  const lines = text.split('\n').map(line => line.trim()).filter(line => line.length > 0);

  if (lines.length === 0) {
    return '';
  }

  let html = '';
  let inList = false;

  lines.forEach((line, index) => {
    // Check for different content patterns
    if (isTitle(line, index) && includeH1) {
      // Main title (H1) - only if includeH1 is true
      if (inList) {
        html += '</ul>\n';
        inList = false;
      }
      html += `<h1>${escapeHTML(line)}</h1>\n`;
    } else if (isHeading(line)) {
      // Section heading (H2)
      if (inList) {
        html += '</ul>\n';
        inList = false;
      }
      html += `<h2>${escapeHTML(cleanHeading(line))}</h2>\n`;
    } else if (isSubheading(line)) {
      // Subsection heading (H3)
      if (inList) {
        html += '</ul>\n';
        inList = false;
      }
      html += `<h3>${escapeHTML(cleanHeading(line))}</h3>\n`;
    } else if (isBulletPoint(line)) {
      // List item
      if (!inList) {
        html += '<ul>\n';
        inList = true;
      }
      const cleanedLine = cleanBulletPoint(line);
      html += `<li>${escapeHTML(cleanedLine)}</li>\n`;
    } else if (line.length > 0) {
      // Regular paragraph
      if (inList) {
        html += '</ul>\n';
        inList = false;
      }

      // Check for emphasis patterns
      const processedLine = processEmphasis(line);
      html += `<p>${processedLine}</p>\n`;
    }
  });

  // Close any open list
  if (inList) {
    html += '</ul>\n';
  }

  return html.trim();
};

/**
 * Check if line is a title (first line or line with title indicators)
 */
const isTitle = (line, index) => {
  return index === 0 && (
    line.length > 10 &&
    !line.startsWith('-') &&
    !line.startsWith('•') &&
    !line.includes(':') &&
    !line.toLowerCase().startsWith('about') &&
    !line.toLowerCase().startsWith('our')
  );
};

/**
 * Check if line is a main heading
 */
const isHeading = (line) => {
  return (
    line.endsWith(':') ||
    /^(about|our|services?|why|benefits?|how|what|features?|solutions?)/i.test(line.trim()) ||
    line.length < 50 && !isBulletPoint(line) && line.includes(' ')
  );
};

/**
 * Check if line is a subheading
 */
const isSubheading = (line) => {
  return (
    line.length < 40 &&
    line.length > 5 &&
    !isBulletPoint(line) &&
    !line.endsWith('.') &&
    /^[A-Z]/.test(line.trim())
  );
};

/**
 * Check if line is a bullet point
 */
const isBulletPoint = (line) => {
  return (
    line.startsWith('-') ||
    line.startsWith('•') ||
    line.startsWith('*') ||
    /^\d+\./.test(line) ||
    line.startsWith('✓') ||
    line.startsWith('→')
  );
};

/**
 * Clean heading text by removing colons and excess formatting
 */
const cleanHeading = (line) => {
  return line.replace(/:$/, '').trim();
};

/**
 * Clean bullet point text
 */
const cleanBulletPoint = (line) => {
  return line
    .replace(/^[-•*✓→]\s*/, '')
    .replace(/^\d+\.\s*/, '')
    .trim();
};

/**
 * Process emphasis patterns (bold, italic)
 */
const processEmphasis = (line) => {
  let processed = escapeHTML(line);

  // Convert **text** to <strong>text</strong>
  processed = processed.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // Convert *text* to <em>text</em>
  processed = processed.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  return processed;
};

/**
 * Add contact links throughout the content
 */
const addContactLinks = (html, contactUrl) => {
  // Add contact link to the end if not already present
  if (!html.includes(contactUrl) && !html.toLowerCase().includes('contact')) {
    html += `\n<p><a href="${contactUrl}">Contact us</a> for more information.</p>`;
  }

  return html;
};

/**
 * Escape HTML special characters
 */
const escapeHTML = (text) => {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
};

/**
 * Validate HTML structure against allowed tags
 */
export const validateHTMLStructure = (html, allowedTags = HTML_STRUCTURE_DEFAULTS.allowedTags) => {
  const tagRegex = /<\/?([a-zA-Z0-9]+)[^>]*>/g;
  const foundTags = [];
  let match;

  while ((match = tagRegex.exec(html)) !== null) {
    const tag = match[1].toLowerCase();
    if (!foundTags.includes(tag)) {
      foundTags.push(tag);
    }
  }

  const invalidTags = foundTags.filter(tag => !allowedTags.includes(tag));

  return {
    isValid: invalidTags.length === 0,
    invalidTags,
    foundTags,
    allowedTags
  };
};

/**
 * Preview structured HTML rendering
 */
export const previewStructuredHTML = (content, siteMeta = {}, options = {}) => {
  const html = renderStructuredHTML(content, siteMeta, options);
  const validation = validateHTMLStructure(html);

  return {
    html,
    validation,
    wordCount: content.split(/\s+/).length,
    characterCount: content.length,
    htmlLength: html.length
  };
};

/**
 * Test HTML renderer with sample content
 */
export const testHTMLRenderer = () => {
  const sampleContent = `Professional Legal Services

About Our Practice:
We provide comprehensive legal solutions for businesses and individuals.

Our Services:
- Corporate law consultation
- Contract negotiation and drafting
- Litigation support
- **Specialized** expertise in commercial disputes

Why Choose Us:
Expert attorneys with over 20 years of experience
Personalized approach to every case
*Proven track record* of successful outcomes

Contact us today for a consultation.`;

  const sampleSiteMeta = {
    companyName: 'Legal Experts LLC',
    contactUrl: 'https://example.com/contact',
    primaryService: 'Legal Services'
  };

  const results = [
    {
      name: 'Basic HTML with H1',
      options: { includeH1: true },
      result: previewStructuredHTML(sampleContent, sampleSiteMeta, { includeH1: true })
    },
    {
      name: 'HTML without H1',
      options: { includeH1: false },
      result: previewStructuredHTML(sampleContent, sampleSiteMeta, { includeH1: false })
    },
    {
      name: 'Plain text (no HTML)',
      options: { includeH1: false },
      result: previewStructuredHTML('Simple plain text content.', sampleSiteMeta)
    }
  ];

  return {
    allValid: results.every(test => test.result.validation.isValid),
    results,
    summary: `${results.filter(t => t.result.validation.isValid).length}/${results.length} tests passed`
  };
};