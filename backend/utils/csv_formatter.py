# backend/utils/csv_formatter.py
# CSV Formatting Utility for Proper CSV Generation
# Converts LLM-generated content into properly formatted CSV files

import csv
import io
import logging
import re
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class CSVColumn:
    """Represents a CSV column with its metadata"""
    name: str
    description: str
    required: bool = True
    max_length: Optional[int] = None

class CSVTemplate:
    """Predefined CSV templates with inheritance support"""
    
    # Base template that others can inherit from
    BASE_CONTENT = [
        CSVColumn("title", "Content title", True, 200),
        CSVColumn("content", "Main content", True),
        CSVColumn("summary", "Content summary", False, 300),
        CSVColumn("date_created", "Creation date", False)
    ]
    
    # Extended templates inherit from base
    WORDPRESS_POSTS = BASE_CONTENT + [
        CSVColumn("post_excerpt", "Short excerpt/summary", False, 500),
        CSVColumn("post_status", "Post status (publish/draft)", False),
        CSVColumn("post_category", "Post category", False),
        CSVColumn("post_tags", "Tags (comma-separated)", False),
        CSVColumn("meta_description", "SEO meta description", False, 160)
    ]
    
    BUSINESS_PAGES = BASE_CONTENT + [
        CSVColumn("page_slug", "URL slug", False),
        CSVColumn("meta_description", "SEO meta description", False, 160),
        CSVColumn("meta_keywords", "SEO keywords", False),
        CSVColumn("page_category", "Page category", False),
        CSVColumn("author", "Content author", False)
    ]
    
    PRODUCT_CATALOG = BASE_CONTENT + [
        CSVColumn("product_price", "Product price", False),
        CSVColumn("product_category", "Product category", False),
        CSVColumn("product_sku", "Product SKU", False),
        CSVColumn("product_features", "Key features", False),
        CSVColumn("product_benefits", "Benefits", False),
        CSVColumn("product_image_url", "Image URL", False)
    ]
    
    # WordPress Enfold Theme optimized template
    ENFOLD_WORDPRESS = [
        CSVColumn("ID", "5-digit unique identifier", True, 5),
        CSVColumn("Title", "Content title", True, 200),
        CSVColumn("Content", "Main content with basic formatting (H1, H2, H3, bold, italic, links) for Avia Builder", True),
        CSVColumn("Excerpt", "Short excerpt/summary", True, 300),
        CSVColumn("Category", "Content category", True, 100),
        CSVColumn("Tags", "At least 12 tags (comma-separated)", True, 500),
        CSVColumn("slug", "URL slug (title with dashes)", True, 200),
        CSVColumn("Image", "Same as category (will be replaced with category image URL)", True, 200)
    ]
    
    # General content uses base template directly
    GENERAL_CONTENT = BASE_CONTENT + [
        CSVColumn("category", "Content category", False),
        CSVColumn("tags", "Tags (comma-separated)", False),
        CSVColumn("author", "Content author", False),
        CSVColumn("status", "Content status", False)
    ]

class CSVFormatter:
    """Professional CSV content formatter and generator"""
    
    def __init__(self):
        self.templates = {
            'enfold': CSVTemplate.ENFOLD_WORDPRESS,
            'wordpress': CSVTemplate.WORDPRESS_POSTS,
            'business': CSVTemplate.BUSINESS_PAGES,
            'product': CSVTemplate.PRODUCT_CATALOG,
            'general': CSVTemplate.GENERAL_CONTENT
        }
    
    def detect_csv_template(self, content: str, user_prompt: str = "") -> List[CSVColumn]:
        """
        Automatically detect the most appropriate CSV template based on content and prompt
        """
        try:
            content_lower = content.lower() if content else ""
            prompt_lower = user_prompt.lower() if user_prompt else ""
            combined = content_lower + " " + prompt_lower
        except Exception as e:
            logger.warning(f"Error processing content/prompt for template detection: {e}")
            content_lower = ""
            prompt_lower = ""
            combined = ""
        
        # Enfold WordPress theme detection (high priority)
        if any(keyword in combined for keyword in ['enfold', 'avia', 'builder']):
            logger.info("Detected Enfold WordPress CSV template")
            return self.templates['enfold']
        
        # WordPress/Blog detection
        elif any(keyword in combined for keyword in ['wordpress', 'blog', 'post', 'article']):
            logger.info("Detected WordPress/Blog CSV template")
            return self.templates['wordpress']
        
        # Product/E-commerce detection
        elif any(keyword in combined for keyword in ['product', 'catalog', 'ecommerce', 'shop', 'store']):
            logger.info("Detected Product Catalog CSV template")
            return self.templates['product']
        
        # Business pages detection
        elif any(keyword in combined for keyword in ['page', 'website', 'business', 'company', 'service']):
            logger.info("Detected Business Pages CSV template")
            return self.templates['business']
        
        # Default to general content
        else:
            logger.info("Using General Content CSV template")
            return self.templates['general']
    
    def extract_csv_headers_from_content(self, content: str) -> Optional[List[str]]:
        """
        Try to extract CSV headers from the generated content
        """
        lines = content.strip().split('\n')
        
        # Look for the first line that could be headers
        for line in lines[:5]:  # Check first 5 lines
            line = line.strip()
            if not line:
                continue
                
            # Check if it looks like CSV headers (has commas, no quotes around everything)
            if ',' in line and not line.startswith('"') and line.count(',') >= 2:
                # Try to parse as CSV
                try:
                    reader = csv.reader(io.StringIO(line))
                    headers = next(reader)
                    # Validate headers (should be reasonable column names)
                    if all(len(h.strip()) > 0 and len(h.strip()) < 50 for h in headers):
                        logger.info(f"Extracted headers from content: {headers}")
                        return [h.strip() for h in headers]
                except Exception:
                    continue
        
        return None
    
    def clean_csv_content(self, content: str) -> str:
        """
        Clean LLM-generated content to remove non-CSV elements
        """
        lines = content.strip().split('\n')
        csv_lines = []
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
                
            # Skip obvious non-CSV lines
            if line.startswith('#') or line.startswith('Here') or line.startswith('I'):
                continue
            if 'csv' in line.lower() and 'content' in line.lower():
                continue
            if line.startswith('```'):
                continue
                
            # If line has commas, it's likely CSV data
            if ',' in line:
                csv_lines.append(line)
        
        return '\n'.join(csv_lines)
    
    def parse_llm_content_to_rows(self, content: str, headers: List[str]) -> List[Dict[str, str]]:
        """
        Parse LLM-generated content into structured CSV rows
        """
        cleaned_content = self.clean_csv_content(content)
        lines = cleaned_content.split('\n')
        rows = []
        
        for line_num, line in enumerate(lines):
            if not line.strip():
                continue
                
            try:
                # Try to parse as CSV
                reader = csv.reader(io.StringIO(line))
                row_data = next(reader)
                
                # Create row dictionary
                row_dict = {}
                for i, header in enumerate(headers):
                    if i < len(row_data):
                        row_dict[header] = row_data[i].strip()
                    else:
                        row_dict[header] = ""
                
                rows.append(row_dict)
                
            except Exception as e:
                logger.warning(f"Failed to parse line {line_num}: {line[:50]}... Error: {e}")
                continue
        
        return rows
    
    def generate_slug_from_title(self, title: str) -> str:
        """Generate URL-friendly slug from title"""
        import re
        # Convert to lowercase and replace non-alphanumeric with dashes
        slug = re.sub(r'[^a-zA-Z0-9\s]', '', title.lower())
        slug = re.sub(r'\s+', '-', slug.strip())
        slug = slug.strip('-')
        return slug[:200]  # Limit length
    
    def _post_process_enfold_rows(self, rows: List[Dict[str, str]], headers: List[str]) -> List[Dict[str, str]]:
        """Post-process rows for Enfold WordPress requirements"""
        processed_rows = []
        
        for i, row in enumerate(rows):
            processed_row = row.copy()
            
            # Generate 5-digit ID if missing or invalid
            if 'ID' in headers:
                current_id = processed_row.get('ID', '').strip()
                if not current_id or not current_id.isdigit() or len(current_id) != 5:
                    processed_row['ID'] = f"{10001 + i:05d}"
            
            # Generate slug from title if missing
            if 'slug' in headers and 'Title' in headers:
                title = processed_row.get('Title', '')
                if title and not processed_row.get('slug'):
                    processed_row['slug'] = self.generate_slug_from_title(title)
            
            # Set Image to same value as Category
            if 'Image' in headers and 'Category' in headers:
                category = processed_row.get('Category', '')
                if category:
                    processed_row['Image'] = category
            
            # Ensure Tags has at least 12 tags
            if 'Tags' in headers:
                current_tags = processed_row.get('Tags', '').strip()
                if current_tags:
                    tag_list = [tag.strip() for tag in current_tags.split(',') if tag.strip()]
                    if len(tag_list) < 12:
                        # Add generic tags to reach 12
                        base_tags = ['legal', 'attorney', 'law', 'consultation', 'professional', 
                                   'expert', 'services', 'advice', 'representation', 'client']
                        for base_tag in base_tags:
                            if base_tag not in tag_list and len(tag_list) < 12:
                                tag_list.append(base_tag)
                    processed_row['Tags'] = ', '.join(tag_list[:12])  # Limit to 12 tags
            
            processed_rows.append(processed_row)
        
        return processed_rows
    
    def generate_structured_csv_prompt(self, user_request: str, template: List[CSVColumn]) -> str:
        """
        Generate an enhanced prompt for structured CSV content
        """
        headers = [col.name for col in template]
        header_descriptions = {col.name: col.description for col in template}
        
        prompt = f"""You are a professional CSV content generator. Generate a complete CSV file based on this request: "{user_request}"

REQUIRED CSV FORMAT:
- First line MUST be headers: {','.join(headers)}
- Each subsequent line MUST be a properly formatted CSV row
- Use proper CSV escaping (quotes around text with commas)
- Generate realistic, professional content

COLUMN SPECIFICATIONS:
"""
        
        for col in template:
            prompt += f"- {col.name}: {col.description}"
            if col.max_length:
                prompt += f" (max {col.max_length} chars)"
            prompt += "\n"
        
        # Add special instructions for Enfold template
        if template == CSVTemplate.ENFOLD_WORDPRESS:
            prompt += f"""
SPECIAL ENFOLD WORDPRESS REQUIREMENTS:
- ID: Generate 5-digit numbers (10001, 10002, etc.)
- Content: Use basic HTML tags compatible with Avia Builder: <h1>, <h2>, <h3>, <strong>, <em>, <a href="">, <ul>, <li>, <p>
- Excerpt: Create compelling 1-2 sentence summaries
- Tags: Include exactly 12+ relevant tags per row (comma-separated)
- slug: Auto-generate from title (lowercase, dashes for spaces, no special chars)
- Image: Set to same value as Category field

FORMATTING REQUIREMENTS:
1. Start with the header row: {','.join(headers)}
2. Generate 3-10 data rows (based on request)
3. Use proper CSV formatting with quotes around text fields
4. Make content unique, realistic, and valuable for WordPress/Enfold theme
5. Ensure each row has the correct number of columns
6. NO explanations, disclaimers, or meta-text
7. Generate ONLY the CSV content

Generate the complete CSV content now:"""
        else:
            prompt += f"""
FORMATTING REQUIREMENTS:
1. Start with the header row: {','.join(headers)}
2. Generate 3-10 data rows (based on request)
3. Use proper CSV formatting with quotes around text fields
4. Make content unique, realistic, and valuable
5. Ensure each row has the correct number of columns
6. NO explanations, disclaimers, or meta-text
7. Generate ONLY the CSV content

Generate the complete CSV content now:"""
        
        return prompt
    
    def format_content_as_csv(self, 
                            content: str, 
                            user_prompt: str = "",
                            specified_headers: Optional[List[str]] = None) -> str:
        """
        Format LLM-generated content as a proper CSV file
        """
        try:
            # 1. Determine CSV structure
            if specified_headers:
                headers = specified_headers
                logger.info(f"Using specified headers: {headers}")
            else:
                # Try to extract headers from content
                extracted_headers = self.extract_csv_headers_from_content(content)
                if extracted_headers:
                    headers = extracted_headers
                else:
                    # Use template-based detection
                    template = self.detect_csv_template(content, user_prompt)
                    headers = [col.name for col in template]
                    logger.info(f"Using template headers: {headers}")
            
            # 2. Parse content into rows
            rows = self.parse_llm_content_to_rows(content, headers)
            
            # 2.5. Post-process for Enfold WordPress requirements if applicable
            template = self.detect_csv_template(content, user_prompt)
            if template == CSVTemplate.ENFOLD_WORDPRESS:
                rows = self._post_process_enfold_rows(rows, headers)
            
            # 3. Generate properly formatted CSV
            output = io.StringIO()
            writer = csv.writer(output, quoting=csv.QUOTE_ALL)
            
            # Write headers
            writer.writerow(headers)
            
            # Write data rows
            for row in rows:
                row_data = [row.get(header, "") for header in headers]
                writer.writerow(row_data)
            
            formatted_csv = output.getvalue()
            output.close()
            
            logger.info(f"Successfully formatted CSV with {len(headers)} columns and {len(rows)} data rows")
            return formatted_csv
            
        except Exception as e:
            logger.error(f"Error formatting CSV content: {e}")
            # Fallback: return original content
            return content
    
    def create_sample_csv(self, template_name: str = "general", num_rows: int = 3) -> str:
        """
        Create a sample CSV file using the specified template
        """
        if template_name not in self.templates:
            template_name = "general"
        
        template = self.templates[template_name]
        headers = [col.name for col in template]
        
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        
        # Write headers
        writer.writerow(headers)
        
        # Write sample rows
        for i in range(num_rows):
            sample_row = []
            for col in template:
                if col.name in ['title', 'post_title', 'page_title', 'product_name']:
                    sample_row.append(f"Sample {col.name.replace('_', ' ').title()} {i+1}")
                elif col.name in ['content', 'post_content', 'page_content', 'product_description']:
                    sample_row.append(f"This is sample content for item {i+1}. It contains detailed information about the topic.")
                elif col.name in ['category', 'post_category', 'page_category', 'product_category']:
                    sample_row.append(f"Category {i+1}")
                elif col.name in ['tags', 'post_tags', 'meta_keywords']:
                    sample_row.append(f"tag{i+1}, sample, content")
                elif col.name in ['date', 'post_date', 'publish_date', 'date_created']:
                    sample_row.append("2025-01-01")
                elif col.name in ['status', 'post_status']:
                    sample_row.append("published")
                else:
                    sample_row.append(f"Sample {col.name} {i+1}")
            writer.writerow(sample_row)
        
        formatted_csv = output.getvalue()
        output.close()
        
        return formatted_csv

# Convenience functions for easy integration
def format_csv_content(content: str, user_prompt: str = "", headers: Optional[List[str]] = None) -> str:
    """Convenience function to format content as CSV"""
    formatter = CSVFormatter()
    return formatter.format_content_as_csv(content, user_prompt, headers)

def generate_csv_prompt(user_request: str, template_name: str = "auto") -> str:
    """Convenience function to generate enhanced CSV prompts"""
    formatter = CSVFormatter()
    
    if template_name == "auto":
        template = formatter.detect_csv_template("", user_request)
    else:
        template = formatter.templates.get(template_name, formatter.templates["general"])
    
    return formatter.generate_structured_csv_prompt(user_request, template)

# Legacy csv_writer_v2 compatibility functions
def _sanitize_csv_field(value):
    """Sanitize CSV field values (from csv_writer_v2)"""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace('"', '""')
    return value

def write_csv(rows, output_path: str, fieldnames):
    """Write dictionaries to CSV file (from csv_writer_v2)"""
    import csv
    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for row in rows:
            sanitized = {
                key: _sanitize_csv_field(row.get(key, "")) for key in fieldnames
            }
            writer.writerow(sanitized)
    return output_path 