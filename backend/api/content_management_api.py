#!/usr/bin/env python3
"""
Content Management API
Provides endpoints for managing and tracking generated content
Supports content library, duplicate detection, and upload tracking
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from flask import Blueprint, request, jsonify
from sqlalchemy import and_, or_

from backend.models import Page, Generation, db

# Configure logging
logger = logging.getLogger(__name__)

# Create blueprint
content_mgmt_bp = Blueprint('content_management', __name__, url_prefix='/api/content')

@content_mgmt_bp.route('/pages', methods=['GET'])
def list_pages():
    """List all generated pages with filters"""
    try:
        # Get query parameters
        website = request.args.get('website')
        uploaded = request.args.get('uploaded')  # true/false
        generation_id = request.args.get('generation_id')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        
        # Build query - filter out deleted pages by default
        query = db.session.query(Page).filter(Page.status != 'deleted')

        # Apply filters
        if website:
            query = query.join(Generation).filter(Generation.site_key.like(f'%{website}%'))

        if uploaded is not None:
            if uploaded == 'true':
                query = query.filter(Page.meta_json.like('%"uploaded_to_wp":true%'))
            elif uploaded == 'false':
                query = query.filter(or_(
                    Page.meta_json.like('%"uploaded_to_wp":false%'),
                    Page.meta_json.notlike('%"uploaded_to_wp":true%')
                ))

        if generation_id:
            query = query.filter(Page.generation_id == generation_id)
        
        # Get total count
        total_count = query.count()
        
        # Apply pagination and ordering
        pages = query.order_by(Page.created_at.desc()).offset(offset).limit(limit).all()
        
        # Convert to dict format
        pages_data = []
        for page in pages:
            meta = {}
            if page.meta_json:
                try:
                    meta = json.loads(page.meta_json)
                except (json.JSONDecodeError, TypeError):
                    meta = {}

            # Get generation info for context
            generation = db.session.query(Generation).filter_by(id=page.generation_id).first()

            pages_data.append({
                'id': page.id,
                'title': page.title,
                'slug': page.slug,
                'category': page.category,
                'tags': page.tags,
                'excerpt': page.excerpt,
                'content': page.content[:200] + '...' if len(page.content) > 200 else page.content,
                'generation_id': page.generation_id,
                'created_at': page.created_at.isoformat() if page.created_at else None,
                'status': page.status,
                'approved_at': page.approved_at.isoformat() if page.approved_at else None,
                'meta': meta,
                'uploaded_to_wp': meta.get('uploaded_to_wp', False),
                'wp_post_id': meta.get('wp_post_id'),
                'word_count': meta.get('word_count', 0),
                'client': generation.client if generation else None,
                'project': generation.project if generation else None,
                'website': generation.website if generation else None,
                'competitor': generation.competitor if generation else None
            })
        
        return jsonify({
            'pages': pages_data,
            'total': total_count,
            'limit': limit,
            'offset': offset
        })
        
    except Exception as e:
        logger.error(f"Error listing pages: {e}")
        return jsonify({'error': 'Failed to list pages'}), 500

@content_mgmt_bp.route('/pages/<page_id>', methods=['GET'])
def get_page(page_id):
    """Get single page details"""
    try:
        page = db.session.query(Page).filter_by(id=page_id).first()
        if not page:
            return jsonify({'error': 'Page not found'}), 404
        
        # Get generation info
        generation = db.session.query(Generation).filter_by(id=page.generation_id).first()
        
        meta = {}
        if page.meta_json:
            try:
                meta = json.loads(page.meta_json)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        
        generation_meta = {}
        if generation and generation.meta_json:
            try:
                generation_meta = json.loads(generation.meta_json)
            except (json.JSONDecodeError, TypeError):
                generation_meta = {}
        
        return jsonify({
            'id': page.id,
            'title': page.title,
            'slug': page.slug,
            'category': page.category,
            'tags': page.tags,
            'excerpt': page.excerpt,
            'content': page.content,
            'generation_id': page.generation_id,
            'created_at': page.created_at.isoformat() if page.created_at else None,
            'status': page.status,
            'approved_at': page.approved_at.isoformat() if page.approved_at else None,
            'meta': meta,
            'uploaded_to_wp': meta.get('uploaded_to_wp', False),
            'wp_post_id': meta.get('wp_post_id'),
            'word_count': meta.get('word_count', 0),
            'client': generation.client if generation else None,
            'project': generation.project if generation else None,
            'website': generation.website if generation else None,
            'competitor': generation.competitor if generation else None,
            'generation': {
                'id': generation.id if generation else None,
                'site_key': generation.site_key if generation else None,
                'created_at': generation.created_at.isoformat() if generation and generation.created_at else None,
                'meta': generation_meta
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting page {page_id}: {e}")
        return jsonify({'error': 'Failed to get page'}), 500

@content_mgmt_bp.route('/pages/<page_id>/mark-uploaded', methods=['POST'])
def mark_uploaded(page_id):
    """Mark page as uploaded to WordPress"""
    try:
        data = request.get_json()
        wp_post_id = data.get('wp_post_id')
        
        if not wp_post_id:
            return jsonify({'error': 'wp_post_id is required'}), 400
        
        page = db.session.query(Page).filter_by(id=page_id).first()
        if not page:
            return jsonify({'error': 'Page not found'}), 404
        
        # Update meta_json
        meta = {}
        if page.meta_json:
            try:
                meta = json.loads(page.meta_json)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        
        meta['uploaded_to_wp'] = True
        meta['wp_post_id'] = wp_post_id
        meta['uploaded_at'] = datetime.now().isoformat()
        page.meta_json = json.dumps(meta)
        
        db.session.commit()
        
        logger.info(f"Marked page {page_id} as uploaded (WP Post ID: {wp_post_id})")
        
        return jsonify({
            'success': True,
            'page': {
                'id': page.id,
                'title': page.title,
                'uploaded_to_wp': True,
                'wp_post_id': wp_post_id
            }
        })
        
    except Exception as e:
        logger.error(f"Error marking page {page_id} as uploaded: {e}")
        db.session.rollback()
        return jsonify({'error': 'Failed to mark page as uploaded'}), 500

@content_mgmt_bp.route('/pages/<page_id>/mark-not-uploaded', methods=['POST'])
def mark_not_uploaded(page_id):
    """Mark page as not uploaded to WordPress"""
    try:
        page = db.session.query(Page).filter_by(id=page_id).first()
        if not page:
            return jsonify({'error': 'Page not found'}), 404
        
        # Update meta_json
        meta = {}
        if page.meta_json:
            try:
                meta = json.loads(page.meta_json)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        
        meta['uploaded_to_wp'] = False
        meta.pop('wp_post_id', None)
        meta.pop('uploaded_at', None)
        page.meta_json = json.dumps(meta)
        
        db.session.commit()
        
        logger.info(f"Marked page {page_id} as not uploaded")
        
        return jsonify({
            'success': True,
            'page': {
                'id': page.id,
                'title': page.title,
                'uploaded_to_wp': False
            }
        })
        
    except Exception as e:
        logger.error(f"Error marking page {page_id} as not uploaded: {e}")
        db.session.rollback()
        return jsonify({'error': 'Failed to mark page as not uploaded'}), 500

@content_mgmt_bp.route('/generations', methods=['GET'])
def list_generations():
    """List all content generation batches"""
    try:
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        generations = db.session.query(Generation).order_by(Generation.created_at.desc()).offset(offset).limit(limit).all()
        
        generations_data = []
        for gen in generations:
            meta = {}
            if gen.meta_json:
                try:
                    meta = json.loads(gen.meta_json)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            
            # Get page count
            page_count = db.session.query(Page).filter_by(generation_id=gen.id).count()
            
            generations_data.append({
                'id': gen.id,
                'site_key': gen.site_key,
                'delimiter': gen.delimiter,
                'structured_html': gen.structured_html,
                'brand_tone': gen.brand_tone,
                'created_at': gen.created_at.isoformat() if gen.created_at else None,
                'meta': meta,
                'page_count': page_count
            })
        
        return jsonify({
            'generations': generations_data,
            'total': db.session.query(Generation).count()
        })
        
    except Exception as e:
        logger.error(f"Error listing generations: {e}")
        return jsonify({'error': 'Failed to list generations'}), 500

@content_mgmt_bp.route('/check-duplicates', methods=['POST'])
def check_duplicates():
    """Check if content would create duplicates"""
    try:
        data = request.get_json()
        page_ids = data.get('page_ids', [])
        slugs = data.get('slugs', [])
        website = data.get('website')
        
        if not website:
            return jsonify({'error': 'website is required'}), 400
        
        # Check existing pages with these IDs or slugs
        existing_pages = []
        
        if page_ids:
            existing_by_id = db.session.query(Page).join(Generation).filter(
                Page.id.in_(page_ids),
                Generation.site_key.like(f'%{website}%')
            ).all()
            existing_pages.extend(existing_by_id)
        
        if slugs:
            existing_by_slug = db.session.query(Page).join(Generation).filter(
                Page.slug.in_(slugs),
                Generation.site_key.like(f'%{website}%')
            ).all()
            existing_pages.extend(existing_by_slug)
        
        # Remove duplicates
        existing_pages = list(set(existing_pages))
        
        # Convert to dict format
        existing_data = []
        for page in existing_pages:
            meta = {}
            if page.meta_json:
                try:
                    meta = json.loads(page.meta_json)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            
            existing_data.append({
                'id': page.id,
                'title': page.title,
                'slug': page.slug,
                'generation_id': page.generation_id,
                'created_at': page.created_at.isoformat() if page.created_at else None,
                'uploaded_to_wp': meta.get('uploaded_to_wp', False),
                'wp_post_id': meta.get('wp_post_id')
            })
        
        return jsonify({
            'duplicates_found': len(existing_pages) > 0,
            'existing_pages': existing_data,
            'count': len(existing_pages)
        })
        
    except Exception as e:
        logger.error(f"Error checking duplicates: {e}")
        return jsonify({'error': 'Failed to check duplicates'}), 500

@content_mgmt_bp.route('/stats', methods=['GET'])
def get_stats():
    """Get content generation statistics"""
    try:
        website = request.args.get('website')
        
        # Base queries
        pages_query = db.session.query(Page)
        generations_query = db.session.query(Generation)
        
        if website:
            pages_query = pages_query.join(Generation).filter(Generation.site_key.like(f'%{website}%'))
            generations_query = generations_query.filter(Generation.site_key.like(f'%{website}%'))
        
        # Get counts
        total_pages = pages_query.count()
        total_generations = generations_query.count()
        
        # Get uploaded pages count
        uploaded_pages = pages_query.filter(Page.meta_json.like('%"uploaded_to_wp":true%')).count()
        
        # Get recent activity (last 7 days)
        from datetime import timedelta
        week_ago = datetime.now() - timedelta(days=7)
        recent_pages = pages_query.filter(Page.created_at >= week_ago).count()
        recent_generations = generations_query.filter(Generation.created_at >= week_ago).count()
        
        return jsonify({
            'total_pages': total_pages,
            'total_generations': total_generations,
            'uploaded_pages': uploaded_pages,
            'pending_pages': total_pages - uploaded_pages,
            'recent_pages_7d': recent_pages,
            'recent_generations_7d': recent_generations,
            'upload_rate': (uploaded_pages / total_pages * 100) if total_pages > 0 else 0
        })
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return jsonify({'error': 'Failed to get stats'}), 500

@content_mgmt_bp.route('/pages/<page_id>/delete', methods=['DELETE'])
def delete_page(page_id):
    """Delete a page (soft delete by marking as deleted)"""
    try:
        page = db.session.query(Page).filter_by(id=page_id).first()
        if not page:
            return jsonify({'error': 'Page not found'}), 404

        # Update status to deleted
        page.status = 'deleted'
        page.deleted_at = datetime.now()

        db.session.commit()

        logger.info(f"Soft deleted page {page_id}")

        return jsonify({
            'success': True,
            'message': f'Page {page_id} marked as deleted'
        })

    except Exception as e:
        logger.error(f"Error deleting page {page_id}: {e}")
        db.session.rollback()
        return jsonify({'error': 'Failed to delete page'}), 500

@content_mgmt_bp.route('/pages/<page_id>/approve', methods=['POST'])
def approve_page(page_id):
    """Approve a page for use"""
    try:
        page = db.session.query(Page).filter_by(id=page_id).first()
        if not page:
            return jsonify({'error': 'Page not found'}), 404

        # Update status to approved
        page.status = 'approved'
        page.approved_at = datetime.now()

        db.session.commit()

        logger.info(f"Approved page {page_id}")

        return jsonify({
            'success': True,
            'message': f'Page {page_id} approved',
            'page': {
                'id': page.id,
                'title': page.title,
                'status': page.status,
                'approved_at': page.approved_at.isoformat() if page.approved_at else None
            }
        })

    except Exception as e:
        logger.error(f"Error approving page {page_id}: {e}")
        db.session.rollback()
        return jsonify({'error': 'Failed to approve page'}), 500
