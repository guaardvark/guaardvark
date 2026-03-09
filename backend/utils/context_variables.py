# backend/utils/context_variables.py
# Context Variables System for Dynamic CSV Generation
# Version 1.0: Replace hardcoded values with dynamic templates

import logging
import re
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class ContextTemplate:
    """Template for context variables with defaults and validation"""
    name: str
    description: str
    variables: Dict[str, Any]
    default_values: Dict[str, str]
    required_fields: List[str]
    suggested_topics: List[str]
    industry: str = "general"
    
    def validate(self) -> List[str]:
        """Validate that all required fields are present"""
        errors = []
        for field in self.required_fields:
            if field not in self.variables or not self.variables[field]:
                errors.append(f"Missing required field: {field}")
        return errors
    
    def get_populated_variables(self) -> Dict[str, str]:
        """Get variables with defaults filled in for missing values"""
        result = {}
        for key, default in self.default_values.items():
            result[key] = self.variables.get(key, default)
        return result

class ContextVariablesExtractor:
    """Extracts context variables from natural language requests"""
    
    def __init__(self):
        # Patterns for extracting different types of information
        self.patterns = {
            'client': [
                r"(?:client|company|organization|business)(?:\s+is|\s+name)?[:\s]+([^,\n\.]+)",
                r"for\s+([A-Z][a-zA-Z\s&\.-]+)(?:\s+company|\s+corp|\s+inc|\s+llc|\s+ltd)?",
                r"(?:working\s+with|building\s+for|creating\s+for)\s+([A-Z][a-zA-Z\s&\.-]+)",
            ],
            'project': [
                r"(?:project|campaign|initiative)(?:\s+is|\s+name)?[:\s]+([^,\n\.]+)",
                r"(?:creating|building|developing|generating)\s+([^,\n\.]+?)(?:\s+for|\s+to)",
                r"project[:\s]+[\"']?([^\"'\n]+)[\"']?",
            ],
            'website': [
                r"(?:website|domain|url|site)(?:\s+is)?[:\s]+((?:https?://)?[a-zA-Z0-9\.-]+\.[a-zA-Z]{2,})",
                r"(?:for|on|at)\s+((?:www\.)?[a-zA-Z0-9\.-]+\.[a-zA-Z]{2,})",
                r"(?:visit|check|see)\s+((?:https?://)?[a-zA-Z0-9\.-]+\.[a-zA-Z]{2,})",
            ],
            'industry': [
                r"(?:industry|sector|field|domain)[:\s]+([^,\n\.]+)",
                r"(?:in\s+the\s+|specializing\s+in\s+)([a-zA-Z\s]+?)(?:\s+industry|\s+sector|\s+field)",
                r"(?:legal|medical|tech|technology|healthcare|finance|real\s+estate|data\s+center|consulting)",
            ],
            'content_type': [
                r"(?:landing\s+pages?|blog\s+posts?|articles?|pages?|content)",
                r"(?:seo|marketing|advertising|promotional)\s+(?:content|material|copy)",
                r"(?:website\s+content|web\s+pages?|marketing\s+materials?)",
            ],
            'quantity': [
                r"(\d+)\s*(?:pages?|articles?|posts?|items?|entries?|pieces?)",
                r"(?:generate|create|make|build)\s+(\d+)",
                r"(?:about|around|approximately)\s+(\d+)",
                r"Generate\s+(\d+)",
                r"(\d+)\s+(?:professional|legal|website|content)",
            ],
            'word_count': [
                r"(\d+)(?:\+|plus)?\s*words?",
                r"(?:about|around|approximately)\s+(\d+)\s*words?",
                r"(\d+)(?:-|\s*to\s*)(\d+)\s*words?",
            ],
        }
    
    def extract_from_text(self, text: str) -> Dict[str, Any]:
        """Extract context variables from natural language text"""
        text_lower = text.lower()
        extracted = {}
        
        for variable_type, patterns in self.patterns.items():
            for pattern in patterns:
                matches = re.findall(pattern, text_lower, re.IGNORECASE)
                if matches:
                    if variable_type == 'quantity':
                        extracted[variable_type] = int(matches[0]) if matches[0].isdigit() else None
                    elif variable_type == 'word_count':
                        if isinstance(matches[0], tuple):
                            # Range like "500-1000 words"
                            extracted[variable_type] = int(matches[0][1]) if matches[0][1].isdigit() else 500
                        else:
                            extracted[variable_type] = int(matches[0]) if matches[0].isdigit() else 500
                    else:
                        extracted[variable_type] = matches[0].strip()
                    break
        
        # Clean up extracted values
        if 'client' in extracted:
            extracted['client'] = self._clean_client_name(extracted['client'])
        
        if 'website' in extracted:
            extracted['website'] = self._clean_website_url(extracted['website'])
            
        if 'project' in extracted:
            extracted['project'] = self._clean_project_name(extracted['project'])
        
        return extracted
    
    def _clean_client_name(self, name: str) -> str:
        """Clean and format client name"""
        # Remove common suffixes and clean up
        name = re.sub(r'\s+(company|corp|corporation|inc|incorporated|llc|ltd|limited)\.?$', '', name, flags=re.IGNORECASE)
        return name.strip().title()
    
    def _clean_website_url(self, url: str) -> str:
        """Clean and format website URL"""
        url = url.strip().lower()
        if not url.startswith(('http://', 'https://')):
            if not url.startswith('www.'):
                url = 'www.' + url
        return url
    
    def _clean_project_name(self, name: str) -> str:
        """Clean and format project name"""
        return name.strip().title()

class ContextVariablesManager:
    """Manages context variable templates and generation"""
    
    def __init__(self):
        self.extractor = ContextVariablesExtractor()
        self.templates = self._load_default_templates()
    
    def _load_default_templates(self) -> Dict[str, ContextTemplate]:
        """Load default context templates for different industries"""
        return {
            'legal_services': ContextTemplate(
                name="Legal Services",
                description="Template for legal services companies and law firms",
                variables={},
                default_values={
                    'client': '[Client Name]',
                    'project': '[Project Name]',
                    'website': '[website-url.com]',
                    'industry': 'legal services',
                    'content_type': 'landing pages',
                    'target_word_count': '500',
                    'concurrent_workers': '10'
                },
                required_fields=['client', 'project', 'website'],
                # DISABLED: suggested_topics override user input - causing legal bias
                # This list was forcing all generation to use legal topics regardless of user input
                suggested_topics=[
                    # All legal topics commented out to prevent topic override
                    # Users should provide their own topics via the API
                    # "Business Law Consultation Services",
                    # "Corporate Legal Advisory",
                    # ... (ALL 80+ legal topics removed to prevent overriding user input)
                ],
                industry="legal"
            ),
            
            'data_center': ContextTemplate(
                name="Data Center Services",
                description="Template for data center and technology infrastructure companies",
                variables={},
                default_values={
                    'client': '[Client Name]',
                    'project': '[Project Name]',
                    'website': '[website-url.com]',
                    'industry': 'technology infrastructure',
                    'content_type': 'landing pages',
                    'target_word_count': '500',
                    'concurrent_workers': '10'
                },
                required_fields=['client', 'project', 'website'],
                suggested_topics=[
                    "Colocation Services",
                    "Cloud Infrastructure Solutions",
                    "Managed IT Services",
                    "Network Security Solutions",
                    "Disaster Recovery Planning",
                    "Virtualization Services",
                    "Storage Solutions",
                    "Bandwidth and Connectivity",
                    "Compliance and Certification",
                    "24/7 Technical Support"
                ],
                industry="technology"
            ),
            
            'healthcare': ContextTemplate(
                name="Healthcare Services",
                description="Template for healthcare providers and medical practices",
                variables={},
                default_values={
                    'client': '[Client Name]',
                    'project': '[Project Name]',
                    'website': '[website-url.com]',
                    'industry': 'healthcare',
                    'content_type': 'service pages',
                    'target_word_count': '600',
                    'concurrent_workers': '8'
                },
                required_fields=['client', 'project', 'website'],
                suggested_topics=[
                    "Primary Care Services",
                    "Specialist Consultations",
                    "Preventive Health Screenings",
                    "Chronic Disease Management",
                    "Mental Health Services",
                    "Emergency Care",
                    "Diagnostic Imaging",
                    "Laboratory Services",
                    "Physical Therapy",
                    "Telemedicine Services"
                ],
                industry="healthcare"
            ),
            
            'generic': ContextTemplate(
                name="Generic Business",
                description="Template for general business and professional services",
                variables={},
                default_values={
                    'client': '[Client Name]',
                    'project': '[Project Name]',
                    'website': '[website-url.com]',
                    'industry': 'professional services',
                    'content_type': 'marketing content',
                    'target_word_count': '500',
                    'concurrent_workers': '10'
                },
                required_fields=['client', 'project'],
                suggested_topics=[
                    "Professional Consulting Services",
                    "Business Strategy Development",
                    "Market Analysis and Research",
                    "Process Optimization",
                    "Team Training and Development",
                    "Quality Assurance",
                    "Customer Service Excellence",
                    "Technology Implementation",
                    "Risk Management",
                    "Performance Improvement"
                ],
                industry="general"
            )
        }
    
    def detect_template(self, text: str, extracted_vars: Optional[Dict] = None) -> str:
        """Detect the most appropriate template based on text content"""
        text_lower = text.lower()
        extracted_vars = extracted_vars or {}
        
        # Keywords for different industries
        # DISABLED: Legal template auto-detection causes bias - always use 'generic' template
        industry_keywords = {
            # 'legal_services': ['legal', 'law', 'attorney', 'lawyer', 'litigation', 'contract', 'compliance', 'regulatory'],
            'data_center': ['data center', 'datacenter', 'server', 'cloud', 'hosting', 'colocation', 'infrastructure', 'networking'],
            'healthcare': ['healthcare', 'medical', 'doctor', 'physician', 'clinic', 'hospital', 'patient', 'treatment'],
        }
        
        # Check industry from extracted variables
        if 'industry' in extracted_vars:
            industry = extracted_vars['industry'].lower()
            for template_key, keywords in industry_keywords.items():
                if any(keyword in industry for keyword in keywords):
                    return template_key
        
        # Check text content for industry keywords
        for template_key, keywords in industry_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                return template_key
        
        return 'generic'
    
    def create_context_from_text(self, text: str, template_override: Optional[str] = None) -> ContextTemplate:
        """Create a populated context template from natural language text"""
        
        # Extract variables from text
        extracted_vars = self.extractor.extract_from_text(text)
        
        # Detect or use provided template
        template_key = template_override or self.detect_template(text, extracted_vars)
        base_template = self.templates.get(template_key, self.templates['generic'])
        
        # Create a copy and populate with extracted variables
        context = ContextTemplate(
            name=base_template.name,
            description=base_template.description,
            variables=extracted_vars,
            default_values=base_template.default_values.copy(),
            required_fields=base_template.required_fields.copy(),
            suggested_topics=base_template.suggested_topics.copy(),
            industry=base_template.industry
        )
        
        return context
    
    def get_bulk_generation_payload(self, context: ContextTemplate, **overrides) -> Dict[str, Any]:
        """Generate bulk CSV generation payload from context template"""
        
        # Get populated variables with defaults
        populated_vars = context.get_populated_variables()
        
        # Base payload structure
        payload = {
            "client": populated_vars.get('client'),
            "project": populated_vars.get('project'),
            "website": populated_vars.get('website'),
            "topics": context.suggested_topics if context.suggested_topics else "auto",
            "num_items": context.variables.get('quantity', 20),
            "target_word_count": int(populated_vars.get('target_word_count', 500)),
            "concurrent_workers": int(populated_vars.get('concurrent_workers', 10)),
            "batch_size": min(20, max(5, context.variables.get('quantity', 20) // 4)),
            "resume_from_id": None
        }
        
        # Apply any overrides
        payload.update(overrides)
        
        return payload
    
    def replace_placeholders(self, text: str, context: ContextTemplate) -> str:
        """Replace context variable placeholders in text"""
        populated_vars = context.get_populated_variables()
        
        # Define placeholder mappings
        placeholder_map = {
            '[CLIENT]': populated_vars.get('client', 'Professional Services'),
            '[PROJECT]': populated_vars.get('project', 'Marketing Campaign'),
            '[WEBSITE]': populated_vars.get('website', 'professional-website.com'),
            '[INDUSTRY]': populated_vars.get('industry', 'professional services'),
            '[CONTENT_TYPE]': populated_vars.get('content_type', 'content'),
            '[PAGE_COUNT]': str(context.variables.get('quantity', 20)),
            '[WORD_COUNT]': populated_vars.get('target_word_count', '500'),
            '[CONCURRENT_WORKERS]': populated_vars.get('concurrent_workers', '10'),
        }
        
        # Replace placeholders
        result = text
        for placeholder, value in placeholder_map.items():
            result = result.replace(placeholder, str(value))
        
        return result

# Global instance for easy access
context_manager = ContextVariablesManager() 