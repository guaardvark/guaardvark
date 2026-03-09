/**
 * XML Rendering and Formatting Utilities
 * Provides XML formatting, validation, and preview capabilities
 * for WordPress XML imports via Llamanator plugin
 */

/**
 * Format XML string with proper indentation and syntax highlighting
 * @param {string} xmlString - Raw XML string
 * @returns {string} Formatted XML with HTML markup for syntax highlighting
 */
export const formatXMLWithHighlighting = (xmlString) => {
  try {
    // Parse XML to validate
    const parser = new DOMParser();
    const xmlDoc = parser.parseFromString(xmlString, 'text/xml');

    // Check for parsing errors
    const parserError = xmlDoc.getElementsByTagName('parsererror');
    if (parserError.length > 0) {
      throw new Error('Invalid XML structure');
    }

    // Pretty print XML
    const serializer = new XMLSerializer();
    const formatted = formatXMLString(serializer.serializeToString(xmlDoc));

    // Add syntax highlighting
    return highlightXML(formatted);
  } catch (error) {
    console.error('XML formatting error:', error);
    return `<span style="color: red;">Error formatting XML: ${error.message}</span>`;
  }
};

/**
 * Format XML string with indentation
 * @param {string} xml - Raw XML string
 * @returns {string} Formatted XML
 */
export const formatXMLString = (xml) => {
  let formatted = '';
  let indent = '';
  const tab = '  '; // 2 spaces

  xml.split(/>\s*</).forEach((node) => {
    if (node.match(/^\/\w/)) {
      // Closing tag
      indent = indent.substring(tab.length);
    }

    formatted += indent + '<' + node + '>\n';

    if (node.match(/^<?\w[^>]*[^\/]$/) && !node.startsWith('?')) {
      // Opening tag
      indent += tab;
    }
  });

  return formatted.substring(1, formatted.length - 2);
};

/**
 * Add syntax highlighting to XML
 * @param {string} xml - Formatted XML string
 * @returns {string} HTML with syntax highlighting
 */
export const highlightXML = (xml) => {
  return xml
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/(&lt;\/?)([\w:-]+)/g, '<span style="color: #0066cc;">$1$2</span>')
    .replace(/([\w:-]+)=/g, '<span style="color: #aa5d00;">$1</span>=')
    .replace(/="([^"]*)"/g, '="<span style="color: #008000;">$1</span>"')
    .replace(/&lt;!--/g, '<span style="color: #808080;">&lt;!--')
    .replace(/--&gt;/g, '--&gt;</span>')
    .replace(/&lt;\!\[CDATA\[/g, '<span style="color: #4a4a4a;">&lt;![CDATA[')
    .replace(/\]\]&gt;/g, ']]&gt;</span>');
};

/**
 * Validate XML structure for Llamanator compatibility
 * @param {string} xmlString - XML string to validate
 * @returns {object} Validation result with errors and warnings
 */
export const validateLlamanatorXML = (xmlString) => {
  const errors = [];
  const warnings = [];

  try {
    const parser = new DOMParser();
    const xmlDoc = parser.parseFromString(xmlString, 'text/xml');

    // Check for parsing errors
    const parserError = xmlDoc.getElementsByTagName('parsererror');
    if (parserError.length > 0) {
      errors.push('Invalid XML structure: ' + parserError[0].textContent);
      return { valid: false, errors, warnings };
    }

    // Check root element
    const root = xmlDoc.documentElement;
    if (root.tagName !== 'llamanator_export') {
      errors.push('Root element must be <llamanator_export>');
    }

    // Check required attributes
    if (!root.getAttribute('version')) {
      warnings.push('Missing version attribute on root element');
    }

    // Check post structure
    const posts = root.getElementsByTagName('post');
    if (posts.length === 0) {
      warnings.push('No posts found in XML');
    }

    // Validate each post
    for (let i = 0; i < posts.length; i++) {
      const post = posts[i];
      const postNum = i + 1;

      // Check required fields
      const requiredFields = ['ID', 'Title', 'Content'];
      requiredFields.forEach((field) => {
        const elements = post.getElementsByTagName(field);
        if (elements.length === 0) {
          errors.push(`Post ${postNum}: Missing required field "${field}"`);
        }
      });

      // Check recommended fields
      const recommendedFields = ['Excerpt', 'slug', 'Category'];
      recommendedFields.forEach((field) => {
        const elements = post.getElementsByTagName(field);
        if (elements.length === 0) {
          warnings.push(`Post ${postNum}: Missing recommended field "${field}"`);
        }
      });

      // Validate slug format
      const slugElements = post.getElementsByTagName('slug');
      if (slugElements.length > 0) {
        const slug = slugElements[0].textContent;
        if (slug && !/^[a-z0-9-]+$/.test(slug)) {
          warnings.push(`Post ${postNum}: Slug "${slug}" should only contain lowercase letters, numbers, and hyphens`);
        }
      }
    }

    return {
      valid: errors.length === 0,
      errors,
      warnings,
      postCount: posts.length
    };
  } catch (error) {
    errors.push(`Validation error: ${error.message}`);
    return { valid: false, errors, warnings };
  }
};

/**
 * Generate preview HTML for XML content
 * @param {string} xmlString - XML string
 * @returns {string} HTML preview
 */
export const previewXMLContent = (xmlString) => {
  try {
    const parser = new DOMParser();
    const xmlDoc = parser.parseFromString(xmlString, 'text/xml');
    const posts = xmlDoc.getElementsByTagName('post');

    if (posts.length === 0) {
      return '<p style="color: #666;">No posts found in XML</p>';
    }

    let html = '<div style="font-family: sans-serif;">';
    html += `<h3 style="color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 8px;">XML Preview (${posts.length} posts)</h3>`;

    // Preview first 3 posts
    const previewCount = Math.min(3, posts.length);
    for (let i = 0; i < previewCount; i++) {
      const post = posts[i];

      const id = post.getElementsByTagName('ID')[0]?.textContent || 'N/A';
      const title = post.getElementsByTagName('Title')[0]?.textContent || 'Untitled';
      const content = post.getElementsByTagName('Content')[0]?.textContent || '';
      const excerpt = post.getElementsByTagName('Excerpt')[0]?.textContent || '';
      const category = post.getElementsByTagName('Category')[0]?.textContent || 'Uncategorized';
      const tags = post.getElementsByTagName('Tags')[0]?.textContent || '';
      const slug = post.getElementsByTagName('slug')[0]?.textContent || '';

      html += `
        <div style="border: 1px solid #ddd; border-radius: 4px; padding: 12px; margin: 12px 0; background: #f9f9f9;">
          <h4 style="margin: 0 0 8px 0; color: #0066cc;">${title}</h4>
          <div style="font-size: 0.85em; color: #666; margin-bottom: 8px;">
            <strong>ID:</strong> ${id} |
            <strong>Slug:</strong> ${slug || 'N/A'} |
            <strong>Category:</strong> ${category}
            ${tags ? ` | <strong>Tags:</strong> ${tags}` : ''}
          </div>
          ${excerpt ? `<p style="font-style: italic; color: #555; margin: 8px 0;">${excerpt.substring(0, 150)}${excerpt.length > 150 ? '...' : ''}</p>` : ''}
          <div style="font-size: 0.9em; color: #333; line-height: 1.5;">
            ${content.substring(0, 200)}${content.length > 200 ? '...' : ''}
          </div>
        </div>
      `;
    }

    if (posts.length > previewCount) {
      html += `<p style="color: #666; font-style: italic;">... and ${posts.length - previewCount} more posts</p>`;
    }

    html += '</div>';
    return html;
  } catch (error) {
    return `<p style="color: red;">Error generating preview: ${error.message}</p>`;
  }
};

/**
 * Extract statistics from XML
 * @param {string} xmlString - XML string
 * @returns {object} Statistics object
 */
export const getXMLStatistics = (xmlString) => {
  try {
    const parser = new DOMParser();
    const xmlDoc = parser.parseFromString(xmlString, 'text/xml');
    const posts = xmlDoc.getElementsByTagName('post');

    let totalContent = 0;
    let totalExcerpts = 0;
    const categories = new Set();
    const tags = new Set();

    for (let i = 0; i < posts.length; i++) {
      const post = posts[i];

      const content = post.getElementsByTagName('Content')[0]?.textContent || '';
      const excerpt = post.getElementsByTagName('Excerpt')[0]?.textContent || '';
      const category = post.getElementsByTagName('Category')[0]?.textContent || '';
      const postTags = post.getElementsByTagName('Tags')[0]?.textContent || '';

      totalContent += content.length;
      if (excerpt) totalExcerpts++;

      if (category) {
        category.split('|').forEach(cat => categories.add(cat.trim()));
      }
      if (postTags) {
        postTags.split('|').forEach(tag => tags.add(tag.trim()));
      }
    }

    return {
      postCount: posts.length,
      avgContentLength: posts.length > 0 ? Math.round(totalContent / posts.length) : 0,
      postsWithExcerpts: totalExcerpts,
      uniqueCategories: categories.size,
      uniqueTags: tags.size,
      fileSize: new Blob([xmlString]).size
    };
  } catch (error) {
    console.error('Error getting XML statistics:', error);
    return {
      postCount: 0,
      avgContentLength: 0,
      postsWithExcerpts: 0,
      uniqueCategories: 0,
      uniqueTags: 0,
      fileSize: 0
    };
  }
};

/**
 * Download XML string as file
 * @param {string} xmlString - XML content
 * @param {string} filename - Download filename
 */
export const downloadXMLFile = (xmlString, filename = 'export.xml') => {
  const blob = new Blob([xmlString], { type: 'application/xml' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};
