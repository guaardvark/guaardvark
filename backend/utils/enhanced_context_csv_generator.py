#!/usr/bin/env python3
"""
Enhanced Context-Aware CSV Generator
Integrates all LLM009 ecosystem capabilities for intelligent content generation
"""

import logging
import json
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from pathlib import Path

from backend.utils.entity_context_enhancer import EntityContextEnhancer
from backend.services.entity_relationship_indexer import EntityRelationshipIndexer
from backend.utils.context_manager import ContextManager
from backend.api.web_search_api import extract_website_content
from backend.utils.xml_sitemap_handler import parse_sitemap
from backend.services.indexing_service import get_or_create_index, query_index
from backend.models import Client, Project, Document, db
from backend.utils.bulk_csv_generator import BulkCSVGenerator, GenerationTask

logger = logging.getLogger(__name__)

@dataclass
class EnhancedGenerationContext:
    """Comprehensive context for generation"""
    client_data: Dict[str, Any]
    project_data: Dict[str, Any] 
    entity_relationships: Dict[str, Any]
    competitor_content: Dict[str, Any]
    client_documents: List[Dict[str, Any]]
    industry_context: str
    target_keywords: List[str]
    content_strategy: Dict[str, Any]

class EnhancedContextCSVGenerator:
    """
    Advanced CSV generator that leverages the full LLM009 ecosystem:
    - Entity relationships and client context
    - Document intelligence and uploaded files
    - Web scraping and competitor analysis
    - Multi-model LLM capabilities
    - Contextual content generation
    """
    
    def __init__(self):
        self.entity_enhancer = EntityContextEnhancer()
        self.relationship_indexer = EntityRelationshipIndexer()
        self.context_manager = ContextManager(max_tokens=16384)
        self.bulk_generator = BulkCSVGenerator()
        self.index = None
        self._initialize_index()
    
    def _initialize_index(self):
        """Initialize the document index for context retrieval"""
        try:
            # BUG FIX #15: Handle new return format from get_or_create_index
            result = get_or_create_index()
            self.index = result[0] if isinstance(result, tuple) else result
            logger.info("Enhanced CSV generator initialized with document index")
        except Exception as e:
            logger.error(f"Failed to initialize document index: {e}")
            self.index = None
    
    async def generate_enhanced_csv(
        self,
        client_id: int,
        project_name: str,
        competitor_url: str,
        num_pages: int = 25,
        output_filename: str = None,
        target_keywords: List[str] = None
    ) -> Dict[str, Any]:
        """
        Generate CSV using full ecosystem intelligence
        """
        try:
            # 1. Build comprehensive context
            context = await self._build_enhanced_context(
                client_id, project_name, competitor_url, target_keywords
            )
            
            # 2. Generate intelligent topics based on context
            topics = self._generate_intelligent_topics(context, num_pages)
            
            # 3. Create enhanced generation tasks
            tasks = self._create_enhanced_tasks(context, topics)
            
            # 4. Generate with full context awareness
            result = await self._generate_with_full_context(tasks, context, output_filename)
            
            return result
            
        except Exception as e:
            logger.error(f"Enhanced CSV generation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "context_gathered": False
            }
    
    async def _build_enhanced_context(
        self, 
        client_id: int, 
        project_name: str, 
        competitor_url: str,
        target_keywords: List[str] = None
    ) -> EnhancedGenerationContext:
        """Build comprehensive context using all available systems"""
        
        # Get client and project data from database
        client = db.session.get(Client, client_id)
        if not client:
            raise ValueError(f"Client {client_id} not found")
        
        project = None
        if project_name:
            project = Project.query.filter_by(client_id=client_id, name=project_name).first()
        
        logger.info(f"Building context for client: {client.name}, project: {project_name}")
        
        # 1. Entity relationship analysis
        entity_relationships = self._analyze_entity_relationships(client, project)
        
        # 2. Competitor content analysis
        competitor_content = await self._analyze_competitor_content(competitor_url)
        
        # 3. Client document intelligence
        client_documents = self._analyze_client_documents(client_id)
        
        # 4. Industry context from uploaded files
        industry_context = self._build_industry_context(client, client_documents)
        
        # 5. Extract strategic keywords
        strategic_keywords = self._extract_strategic_keywords(
            competitor_content, client_documents, target_keywords
        )
        
        # 6. Build content strategy
        content_strategy = self._build_content_strategy(
            client, competitor_content, industry_context
        )
        
        return EnhancedGenerationContext(
            client_data={
                "id": client.id,
                "name": client.name,
                "notes": client.notes or "",
                "email": client.email,
                "website": getattr(client, 'website', ''),
                "business_type": self._extract_business_type(client.notes or "")
            },
            project_data={
                "name": project_name,
                "description": project.description if project else "",
                "context": project.context_data if project else {}
            },
            entity_relationships=entity_relationships,
            competitor_content=competitor_content,
            client_documents=client_documents,
            industry_context=industry_context,
            target_keywords=strategic_keywords,
            content_strategy=content_strategy
        )
    
    def _analyze_entity_relationships(self, client: Client, project: Project = None) -> Dict[str, Any]:
        """Analyze entity relationships using the entity context enhancer"""
        try:
            # Create query about the client and project
            query = f"Client: {client.name}"
            if client.notes:
                query += f" - {client.notes}"
            if project:
                query += f" Project: {project.name}"
            
            # Get enhanced context
            enhanced_context = self.entity_enhancer.enhance_query_context(query, [])
            
            # Add system overview
            system_overview = self.relationship_indexer.create_system_overview_document()
            
            return {
                "entity_mentions": enhanced_context.get("entity_mentions", {}),
                "relationships": enhanced_context.get("entity_relationships", {}),
                "summary": enhanced_context.get("relationship_summary", ""),
                "system_overview": system_overview[:1000]  # Truncate for context
            }
            
        except Exception as e:
            logger.error(f"Entity relationship analysis failed: {e}")
            return {"error": str(e)}
    
    async def _analyze_competitor_content(self, competitor_url: str) -> Dict[str, Any]:
        """Analyze competitor website using web scraping capabilities"""
        try:
            if not competitor_url:
                return {"content": "", "keywords": [], "products": []}
            
            logger.info(f"Analyzing competitor: {competitor_url}")
            
            # Extract website content
            content_data = extract_website_content(competitor_url)
            
            # Extract products/services from content
            products = self._extract_products_from_content(content_data.get("content", ""))
            
            # Extract strategic keywords
            keywords = self._extract_keywords_from_content(content_data.get("content", ""))
            
            return {
                "url": competitor_url,
                "title": content_data.get("title", ""),
                "description": content_data.get("description", ""),
                "content": content_data.get("content", "")[:2000],  # Truncate
                "keywords": keywords,
                "products": products,
                "meta_data": content_data.get("meta_data", {})
            }
            
        except Exception as e:
            logger.error(f"Competitor analysis failed: {e}")
            return {
                "url": competitor_url,
                "content": "",
                "keywords": [],
                "products": [],
                "error": str(e)
            }
    
    def _analyze_client_documents(self, client_id: int) -> List[Dict[str, Any]]:
        """Analyze uploaded client documents using document intelligence"""
        try:
            if not self.index:
                return []
            
            # Query for client-related documents
            client_docs = Document.query.filter_by(client_id=client_id).all()
            
            documents_analysis = []
            for doc in client_docs:
                try:
                    # Query the index for document content
                    query_result = query_index(f"document: {doc.filename}", top_k=3)
                    
                    doc_analysis = {
                        "id": doc.id,
                        "filename": doc.filename,
                        "file_type": doc.file_type,
                        "upload_date": doc.created_at.isoformat(),
                        "content_summary": self._summarize_document_content(query_result),
                        "extracted_keywords": self._extract_doc_keywords(query_result)
                    }
                    documents_analysis.append(doc_analysis)
                    
                except Exception as doc_e:
                    logger.error(f"Error analyzing document {doc.filename}: {doc_e}")
                    continue
            
            return documents_analysis
            
        except Exception as e:
            logger.error(f"Client document analysis failed: {e}")
            return []
    
    def _build_industry_context(self, client: Client, client_documents: List[Dict]) -> str:
        """Build industry context from client data and documents"""
        context_parts = []
        
        # Client notes provide business context
        if client.notes:
            context_parts.append(f"Business Focus: {client.notes}")
        
        # Extract industry context from documents
        for doc in client_documents:
            if doc.get("content_summary"):
                context_parts.append(f"Document Context: {doc['content_summary']}")
        
        # Combine into coherent industry context
        industry_context = " | ".join(context_parts)
        return industry_context[:1000]  # Limit context size
    
    def _extract_strategic_keywords(
        self, 
        competitor_content: Dict[str, Any], 
        client_documents: List[Dict],
        target_keywords: List[str] = None
    ) -> List[str]:
        """Extract strategic keywords from all sources"""
        keywords = set()
        
        # Add target keywords
        if target_keywords:
            keywords.update(target_keywords)
        
        # Extract from competitor content
        keywords.update(competitor_content.get("keywords", []))
        keywords.update(competitor_content.get("products", []))
        
        # Extract from client documents
        for doc in client_documents:
            keywords.update(doc.get("extracted_keywords", []))
        
        return list(keywords)[:20]  # Limit to most relevant
    
    def _build_content_strategy(
        self,
        client: Client,
        competitor_content: Dict[str, Any],
        industry_context: str
    ) -> Dict[str, Any]:
        """Build intelligent content strategy"""
        
        # Determine content approach based on client notes
        business_type = self._extract_business_type(client.notes or "")
        
        # Analyze competitor positioning
        competitor_focus = self._analyze_competitor_positioning(competitor_content)
        
        return {
            "business_type": business_type,
            "content_angle": self._determine_content_angle(business_type, competitor_focus),
            "tone": self._determine_content_tone(business_type),
            "focus_areas": self._identify_focus_areas(industry_context, competitor_content),
            "differentiation": self._identify_differentiation_opportunities(
                client.notes or "", competitor_content
            )
        }
    
    def _generate_intelligent_topics(
        self, 
        context: EnhancedGenerationContext, 
        num_pages: int
    ) -> List[str]:
        """Generate intelligent topics based on comprehensive context"""
        
        # Base topics from business type and client notes
        base_topics = self._generate_base_topics(context.client_data, num_pages // 2)
        
        # Competitor-inspired topics (but differentiated)
        competitor_topics = self._generate_competitor_inspired_topics(
            context.competitor_content, num_pages // 3
        )
        
        # Industry-specific topics from documents
        industry_topics = self._generate_industry_topics(
            context.industry_context, context.client_documents, num_pages // 6
        )
        
        # Fill remaining with strategic topics
        strategic_topics = self._generate_strategic_topics(
            context.target_keywords, context.content_strategy, 
            num_pages - len(base_topics) - len(competitor_topics) - len(industry_topics)
        )
        
        all_topics = base_topics + competitor_topics + industry_topics + strategic_topics
        return all_topics[:num_pages]  # Ensure exact count
    
    def _create_enhanced_tasks(
        self, 
        context: EnhancedGenerationContext, 
        topics: List[str]
    ) -> List[GenerationTask]:
        """Create generation tasks with full context"""
        
        tasks = []
        for i, topic in enumerate(topics, 1):
            task = GenerationTask(
                id=f"enhanced_{i:03d}",
                topic=topic,
                client=context.client_data["name"],
                project=context.project_data["name"],
                website=context.client_data.get("website", ""),
                client_notes=context.client_data["notes"],
                target_keywords=context.target_keywords,
                # Enhanced context for this specific task
                enhanced_context={
                    "business_type": context.content_strategy["business_type"],
                    "content_angle": context.content_strategy["content_angle"],
                    "competitor_insights": context.competitor_content,
                    "industry_context": context.industry_context,
                    "differentiation": context.content_strategy["differentiation"]
                }
            )
            tasks.append(task)
        
        return tasks
    
    async def _generate_with_full_context(
        self, 
        tasks: List[GenerationTask], 
        context: EnhancedGenerationContext,
        output_filename: str = None
    ) -> Dict[str, Any]:
        """Generate CSV with full context awareness"""
        
        # Set up enhanced prompt template
        self._setup_enhanced_template(context)
        
        # Generate using bulk generator with enhanced context
        result = await self.bulk_generator.generate_bulk_csv_async(
            tasks=tasks,
            output_filename=output_filename or f"enhanced_{context.client_data['name']}_content.csv",
            concurrent_workers=3,  # Conservative for enhanced processing
            batch_size=10
        )
        
        return {
            "success": result.get("success", False),
            "output_filename": result.get("output_filename"),
            "pages_generated": len(tasks),
            "context_used": {
                "entity_relationships": bool(context.entity_relationships),
                "competitor_analysis": bool(context.competitor_content.get("content")),
                "client_documents": len(context.client_documents),
                "strategic_keywords": len(context.target_keywords)
            },
            "generation_metadata": result
        }
    
    # Helper methods for content analysis and generation
    def _extract_business_type(self, notes: str) -> str:
        """Extract business type from client notes"""
        notes_lower = notes.lower()
        
        business_types = {
            "manufacturing": ["manufacturing", "metalworks", "fabrication", "industrial"],
            "retail": ["store", "retail", "shop", "sales"],
            "services": ["services", "consulting", "professional"],
            "technology": ["tech", "software", "digital", "IT"],
            "automotive": ["automotive", "truck", "tractor", "vehicle"],
            "agriculture": ["farm", "agricultural", "equipment", "machinery"]
        }
        
        for business_type, keywords in business_types.items():
            if any(keyword in notes_lower for keyword in keywords):
                return business_type
        
        return "professional_services"
    
    def _extract_products_from_content(self, content: str) -> List[str]:
        """Extract product mentions from content"""
        import re
        
        # Look for common product patterns
        product_patterns = [
            r'(?i)\b(?:brush guard|grill guard|push bar|bumper guard|front guard)\b',
            r'(?i)\b(?:tractor|truck|vehicle|equipment)\s+\w+\b',
            r'(?i)\b(?:heavy duty|commercial|industrial)\s+\w+\b'
        ]
        
        products = set()
        for pattern in product_patterns:
            matches = re.findall(pattern, content)
            products.update(matches)
        
        return list(products)[:10]
    
    def _extract_keywords_from_content(self, content: str) -> List[str]:
        """Extract keywords from content"""
        import re
        
        # Simple keyword extraction (can be enhanced with NLP)
        words = re.findall(r'\b[A-Za-z]{4,}\b', content)
        word_freq = {}
        
        for word in words:
            word_lower = word.lower()
            if word_lower not in ['this', 'that', 'with', 'from', 'they', 'have', 'will']:
                word_freq[word_lower] = word_freq.get(word_lower, 0) + 1
        
        # Return top keywords
        sorted_keywords = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [kw[0] for kw in sorted_keywords[:15]]
    
    def _setup_enhanced_template(self, context: EnhancedGenerationContext):
        """Set up enhanced prompt template with full context"""
        
        enhanced_template = f"""
        You are creating professional content for {context.client_data['name']}.
        
        BUSINESS CONTEXT:
        - Business Type: {context.content_strategy['business_type']}
        - Business Notes: {context.client_data['notes']}
        - Content Angle: {context.content_strategy['content_angle']}
        
        COMPETITIVE LANDSCAPE:
        - Competitor Analysis: {context.competitor_content.get('title', 'N/A')}
        - Market Positioning: Differentiate through {context.content_strategy['differentiation']}
        
        CONTENT STRATEGY:
        - Target Keywords: {', '.join(context.target_keywords[:10])}
        - Tone: {context.content_strategy['tone']}
        - Focus Areas: {', '.join(context.content_strategy['focus_areas'])}
        
        Generate professional, SEO-optimized content that:
        1. Reflects the client's specific business focus
        2. Differentiates from competitors
        3. Incorporates relevant industry keywords
        4. Maintains consistent brand voice
        
        Topic: {{topic}}
        Client: {{client}}
        Project: {{project}}
        Website: {{website}}
        """
        
        # Update the bulk generator's template
        self.bulk_generator.enhanced_template = enhanced_template
        
    # Additional helper methods would continue here...
    def _determine_content_angle(self, business_type: str, competitor_focus: str) -> str:
        """Determine unique content angle"""
        angles = {
            "manufacturing": "precision engineering and custom solutions",
            "automotive": "heavy-duty performance and reliability", 
            "retail": "product expertise and customer service",
            "services": "professional expertise and results"
        }
        return angles.get(business_type, "professional excellence")
    
    def _determine_content_tone(self, business_type: str) -> str:
        """Determine appropriate content tone"""
        tones = {
            "manufacturing": "authoritative and technical",
            "automotive": "rugged and performance-focused",
            "retail": "helpful and product-focused",
            "services": "professional and consultative"
        }
        return tones.get(business_type, "professional")
    
    def _generate_base_topics(self, client_data: Dict, count: int) -> List[str]:
        """Generate base topics from client business type"""
        business_notes = client_data.get("notes", "").lower()
        
        if "tractor" in business_notes and "guard" in business_notes:
            return [
                "Heavy Duty Tractor Brush Guards for Agricultural Work",
                "Custom Metalwork Solutions for Farm Equipment", 
                "Protecting Your Investment with Quality Guards",
                "Industrial Fabrication for Heavy Machinery",
                "Durable Construction for Harsh Environments"
            ][:count]
        
        return [f"Professional {client_data['name']} Solutions Topic {i}" for i in range(1, count+1)]
    
    # More helper methods would be implemented here...

# Convenience function for easy integration
async def generate_enhanced_csv(
    client_id: int,
    project_name: str = "Enhanced Content Generation",
    competitor_url: str = "",
    num_pages: int = 25,
    output_filename: str = None,
    target_keywords: List[str] = None
) -> Dict[str, Any]:
    """
    Convenience function to generate enhanced CSV with full ecosystem integration
    """
    generator = EnhancedContextCSVGenerator()
    return await generator.generate_enhanced_csv(
        client_id=client_id,
        project_name=project_name,
        competitor_url=competitor_url,
        num_pages=num_pages,
        output_filename=output_filename,
        target_keywords=target_keywords
    )