/**
 * Shared validation schemas using Zod for client-side validation
 * These schemas ensure data consistency between UI and backend
 */

import { z } from 'zod';

// Brand tone enum
export const BrandTone = {
  NEUTRAL: 'neutral',
  FRIENDLY: 'friendly',
  AUTHORITATIVE: 'authoritative',
  PLAYFUL: 'playful',
  LUXURY: 'luxury',
};

// Social platform validation
export const SocialLinkSchema = z.object({
  platform: z.string().min(1, 'Platform is required'),
  url: z.string().url('Must be a valid URL'),
});

// Phone number validation (E.164 or formatted)
const phoneRegex = /^[+]?[1-9][\d]{0,15}$|^[(]?[\d\s\-.()]{10,}$/;

// Site metadata schema
export const SiteMetaSchema = z.object({
  companyName: z.string().min(1, 'Company name is required').max(255, 'Company name too long'),
  phone: z.string().regex(phoneRegex, 'Invalid phone number format').optional().or(z.literal('')),
  contactUrl: z.string().url('Must be a valid contact URL').optional().or(z.literal('')),
  clientLocation: z.string().min(1, 'Client location is required').max(255, 'Location too long'),
  primaryService: z.string().min(1, 'Primary service is required').max(255, 'Service name too long'),
  secondaryService: z.string().max(255, 'Service name too long').optional().or(z.literal('')),
  brandTone: z.enum([
    BrandTone.NEUTRAL,
    BrandTone.FRIENDLY,
    BrandTone.AUTHORITATIVE,
    BrandTone.PLAYFUL,
    BrandTone.LUXURY,
  ]),
  hours: z.string().max(500, 'Hours description too long').optional().or(z.literal('')),
  socialLinks: z.array(SocialLinkSchema).optional().default([]),
});

// Row/Page schema for generated content
export const PageRowSchema = z.object({
  title: z.string().min(10, 'Title must be at least 10 characters').max(200, 'Title too long'),
  slug: z.string().min(1, 'Slug is required').max(500, 'Slug too long').regex(
    /^[a-z0-9]+(?:-[a-z0-9]+)*$/,
    'Slug must be lowercase, a-z and 0-9 only, separated by dashes'
  ),
  category: z.string().max(255, 'Category too long').optional().or(z.literal('')),
  tags: z.array(z.string()).optional().default([]),
  excerpt: z.string().max(500, 'Excerpt too long').optional().or(z.literal('')),
  content: z.string().min(100, 'Content must be at least 100 characters').max(50000, 'Content too long'),
}).merge(SiteMetaSchema);

// CSV export configuration schema
export const CsvConfigSchema = z.object({
  delimiter: z.enum([',', ';', '\t'], {
    errorMap: () => ({ message: 'Delimiter must be comma, semicolon, or tab' })
  }),
  structuredHtml: z.boolean().default(false),
  includeH1: z.boolean().default(true), // Whether to include H1 in content
});

// Generation batch schema
export const GenerationBatchSchema = z.object({
  outputFilename: z.string().min(1, 'Output filename is required')
    .regex(/\.csv$/i, 'Filename must end with .csv'),
  siteMeta: SiteMetaSchema,
  csvConfig: CsvConfigSchema,
  rows: z.array(PageRowSchema).min(1, 'At least one row is required'),
});

// Validation helpers
export const validateSiteMeta = (data) => {
  try {
    return { success: true, data: SiteMetaSchema.parse(data), errors: null };
  } catch (error) {
    return { success: false, data: null, errors: error.errors };
  }
};

export const validatePageRow = (data) => {
  try {
    return { success: true, data: PageRowSchema.parse(data), errors: null };
  } catch (error) {
    return { success: false, data: null, errors: error.errors };
  }
};

export const validateCsvConfig = (data) => {
  try {
    return { success: true, data: CsvConfigSchema.parse(data), errors: null };
  } catch (error) {
    return { success: false, data: null, errors: error.errors };
  }
};

export const validateGenerationBatch = (data) => {
  try {
    return { success: true, data: GenerationBatchSchema.parse(data), errors: null };
  } catch (error) {
    return { success: false, data: null, errors: error.errors };
  }
};

// Format validation errors for display
export const formatValidationErrors = (errors) => {
  if (!errors) return [];

  return errors.map(error => ({
    field: error.path.join('.'),
    message: error.message,
    code: error.code
  }));
};

// Preflight validation report
export const createValidationReport = (rows, siteMeta, csvConfig) => {
  const report = {
    totalRows: rows.length,
    validRows: 0,
    invalidRows: 0,
    warnings: [],
    errors: [],
    fieldErrors: {},
  };

  // Validate site meta
  const siteMetaResult = validateSiteMeta(siteMeta);
  if (!siteMetaResult.success) {
    report.errors.push({
      type: 'site_meta',
      message: 'Site metadata validation failed',
      details: formatValidationErrors(siteMetaResult.errors)
    });
  }

  // Validate CSV config
  const csvConfigResult = validateCsvConfig(csvConfig);
  if (!csvConfigResult.success) {
    report.errors.push({
      type: 'csv_config',
      message: 'CSV configuration validation failed',
      details: formatValidationErrors(csvConfigResult.errors)
    });
  }

  // Validate each row
  rows.forEach((row, index) => {
    const rowResult = validatePageRow({ ...row, ...siteMeta });
    if (rowResult.success) {
      report.validRows++;

      // Check for warnings
      if (row.content && row.content.length < 300) {
        report.warnings.push({
          type: 'content_length',
          row: index + 1,
          message: 'Content may be too short (less than 300 characters)'
        });
      }

      if (!row.excerpt || row.excerpt.length < 100) {
        report.warnings.push({
          type: 'excerpt_missing',
          row: index + 1,
          message: 'Excerpt is missing or too short'
        });
      }
    } else {
      report.invalidRows++;
      const formattedErrors = formatValidationErrors(rowResult.errors);

      formattedErrors.forEach(error => {
        if (!report.fieldErrors[error.field]) {
          report.fieldErrors[error.field] = [];
        }
        report.fieldErrors[error.field].push({
          row: index + 1,
          message: error.message
        });
      });
    }
  });

  return report;
};