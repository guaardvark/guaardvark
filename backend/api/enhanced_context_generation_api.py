#!/usr/bin/env python3
"""
Enhanced Context Generation API
Leverages full LLM009 ecosystem for intelligent content generation
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any

from flask import Blueprint, request, jsonify, current_app

from backend.utils.enhanced_context_csv_generator import (
    EnhancedContextCSVGenerator,
    generate_enhanced_csv
)
from backend.utils.response_utils import success_response, error_response
from backend.models import Client, db

enhanced_gen_bp = Blueprint("enhanced_generation_api", __name__, url_prefix="/api/enhanced-generation")
logger = logging.getLogger(__name__)

@enhanced_gen_bp.route("/csv", methods=["POST"])
def generate_enhanced_csv_endpoint():
    """
    Generate CSV using full ecosystem intelligence
    
    Expected payload:
    {
        "client_id": 6,
        "project_name": "Tractor Equipment Content",
        "competitor_url": "https://www.homesteadimplements.com/grill-guards/",
        "num_pages": 25,
        "output_filename": "bamw_tractor_guards_enhanced.csv",
        "target_keywords": ["tractor brush guards", "heavy duty", "metal fabrication"]
    }
    """
    try:
        data = request.get_json()
        if not data:
            return error_response("Request body must be JSON", 400)

        # Validate required fields
        client_id = data.get("client_id")
        if not client_id:
            return error_response("client_id is required", 400)
        
        # Verify client exists
        client = db.session.get(Client, client_id)
        if not client:
            return error_response(f"Client {client_id} not found", 404)

        # Extract parameters with defaults
        project_name = data.get("project_name", "Enhanced Content Generation")
        competitor_url = data.get("competitor_url", "")
        num_pages = data.get("num_pages", 25)
        output_filename = data.get("output_filename")
        target_keywords = data.get("target_keywords", [])

        logger.info(f"Starting enhanced CSV generation for client {client.name}")
        
        # Create event loop for async generation
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Generate enhanced CSV
            result = loop.run_until_complete(
                generate_enhanced_csv(
                    client_id=client_id,
                    project_name=project_name,
                    competitor_url=competitor_url,
                    num_pages=num_pages,
                    output_filename=output_filename,
                    target_keywords=target_keywords
                )
            )
            
            if result.get("success"):
                logger.info(f"Enhanced CSV generation completed: {result.get('output_filename')}")
                return success_response(
                    message="Enhanced CSV generation completed successfully",
                    data={
                        "client": client.name,
                        "client_id": client_id,
                        "project_name": project_name,
                        "output_filename": result.get("output_filename"),
                        "pages_generated": result.get("pages_generated"),
                        "context_utilized": result.get("context_used"),
                        "competitor_analyzed": bool(competitor_url),
                        "keywords_targeted": len(target_keywords),
                        "generation_metadata": result.get("generation_metadata", {})
                    }
                )
            else:
                error_msg = result.get("error", "Generation failed")
                logger.error(f"Enhanced generation failed: {error_msg}")
                return error_response(f"Enhanced generation failed: {error_msg}", 500)
                
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Enhanced CSV generation endpoint error: {e}", exc_info=True)
        return error_response(f"Generation failed: {str(e)}", 500)

@enhanced_gen_bp.route("/context-preview", methods=["POST"]) 
def preview_generation_context():
    """
    Preview the context that would be used for generation
    Useful for debugging and understanding what data is available
    """
    try:
        data = request.get_json()
        if not data:
            return error_response("Request body must be JSON", 400)

        client_id = data.get("client_id")
        if not client_id:
            return error_response("client_id is required", 400)
        
        client = db.session.get(Client, client_id)
        if not client:
            return error_response(f"Client {client_id} not found", 404)

        project_name = data.get("project_name", "Context Preview")
        competitor_url = data.get("competitor_url", "")
        target_keywords = data.get("target_keywords", [])

        # Initialize generator
        generator = EnhancedContextCSVGenerator()
        
        # Create event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Build context (without full generation)
            context = loop.run_until_complete(
                generator._build_enhanced_context(
                    client_id, project_name, competitor_url, target_keywords
                )
            )
            
            # Return context preview
            return success_response(
                message="Generation context preview",
                data={
                    "client_data": context.client_data,
                    "project_data": context.project_data,
                    "entity_relationships_summary": context.entity_relationships.get("summary", ""),
                    "competitor_content": {
                        "url": context.competitor_content.get("url", ""),
                        "title": context.competitor_content.get("title", ""),
                        "keywords_found": len(context.competitor_content.get("keywords", [])),
                        "products_found": len(context.competitor_content.get("products", []))
                    },
                    "client_documents": [
                        {
                            "filename": doc.get("filename", ""),
                            "file_type": doc.get("file_type", ""),
                            "keywords_extracted": len(doc.get("extracted_keywords", []))
                        }
                        for doc in context.client_documents
                    ],
                    "industry_context": context.industry_context[:200] + "..." if len(context.industry_context) > 200 else context.industry_context,
                    "target_keywords": context.target_keywords,
                    "content_strategy": context.content_strategy,
                    "context_completeness": {
                        "has_client_notes": bool(context.client_data.get("notes")),
                        "has_competitor_data": bool(context.competitor_content.get("content")),
                        "has_client_documents": len(context.client_documents) > 0,
                        "has_entity_relationships": bool(context.entity_relationships),
                        "keywords_available": len(context.target_keywords)
                    }
                }
            )
            
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Context preview error: {e}", exc_info=True)
        return error_response(f"Context preview failed: {str(e)}", 500)

@enhanced_gen_bp.route("/clients/<int:client_id>/capabilities", methods=["GET"])
def get_client_capabilities(client_id: int):
    """
    Get available capabilities and context for a specific client
    Shows what data is available for enhanced generation
    """
    try:
        client = db.session.get(Client, client_id)
        if not client:
            return error_response(f"Client {client_id} not found", 404)

        # Check available data sources
        from backend.models import Document, Project
        
        client_documents = Document.query.filter_by(client_id=client_id).all()
        client_projects = Project.query.filter_by(client_id=client_id).all()
        
        capabilities = {
            "client_info": {
                "id": client.id,
                "name": client.name,
                "notes": client.notes,
                "has_business_description": bool(client.notes),
                "email": client.email
            },
            "available_context": {
                "uploaded_documents": len(client_documents),
                "active_projects": len(client_projects),
                "entity_relationships": True,  # Always available
                "web_scraping": True,  # Always available
                "document_intelligence": len(client_documents) > 0
            },
            "document_details": [
                {
                    "id": doc.id,
                    "filename": doc.filename,
                    "file_type": doc.file_type,
                    "upload_date": doc.created_at.isoformat(),
                    "indexed": True  # Assume indexed if in database
                }
                for doc in client_documents
            ],
            "project_details": [
                {
                    "id": project.id,
                    "name": project.name,
                    "description": project.description,
                    "created_date": project.created_at.isoformat()
                }
                for project in client_projects
            ],
            "generation_readiness": {
                "ready_for_enhanced_generation": bool(client.notes),
                "recommended_competitor_url": "https://example.com/competitor" if client.notes else None,
                "suggested_keywords": client.notes.split() if client.notes else [],
                "context_score": (
                    (1 if client.notes else 0) +
                    (1 if client_documents else 0) + 
                    (1 if client_projects else 0)
                ) / 3 * 100
            }
        }

        return success_response(
            message=f"Client capabilities for {client.name}",
            data=capabilities
        )

    except Exception as e:
        logger.error(f"Client capabilities error: {e}", exc_info=True)
        return error_response(f"Failed to get client capabilities: {str(e)}", 500)

@enhanced_gen_bp.route("/test-integration", methods=["POST"])
def test_enhanced_integration():
    """
    Test endpoint to verify all systems are working together
    """
    try:
        data = request.get_json() or {}
        
        # Test components
        test_results = {}
        
        # Test 1: Entity Context Enhancer
        try:
            from backend.utils.entity_context_enhancer import EntityContextEnhancer
            enhancer = EntityContextEnhancer()
            test_context = enhancer.enhance_query_context("test client", [])
            test_results["entity_enhancer"] = {
                "status": "working",
                "features": ["entity_mentions", "relationships"]
            }
        except Exception as e:
            test_results["entity_enhancer"] = {"status": "error", "error": str(e)}
        
        # Test 2: Web Scraper
        try:
            from backend.api.web_search_api import extract_website_content
            test_results["web_scraper"] = {"status": "available"}
        except Exception as e:
            test_results["web_scraper"] = {"status": "error", "error": str(e)}
        
        # Test 3: Document Index
        try:
            from backend.services.indexing_service import get_or_create_index
            # BUG FIX #15: Handle new return format
            result = get_or_create_index()
            index = result[0] if isinstance(result, tuple) else result
            test_results["document_index"] = {
                "status": "working" if index else "not_initialized",
                "available": index is not None
            }
        except Exception as e:
            test_results["document_index"] = {"status": "error", "error": str(e)}
        
        # Test 4: Context Manager
        try:
            from backend.utils.context_manager import ContextManager
            manager = ContextManager()
            test_results["context_manager"] = {"status": "working", "max_tokens": manager.max_tokens}
        except Exception as e:
            test_results["context_manager"] = {"status": "error", "error": str(e)}
        
        # Test 5: Bulk CSV Generator
        try:
            from backend.utils.bulk_csv_generator import BulkCSVGenerator
            generator = BulkCSVGenerator()
            test_results["bulk_csv_generator"] = {"status": "working"}
        except Exception as e:
            test_results["bulk_csv_generator"] = {"status": "error", "error": str(e)}

        # Overall integration status
        working_components = sum(1 for result in test_results.values() if result.get("status") == "working")
        total_components = len(test_results)
        
        integration_status = {
            "overall_status": "ready" if working_components >= 4 else "partial" if working_components >= 2 else "not_ready",
            "working_components": working_components,
            "total_components": total_components,
            "readiness_percentage": (working_components / total_components) * 100,
            "component_details": test_results
        }

        return success_response(
            message="Enhanced integration test completed",
            data=integration_status
        )

    except Exception as e:
        logger.error(f"Integration test error: {e}", exc_info=True)
        return error_response(f"Integration test failed: {str(e)}", 500)