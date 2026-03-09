/**
 * CSV utility with configurable delimiters
 * Supports comma, semicolon, and tab delimiters with proper escaping
 */

// Delimiter mappings
export const DELIMITERS = {
  COMMA: ',',
  SEMICOLON: ';',
  TAB: '\t'
};

export const DELIMITER_LABELS = {
  [DELIMITERS.COMMA]: 'Comma (,)',
  [DELIMITERS.SEMICOLON]: 'Semicolon (;)',
  [DELIMITERS.TAB]: 'Tab'
};

/**
 * Escape CSV field value based on delimiter
 * @param {string} value - Field value to escape
 * @param {string} delimiter - CSV delimiter
 * @returns {string} - Properly escaped field value
 */
const escapeCSVField = (value, delimiter) => {
  if (value === null || value === undefined) {
    return '';
  }

  const strValue = String(value);

  // Check if escaping is needed
  const needsEscaping =
    strValue.includes(delimiter) ||
    strValue.includes('"') ||
    strValue.includes('\n') ||
    strValue.includes('\r');

  if (!needsEscaping) {
    return strValue;
  }

  // Escape quotes by doubling them and wrap in quotes
  const escapedValue = strValue.replace(/"/g, '""');
  return `"${escapedValue}"`;
};

/**
 * Convert array of objects to CSV string
 * @param {Array<Object>} data - Array of objects to convert
 * @param {string} delimiter - CSV delimiter (default: comma)
 * @param {Array<string>} headers - Optional header names (uses object keys if not provided)
 * @returns {string} - CSV string
 */
export const arrayToCSV = (data, delimiter = DELIMITERS.COMMA, headers = null) => {
  if (!Array.isArray(data) || data.length === 0) {
    return '';
  }

  // Get headers from first object if not provided
  const csvHeaders = headers || Object.keys(data[0]);

  // Create header row
  const headerRow = csvHeaders
    .map(header => escapeCSVField(header, delimiter))
    .join(delimiter);

  // Create data rows
  const dataRows = data.map(row =>
    csvHeaders
      .map(header => escapeCSVField(row[header], delimiter))
      .join(delimiter)
  );

  return [headerRow, ...dataRows].join('\n');
};

/**
 * Parse CSV string to array of objects
 * @param {string} csvString - CSV string to parse
 * @param {string} delimiter - CSV delimiter (default: comma)
 * @param {boolean} hasHeaders - Whether first row contains headers (default: true)
 * @returns {Array<Object>} - Array of objects
 */
export const csvToArray = (csvString, delimiter = DELIMITERS.COMMA, hasHeaders = true) => {
  if (!csvString || typeof csvString !== 'string') {
    return [];
  }

  const lines = csvString.trim().split('\n');
  if (lines.length === 0) {
    return [];
  }

  // Parse CSV with proper quote handling
  const parseCSVLine = (line) => {
    const fields = [];
    let currentField = '';
    let inQuotes = false;
    let i = 0;

    while (i < line.length) {
      const char = line[i];
      const nextChar = line[i + 1];

      if (char === '"') {
        if (inQuotes && nextChar === '"') {
          // Escaped quote
          currentField += '"';
          i += 2;
        } else {
          // Start or end of quoted field
          inQuotes = !inQuotes;
          i++;
        }
      } else if (char === delimiter && !inQuotes) {
        // Field delimiter
        fields.push(currentField);
        currentField = '';
        i++;
      } else {
        currentField += char;
        i++;
      }
    }

    // Add the last field
    fields.push(currentField);
    return fields;
  };

  let headers;
  let dataStartIndex;

  if (hasHeaders) {
    headers = parseCSVLine(lines[0]);
    dataStartIndex = 1;
  } else {
    // Generate headers as column1, column2, etc.
    const firstRowFields = parseCSVLine(lines[0]);
    headers = firstRowFields.map((_, index) => `column${index + 1}`);
    dataStartIndex = 0;
  }

  // Parse data rows
  const data = [];
  for (let i = dataStartIndex; i < lines.length; i++) {
    const fields = parseCSVLine(lines[i]);
    const row = {};

    headers.forEach((header, index) => {
      row[header] = fields[index] || '';
    });

    data.push(row);
  }

  return data;
};

/**
 * Download CSV data as file
 * @param {Array<Object>} data - Data to export
 * @param {string} filename - Download filename
 * @param {string} delimiter - CSV delimiter
 * @param {Array<string>} headers - Optional custom headers
 */
export const downloadCSV = (data, filename, delimiter = DELIMITERS.COMMA, headers = null) => {
  const csvContent = arrayToCSV(data, delimiter, headers);
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const link = document.createElement('a');

  if (link.download !== undefined) {
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', filename);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }
};

/**
 * Validate CSV structure
 * @param {Array<Object>} data - Data to validate
 * @returns {Object} - Validation result
 */
export const validateCSVData = (data) => {
  const result = {
    isValid: true,
    errors: [],
    warnings: [],
    summary: {
      totalRows: 0,
      totalColumns: 0,
      emptyRows: 0,
      inconsistentColumns: 0
    }
  };

  if (!Array.isArray(data)) {
    result.isValid = false;
    result.errors.push('Data must be an array');
    return result;
  }

  if (data.length === 0) {
    result.warnings.push('Data array is empty');
    return result;
  }

  result.summary.totalRows = data.length;

  // Get expected columns from first row
  const expectedColumns = Object.keys(data[0]);
  result.summary.totalColumns = expectedColumns.length;

  // Validate each row
  data.forEach((row, index) => {
    if (!row || typeof row !== 'object') {
      result.isValid = false;
      result.errors.push(`Row ${index + 1} is not an object`);
      return;
    }

    const rowColumns = Object.keys(row);

    // Check for consistent columns
    if (rowColumns.length !== expectedColumns.length) {
      result.summary.inconsistentColumns++;
      result.warnings.push(`Row ${index + 1} has different number of columns`);
    }

    // Check for missing expected columns
    const missingColumns = expectedColumns.filter(col => !rowColumns.includes(col));
    if (missingColumns.length > 0) {
      result.warnings.push(`Row ${index + 1} missing columns: ${missingColumns.join(', ')}`);
    }

    // Check for empty row
    const hasData = rowColumns.some(col => row[col] && String(row[col]).trim());
    if (!hasData) {
      result.summary.emptyRows++;
      result.warnings.push(`Row ${index + 1} appears to be empty`);
    }
  });

  return result;
};

/**
 * Get delimiter from string by analyzing content
 * @param {string} csvString - CSV content to analyze
 * @returns {string} - Detected delimiter
 */
export const detectDelimiter = (csvString) => {
  if (!csvString || typeof csvString !== 'string') {
    return DELIMITERS.COMMA;
  }

  const firstLine = csvString.split('\n')[0];
  const delimiters = [DELIMITERS.COMMA, DELIMITERS.SEMICOLON, DELIMITERS.TAB];

  // Count occurrences of each delimiter
  const counts = delimiters.map(delimiter => ({
    delimiter,
    count: (firstLine.match(new RegExp(delimiter === '\t' ? '\\t' : `\\${delimiter}`, 'g')) || []).length
  }));

  // Return delimiter with highest count
  const winner = counts.reduce((prev, current) =>
    current.count > prev.count ? current : prev
  );

  return winner.count > 0 ? winner.delimiter : DELIMITERS.COMMA;
};

/**
 * Test CSV utilities
 * @returns {Object} - Test results
 */
export const testCSVUtils = () => {
  const testData = [
    { id: 1, name: 'John Doe', email: 'john@example.com', notes: 'Has "quotes" and, commas' },
    { id: 2, name: 'Jane Smith', email: 'jane@example.com', notes: 'Normal text' },
    { id: 3, name: 'Bob; Johnson', email: 'bob@example.com', notes: 'Has semicolon; and newline\nhere' }
  ];

  const tests = [];

  // Test comma delimiter
  const commaCSV = arrayToCSV(testData, DELIMITERS.COMMA);
  const commaParsed = csvToArray(commaCSV, DELIMITERS.COMMA);
  tests.push({
    name: 'Comma delimiter round-trip',
    passed: JSON.stringify(testData) === JSON.stringify(commaParsed)
  });

  // Test semicolon delimiter
  const semicolonCSV = arrayToCSV(testData, DELIMITERS.SEMICOLON);
  const semicolonParsed = csvToArray(semicolonCSV, DELIMITERS.SEMICOLON);
  tests.push({
    name: 'Semicolon delimiter round-trip',
    passed: JSON.stringify(testData) === JSON.stringify(semicolonParsed)
  });

  // Test tab delimiter
  const tabCSV = arrayToCSV(testData, DELIMITERS.TAB);
  const tabParsed = csvToArray(tabCSV, DELIMITERS.TAB);
  tests.push({
    name: 'Tab delimiter round-trip',
    passed: JSON.stringify(testData) === JSON.stringify(tabParsed)
  });

  // Test validation
  const validation = validateCSVData(testData);
  tests.push({
    name: 'Data validation',
    passed: validation.isValid && validation.summary.totalRows === 3
  });

  // Test delimiter detection
  const detectedComma = detectDelimiter(commaCSV);
  const detectedSemicolon = detectDelimiter(semicolonCSV);
  const detectedTab = detectDelimiter(tabCSV);

  tests.push({
    name: 'Delimiter detection',
    passed:
      detectedComma === DELIMITERS.COMMA &&
      detectedSemicolon === DELIMITERS.SEMICOLON &&
      detectedTab === DELIMITERS.TAB
  });

  const allPassed = tests.every(test => test.passed);
  const passedCount = tests.filter(test => test.passed).length;

  return {
    allPassed,
    tests,
    summary: `${passedCount}/${tests.length} tests passed`
  };
};