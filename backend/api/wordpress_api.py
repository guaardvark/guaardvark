# backend/api/wordpress_api.py
"""
WordPress Integration API
Handles WordPress site registration, content pulling, processing, and pushing
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.models import WordPressSite, WordPressPage, Client, Project, Website, db
from backend.services.wordpress_api_service import WordPressAPIService
from backend.services.llamanator_api_service import LlamanatorAPIService

wordpress_bp = Blueprint("wordpress_api", __name__, url_prefix="/api/wordpress")
logger = logging.getLogger(__name__)


@wordpress_bp.route("/sites", methods=["GET"])
def list_wordpress_sites():
    """List all registered WordPress sites"""
    try:
        sites = db.session.query(WordPressSite).all()
        return (
            jsonify({"success": True, "data": [site.to_dict() for site in sites]}),
            200,
        )
    except Exception as e:
        logger.error(f"Error listing WordPress sites: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/sites", methods=["POST"])
def register_wordpress_site():
    """Register a new WordPress site"""
    try:
        data = request.get_json()

        # Validate required fields based on connection type
        connection_type = data.get("connection_type", "llamanator")

        if connection_type == "llamanator":
            required_fields = ["url", "api_key"]
        else:
            required_fields = ["url", "username", "api_key"]

        for field in required_fields:
            if field not in data or not data[field]:
                return (
                    jsonify(
                        {"success": False, "error": f"Missing required field: {field}"}
                    ),
                    400,
                )

        # Check if site already exists
        existing = db.session.query(WordPressSite).filter_by(url=data["url"]).first()
        if existing:
            return (
                jsonify(
                    {"success": False, "error": "WordPress site already registered"}
                ),
                409,
            )

        # Test connection with appropriate service
        if connection_type == "llamanator":
            wp_service = LlamanatorAPIService(
                site_url=data["url"], api_key=data["api_key"]
            )
        else:
            wp_service = WordPressAPIService(
                site_url=data["url"], username=data["username"], api_key=data["api_key"]
            )

        success, error = wp_service.test_connection()

        if not success:
            return (
                jsonify(
                    {"success": False, "error": f"Connection test failed: {error}"}
                ),
                400,
            )

        # Create WordPressSite record
        # For LLAMANATOR2 connections, username is optional but SQLite requires a value
        # So we'll use an empty string as a placeholder
        username_value = (
            data.get("username")
            if connection_type != "llamanator"
            else (data.get("username") or "")
        )

        wp_site = WordPressSite(
            url=data["url"],
            site_name=data.get("site_name"),
            username=username_value,  # Use empty string for LLAMANATOR2 if not provided
            api_key=data["api_key"],  # TODO: Encrypt this
            connection_type=connection_type,
            client_id=data.get("client_id"),
            project_id=data.get("project_id"),
            website_id=data.get("website_id"),
            pull_settings=json.dumps(data.get("pull_settings", {})),
            push_settings=json.dumps(data.get("push_settings", {})),
            status="active",
            last_test_at=datetime.now(),
        )

        db.session.add(wp_site)
        db.session.commit()

        logger.info(f"Registered WordPress site: {wp_site.url} (ID: {wp_site.id})")

        return jsonify({"success": True, "data": wp_site.to_dict()}), 201

    except IntegrityError as e:
        db.session.rollback()
        logger.error(
            f"Database integrity error registering WordPress site: {e}", exc_info=True
        )
        # Provide more detailed error message
        error_msg = str(e)
        if "NOT NULL constraint failed" in error_msg:
            return (
                jsonify(
                    {"success": False, "error": f"Required field missing: {error_msg}"}
                ),
                400,
            )
        return (
            jsonify(
                {"success": False, "error": f"Database integrity error: {error_msg}"}
            ),
            400,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error registering WordPress site: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/sites/<int:site_id>", methods=["GET"])
def get_wordpress_site(site_id: int):
    """Get WordPress site details"""
    try:
        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        return jsonify({"success": True, "data": wp_site.to_dict()}), 200
    except Exception as e:
        logger.error(f"Error getting WordPress site: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/sites/<int:site_id>", methods=["PUT"])
def update_wordpress_site(site_id: int):
    """Update WordPress site settings"""
    try:
        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        data = request.get_json()

        # Update fields if provided
        if "site_name" in data:
            wp_site.site_name = data["site_name"]
        if "username" in data:
            # For LLAMANATOR2, allow empty string; for WordPress, require actual value
            connection_type = wp_site.connection_type or "llamanator"
            if connection_type == "llamanator":
                wp_site.username = data["username"] or ""
            else:
                wp_site.username = data["username"]
        if "api_key" in data:
            wp_site.api_key = data["api_key"]  # TODO: Encrypt
        if "connection_type" in data:
            wp_site.connection_type = data["connection_type"]
        if "client_id" in data:
            wp_site.client_id = data["client_id"]
        if "project_id" in data:
            wp_site.project_id = data["project_id"]
        if "website_id" in data:
            wp_site.website_id = data["website_id"]
        if "pull_settings" in data:
            wp_site.pull_settings = json.dumps(data["pull_settings"])
        if "push_settings" in data:
            wp_site.push_settings = json.dumps(data["push_settings"])
        if "status" in data:
            wp_site.status = data["status"]

        wp_site.updated_at = datetime.now()

        # If credentials changed, test connection
        if "api_key" in data or "username" in data or "connection_type" in data:
            connection_type = wp_site.connection_type or "llamanator"
            if connection_type == "llamanator":
                test_service = LlamanatorAPIService(
                    site_url=wp_site.url, api_key=wp_site.api_key
                )
            else:
                if not wp_site.username:
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": "Username is required for direct WordPress REST API connection",
                            }
                        ),
                        400,
                    )
                test_service = WordPressAPIService(
                    site_url=wp_site.url,
                    username=wp_site.username,
                    api_key=wp_site.api_key,
                )
            success, error = test_service.test_connection()
            wp_site.last_test_at = datetime.now()
            if success:
                wp_site.status = "active"
                wp_site.error_message = None
            else:
                wp_site.status = "error"
                wp_site.error_message = error

        db.session.commit()

        return jsonify({"success": True, "data": wp_site.to_dict()}), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating WordPress site: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/sites/<int:site_id>/test", methods=["POST"])
def test_wordpress_connection(site_id: int):
    """Test WordPress site connection"""
    try:
        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        # Use appropriate service based on connection type
        connection_type = (
            getattr(wp_site, "connection_type", "llamanator") or "llamanator"
        )

        if connection_type == "llamanator":
            wp_service = LlamanatorAPIService(
                site_url=wp_site.url, api_key=wp_site.api_key
            )
        else:
            if not wp_site.username:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Username is required for direct WordPress REST API connection",
                        }
                    ),
                    400,
                )
            wp_service = WordPressAPIService(
                site_url=wp_site.url, username=wp_site.username, api_key=wp_site.api_key
            )

        success, error = wp_service.test_connection()

        wp_site.last_test_at = datetime.now()
        if success:
            wp_site.status = "active"
            wp_site.error_message = None
        else:
            wp_site.status = "error"
            wp_site.error_message = error

        db.session.commit()

        return jsonify(
            {
                "success": success,
                "message": (
                    "Connection successful"
                    if success
                    else f"Connection failed: {error}"
                ),
                "data": wp_site.to_dict(),
            }
        ), (200 if success else 400)

    except Exception as e:
        logger.error(f"Error testing WordPress connection: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/sites/<int:site_id>", methods=["DELETE"])
def delete_wordpress_site(site_id: int):
    """Delete WordPress site registration"""
    try:
        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        db.session.delete(wp_site)
        db.session.commit()

        return jsonify({"success": True, "message": "WordPress site deleted"}), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting WordPress site: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/pull/list", methods=["POST"])
def pull_page_list():
    """Pull list of pages/posts from WordPress site"""
    try:
        data = request.get_json() or {}
        site_id = data.get("site_id")
        post_type = data.get("post_type", "post")
        per_page = data.get("per_page", 100)
        max_pages = data.get("max_pages")
        filters = data.get("filters")

        if not site_id:
            return jsonify({"success": False, "error": "site_id is required"}), 400

        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        # Use content puller service
        from backend.services.wordpress_content_puller import WordPressContentPuller

        puller = WordPressContentPuller(site_id=site_id)
        result = puller.pull_page_list(
            post_type=post_type, per_page=per_page, max_pages=max_pages, filters=filters
        )

        if not result["success"]:
            return jsonify(result), 400

        # Return simplified post list for API response
        posts_data = []
        for post in result.get("posts", []):
            posts_data.append(
                {
                    "wordpress_post_id": post.get("id"),
                    "title": post.get("title", {}).get("rendered", ""),
                    "slug": post.get("slug"),
                    "status": post.get("status"),
                    "date": post.get("date"),
                    "link": post.get("link"),
                }
            )

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "posts": posts_data,
                        "total_pulled": result.get("total_pulled", len(posts_data)),
                        "post_type": post_type,
                        "job_id": result.get("job_id"),
                    },
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error pulling page list: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/pull/page/<int:site_id>/<int:post_id>", methods=["POST"])
def pull_single_page(site_id: int, post_id: int):
    """Pull a single page/post from WordPress"""
    try:
        data = request.get_json() or {}
        post_type = data.get("post_type", "post")
        update_existing = data.get("update_existing", True)

        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        # Use content puller service
        from backend.services.wordpress_content_puller import WordPressContentPuller

        puller = WordPressContentPuller(site_id=site_id)
        result = puller.pull_single_page(
            post_id=post_id, post_type=post_type, update_existing=update_existing
        )

        if not result["success"]:
            return jsonify(result), 400

        return (
            jsonify(
                {
                    "success": True,
                    "data": result.get("data"),
                    "action": result.get("action", "created"),
                }
            ),
            200,
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error pulling WordPress page: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/pull/sitemap", methods=["POST"])
def pull_sitemap():
    """Pull and parse WordPress sitemap"""
    try:
        data = request.get_json() or {}
        site_id = data.get("site_id")
        extract_post_ids = data.get("extract_post_ids", False)

        if not site_id:
            return jsonify({"success": False, "error": "site_id is required"}), 400

        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        # Use content puller service
        from backend.services.wordpress_content_puller import WordPressContentPuller

        puller = WordPressContentPuller(site_id=site_id)
        result = puller.pull_from_sitemap(extract_post_ids=extract_post_ids)

        if not result["success"]:
            return jsonify(result), 400

        return jsonify({"success": True, "data": result}), 200

    except Exception as e:
        logger.error(f"Error pulling sitemap: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/pull/bulk", methods=["POST"])
def pull_bulk_pages():
    """Pull multiple pages from WordPress"""
    try:
        data = request.get_json() or {}
        site_id = data.get("site_id")
        post_ids = data.get("post_ids", [])
        post_type = data.get("post_type", "post")

        if not site_id:
            return jsonify({"success": False, "error": "site_id is required"}), 400

        if not post_ids or not isinstance(post_ids, list):
            return (
                jsonify(
                    {"success": False, "error": "post_ids must be a non-empty list"}
                ),
                400,
            )

        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        # Use content puller service
        from backend.services.wordpress_content_puller import WordPressContentPuller

        puller = WordPressContentPuller(site_id=site_id)
        result = puller.pull_bulk_pages(post_ids=post_ids, post_type=post_type)

        return jsonify({"success": result["success"], "data": result}), (
            200 if result["success"] else 400
        )

    except Exception as e:
        logger.error(f"Error in bulk pull: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/pull/status/<int:site_id>", methods=["GET"])
def get_pull_status(site_id: int):
    """Get pull status for a WordPress site"""
    try:
        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return jsonify({"success": False, "error": "WordPress site not found"}), 404

        # Use content puller service
        from backend.services.wordpress_content_puller import WordPressContentPuller

        puller = WordPressContentPuller(site_id=site_id)
        result = puller.get_pull_status()

        return jsonify({"success": result["success"], "data": result}), 200

    except Exception as e:
        logger.error(f"Error getting pull status: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# --- Processing Endpoints ---


@wordpress_bp.route("/process/page/<int:page_id>", methods=["POST"])
def process_single_page(page_id: int):
    """Process and improve a single WordPress page"""
    try:
        data = request.get_json() or {}
        process_type = data.get("type", "full")  # full, content, seo, schema

        wp_page = db.session.get(WordPressPage, page_id)
        if not wp_page:
            return jsonify({"success": False, "error": "WordPress page not found"}), 404

        # Use content processor service
        from backend.services.wordpress_content_processor import (
            WordPressContentProcessor,
        )

        processor = WordPressContentProcessor(page_id=page_id)

        if process_type == "content":
            result = processor.process_content_improvement()
        elif process_type == "seo":
            result = processor.process_seo_optimization()
        elif process_type == "schema":
            result = processor.process_schema_generation()
        else:  # full
            result = processor.process_full_improvement()

        return jsonify(
            {
                "success": result["success"],
                "data": result.get("data"),
                "job_id": result.get("job_id"),
                "error": result.get("error"),
            }
        ), (200 if result["success"] else 400)

    except Exception as e:
        logger.error(f"Error processing page: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/process/queue", methods=["POST"])
def queue_pages_for_processing():
    """Queue multiple pages for processing"""
    try:
        data = request.get_json() or {}
        page_ids = data.get("page_ids", [])
        process_type = data.get("type", "full")
        site_id = data.get("site_id")

        if not page_ids or not isinstance(page_ids, list):
            return (
                jsonify(
                    {"success": False, "error": "page_ids must be a non-empty list"}
                ),
                400,
            )

        # Filter pages by site if provided
        query = db.session.query(WordPressPage).filter(WordPressPage.id.in_(page_ids))

        if site_id:
            query = query.filter(WordPressPage.wordpress_site_id == site_id)

        pages = query.all()

        if not pages:
            return (
                jsonify(
                    {"success": False, "error": "No pages found matching criteria"}
                ),
                404,
            )

        # Queue pages for processing
        queued_count = 0
        skipped_count = 0

        for page in pages:
            # Only queue pages that are pulled and not already processing
            if page.pull_status == "pulled" and page.process_status in [
                "pending",
                None,
            ]:
                page.process_status = "pending"
                queued_count += 1
            else:
                skipped_count += 1

        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "queued": queued_count,
                        "skipped": skipped_count,
                        "total": len(pages),
                        "message": f"Queued {queued_count} pages for {process_type} processing",
                    },
                }
            ),
            200,
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error queueing pages: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/process/queue/execute", methods=["POST"])
def execute_processing_queue():
    """Execute processing for queued pages"""
    try:
        data = request.get_json() or {}
        site_id = data.get("site_id")
        process_type = data.get("type", "full")
        max_pages = data.get("max_pages", 10)  # Process max 10 at a time

        # Get pending pages
        query = db.session.query(WordPressPage).filter(
            WordPressPage.process_status == "pending",
            WordPressPage.pull_status == "pulled",
        )

        if site_id:
            query = query.filter(WordPressPage.wordpress_site_id == site_id)

        pages = query.limit(max_pages).all()

        if not pages:
            return (
                jsonify(
                    {
                        "success": True,
                        "data": {
                            "processed": 0,
                            "message": "No pages pending processing",
                        },
                    }
                ),
                200,
            )

        # Process pages
        from backend.services.wordpress_content_processor import (
            WordPressContentProcessor,
        )

        results = {
            "success": True,
            "total": len(pages),
            "succeeded": 0,
            "failed": 0,
            "errors": [],
        }

        for page in pages:
            try:
                processor = WordPressContentProcessor(page_id=page.id)

                if process_type == "content":
                    result = processor.process_content_improvement()
                elif process_type == "seo":
                    result = processor.process_seo_optimization()
                elif process_type == "schema":
                    result = processor.process_schema_generation()
                else:  # full
                    result = processor.process_full_improvement()

                if result["success"]:
                    results["succeeded"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(
                        {
                            "page_id": page.id,
                            "error": result.get("error", "Unknown error"),
                        }
                    )
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"page_id": page.id, "error": str(e)})
                logger.error(f"Error processing page {page.id}: {e}", exc_info=True)

        results["success"] = results["failed"] == 0

        return jsonify({"success": results["success"], "data": results}), 200

    except Exception as e:
        logger.error(f"Error executing processing queue: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/process/status/<int:page_id>", methods=["GET"])
def get_processing_status(page_id: int):
    """Get processing status for a WordPress page"""
    try:
        wp_page = db.session.get(WordPressPage, page_id)
        if not wp_page:
            return jsonify({"success": False, "error": "WordPress page not found"}), 404

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "page_id": page_id,
                        "process_status": wp_page.process_status,
                        "has_improvements": bool(
                            wp_page.improved_title
                            or wp_page.improved_content
                            or wp_page.improved_meta_title
                        ),
                        "processed_at": (
                            wp_page.processed_at.isoformat()
                            if wp_page.processed_at
                            else None
                        ),
                        "improvement_summary": wp_page.improvement_summary,
                    },
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error getting processing status: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/pages", methods=["GET"])
def list_wordpress_pages():
    """List WordPress pages with filters"""
    try:
        site_id = request.args.get("site_id", type=int)
        process_status = request.args.get("process_status")
        pull_status = request.args.get("pull_status")
        post_type = request.args.get("post_type")
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)

        query = db.session.query(WordPressPage)

        if site_id:
            query = query.filter(WordPressPage.wordpress_site_id == site_id)
        if process_status:
            query = query.filter(WordPressPage.process_status == process_status)
        if pull_status:
            query = query.filter(WordPressPage.pull_status == pull_status)
        if post_type:
            query = query.filter(WordPressPage.post_type == post_type)

        total = query.count()
        pages = (
            query.order_by(WordPressPage.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "pages": [page.to_dict() for page in pages],
                        "total": total,
                        "offset": offset,
                        "limit": limit,
                    },
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error listing WordPress pages: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wordpress_bp.route("/pages/<int:page_id>", methods=["GET"])
def get_wordpress_page(page_id: int):
    """Get a single WordPress page with diff view"""
    try:
        wp_page = db.session.get(WordPressPage, page_id)
        if not wp_page:
            return jsonify({"success": False, "error": "WordPress page not found"}), 404

        # Build diff view data
        diff_data = {
            "title": {
                "original": wp_page.title,
                "improved": wp_page.improved_title,
                "changed": (
                    wp_page.title != wp_page.improved_title
                    if wp_page.improved_title
                    else False
                ),
            },
            "content": {
                "original": wp_page.content,
                "improved": wp_page.improved_content,
                "changed": (
                    wp_page.content != wp_page.improved_content
                    if wp_page.improved_content
                    else False
                ),
            },
            "excerpt": {
                "original": wp_page.excerpt,
                "improved": wp_page.improved_excerpt,
                "changed": (
                    wp_page.excerpt != wp_page.improved_excerpt
                    if wp_page.improved_excerpt
                    else False
                ),
            },
        }

        return (
            jsonify(
                {
                    "success": True,
                    "data": {"page": wp_page.to_dict(), "diff": diff_data},
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error getting WordPress page: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
