# backend/services/wordpress_content_puller.py
"""
WordPress Content Puller Service
Orchestrates pulling content from WordPress sites with progress tracking
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

from backend.models import WordPressSite, WordPressPage, db
from backend.services.wordpress_api_service import WordPressAPIService
from backend.services.llamanator_api_service import LlamanatorAPIService
from backend.utils.unified_progress_system import get_unified_progress, ProcessType, ProcessStatus

logger = logging.getLogger(__name__)


class WordPressContentPuller:
    """Service for pulling content from WordPress sites"""
    
    def __init__(self, site_id: int, job_id: Optional[str] = None):
        """
        Initialize content puller for a WordPress site
        
        Args:
            site_id: WordPressSite ID
            job_id: Optional job ID for progress tracking
        """
        self.site_id = site_id
        self.wp_site = db.session.get(WordPressSite, site_id)
        
        if not self.wp_site:
            raise ValueError(f"WordPress site {site_id} not found")
        
        # Initialize appropriate API service based on connection type
        connection_type = getattr(self.wp_site, 'connection_type', 'llamanator') or 'llamanator'
        
        if connection_type == 'llamanator':
            # Use LLAMANATOR2 plugin API
            self.wp_service = LlamanatorAPIService(
                site_url=self.wp_site.url,
                api_key=self.wp_site.api_key
            )
        else:
            # Use direct WordPress REST API (legacy)
            if not self.wp_site.username:
                raise ValueError("Username is required for direct WordPress REST API connection")
            self.wp_service = WordPressAPIService(
                site_url=self.wp_site.url,
                username=self.wp_site.username,
                api_key=self.wp_site.api_key
            )
        
        self.job_id = job_id or str(uuid.uuid4())
        self.progress_system = get_unified_progress()
        
        # Parse pull settings
        self.pull_settings = {}
        if self.wp_site.pull_settings:
            try:
                self.pull_settings = json.loads(self.wp_site.pull_settings)
            except (json.JSONDecodeError, TypeError):
                self.pull_settings = {}
    
    def _update_progress(self, progress: int, message: str, additional_data: Optional[Dict] = None):
        """Update progress tracking"""
        try:
            self.progress_system.update_process(
                self.job_id,
                progress,
                message,
                {
                    "site_id": self.site_id,
                    "site_url": self.wp_site.url,
                    **(additional_data or {})
                }
            )
        except Exception as e:
            logger.warning(f"Failed to update progress: {e}")
    
    def _get_pull_filters(self) -> Dict[str, Any]:
        """Get pull filters from settings"""
        filters = {}
        
        # Post type filter
        if "post_types" in self.pull_settings:
            # This will be handled in pull_page_list
            pass
        
        # Status filter
        if "status" in self.pull_settings:
            filters["status"] = self.pull_settings["status"]
        
        # Date range filter
        if "date_from" in self.pull_settings:
            filters["after"] = self.pull_settings["date_from"]
        if "date_to" in self.pull_settings:
            filters["before"] = self.pull_settings["date_to"]
        
        # Author filter
        if "author_ids" in self.pull_settings:
            # WordPress API uses single author, so we'd need to handle multiple
            pass
        
        # Category filter
        if "category_ids" in self.pull_settings:
            filters["categories"] = self.pull_settings["category_ids"]
        
        # Tag filter
        if "tag_ids" in self.pull_settings:
            filters["tags"] = self.pull_settings["tag_ids"]
        
        return filters
    
    def pull_page_list(self, post_type: str = "post", per_page: int = 100, 
                      max_pages: Optional[int] = None, filters: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Pull list of pages/posts from WordPress with progress tracking
        
        Args:
            post_type: post, page, or custom post type
            per_page: Number of items per page
            max_pages: Maximum number of pages to pull (None = all)
            filters: Additional filters to apply
        
        Returns:
            Dict with pull results
        """
        try:
            # Initialize progress tracking
            self.progress_system.create_process(
                ProcessType.WORDPRESS_PULL,
                f"Pulling {post_type}s from {self.wp_site.url}",
                {"site_id": self.site_id, "site_url": self.wp_site.url},
                self.job_id
            )
            self._update_progress(0, f"Starting to pull {post_type}s...")
            
            # Merge filters
            pull_filters = self._get_pull_filters()
            if filters:
                pull_filters.update(filters)
            
            all_posts = []
            page = 1
            total_pages = None
            total_items = None
            
            while True:
                result = self.wp_service.get_posts(
                    post_type=post_type,
                    per_page=per_page,
                    page=page,
                    **pull_filters
                )
                
                if not result["success"]:
                    self.progress_system.error_process(
                        self.job_id,
                        result.get("error", "Unknown error")
                    )
                    return {
                        "success": False,
                        "error": result.get("error", "Unknown error"),
                        "posts_pulled": len(all_posts),
                        "job_id": self.job_id
                    }
                
                # Handle different response formats from WordPressAPIService vs LlamanatorAPIService
                # WordPressAPIService returns: {"success": True, "posts": [...], "pagination": {...}}
                # LlamanatorAPIService returns: {"success": True, "data": [...], "total": ..., "pages": ...}
                if "posts" in result:
                    # WordPress REST API format
                    posts = result["posts"]
                    pagination = result.get("pagination", {})
                    if total_pages is None:
                        total_pages = pagination.get("total_pages", 1)
                        total_items = pagination.get("total_items", len(posts))
                elif "data" in result:
                    # LlamanatorAPI format
                    posts = result["data"]
                    if total_pages is None:
                        total_pages = result.get("pages", 1)
                        total_items = result.get("total", len(posts))
                else:
                    # Fallback: try to find posts in result
                    posts = result.get("posts", result.get("data", []))
                    if total_pages is None:
                        total_pages = result.get("pages", result.get("pagination", {}).get("total_pages", 1))
                        total_items = result.get("total", result.get("pagination", {}).get("total_items", len(posts)))
                
                all_posts.extend(posts)
                
                # Calculate progress
                if total_pages > 0:
                    progress = min(95, int((page / total_pages) * 90))  # Leave 10% for final processing
                else:
                    progress = 90
                
                self._update_progress(
                    progress,
                    f"Pulled page {page}/{total_pages} ({len(all_posts)}/{total_items or len(all_posts)} posts)",
                    {"current_page": page, "total_pages": total_pages, "posts_pulled": len(all_posts)}
                )
                
                logger.info(f"Pulled page {page}/{total_pages} ({len(posts)} posts)")
                
                if page >= total_pages:
                    break
                
                if max_pages and page >= max_pages:
                    break
                
                page += 1
            
            self._update_progress(95, f"Successfully pulled {len(all_posts)} posts, finalizing...")
            
            # Save all posts to database
            saved_count = 0
            updated_count = 0
            skipped_count = 0
            
            logger.info(f"Starting to save {len(all_posts)} posts to database...")
            
            for idx, post in enumerate(all_posts):
                try:
                    post_id = post.get("id")
                    if not post_id:
                        logger.warning(f"Post at index {idx} has no ID, skipping")
                        skipped_count += 1
                        continue
                    
                    # Extract post data
                    content = post.get("content", {}).get("rendered", "") if isinstance(post.get("content"), dict) else post.get("content", "")
                    title = post.get("title", {}).get("rendered", "") if isinstance(post.get("title"), dict) else post.get("title", "")
                    excerpt = post.get("excerpt", {}).get("rendered", "") if isinstance(post.get("excerpt"), dict) else post.get("excerpt", "")
                    
                    # Ensure title is not empty (required field)
                    if not title:
                        logger.warning(f"Post {post_id} has no title, skipping")
                        skipped_count += 1
                        continue
                    
                    # Calculate content hash
                    content_hash = WordPressAPIService.calculate_content_hash(content)
                    
                    # Parse dates
                    date = None
                    modified = None
                    if post.get("date"):
                        try:
                            date = datetime.fromisoformat(post["date"].replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            pass
                    if post.get("modified"):
                        try:
                            modified = datetime.fromisoformat(post["modified"].replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            pass
                    
                    # Extract categories and tags
                    categories = post.get("categories", [])
                    tags = post.get("tags", [])
                    
                    # Extract author
                    author_id = post.get("author")
                    author_name = f"Author {author_id}" if author_id else None
                    
                    # Check if page already exists
                    existing_page = db.session.query(WordPressPage).filter_by(
                        wordpress_site_id=self.site_id,
                        wordpress_post_id=post_id
                    ).first()
                    
                    if existing_page:
                        # Update existing page
                        existing_page.title = title
                        existing_page.content = content
                        existing_page.excerpt = excerpt
                        existing_page.slug = post.get("slug")
                        existing_page.status = post.get("status", "publish")
                        existing_page.date = date
                        existing_page.modified = modified
                        existing_page.author_id = author_id
                        existing_page.author_name = author_name
                        existing_page.categories = json.dumps(categories if isinstance(categories, list) else [])
                        existing_page.tags = json.dumps(tags if isinstance(tags, list) else [])
                        existing_page.featured_image_id = post.get("featured_media")
                        existing_page.original_content_hash = content_hash
                        existing_page.pull_status = "pulled"
                        existing_page.pulled_at = datetime.now()
                        existing_page.updated_at = datetime.now()
                        
                        # Extract and store SEO data
                        self._extract_and_store_seo_data(existing_page, post)
                        
                        updated_count += 1
                    else:
                        # Create new page
                        wp_page = WordPressPage(
                            wordpress_site_id=self.site_id,
                            wordpress_post_id=post_id,
                            post_type=post_type,
                            title=title,
                            content=content,
                            excerpt=excerpt,
                            slug=post.get("slug"),
                            status=post.get("status", "publish"),
                            date=date,
                            modified=modified,
                            author_id=author_id,
                            author_name=author_name,
                            categories=json.dumps(categories if isinstance(categories, list) else []),
                            tags=json.dumps(tags if isinstance(tags, list) else []),
                            featured_image_id=post.get("featured_media"),
                            meta_data=json.dumps({}),
                            original_content_hash=content_hash,
                            pull_status="pulled",
                            pulled_at=datetime.now()
                        )
                        db.session.add(wp_page)
                        
                        # Extract and store SEO data
                        self._extract_and_store_seo_data(wp_page, post)
                        
                        saved_count += 1
                    
                    # Update progress every 100 posts
                    if (idx + 1) % 100 == 0:
                        progress = 95 + int((idx + 1) / len(all_posts) * 4)  # 95-99% for saving
                        self._update_progress(
                            progress,
                            f"Saving posts to database... ({idx + 1}/{len(all_posts)})",
                            {"saved": saved_count, "updated": updated_count, "skipped": skipped_count}
                        )
                        # Commit periodically to avoid large transaction
                        try:
                            db.session.commit()
                            logger.info(f"Periodic commit successful: saved {saved_count}, updated {updated_count} so far")
                        except Exception as e:
                            logger.error(f"Periodic commit failed at post {idx + 1}: {e}", exc_info=True)
                            db.session.rollback()
                            # Continue anyway, will try final commit
                        
                except Exception as e:
                    logger.error(f"Error saving post {post.get('id')}: {e}", exc_info=True)
                    skipped_count += 1
                    continue
            
            # Final commit for any remaining changes
            self.wp_site.last_pull_at = datetime.now()
            try:
                db.session.commit()
                logger.info(f"Final database commit successful: Saved {saved_count} new posts and updated {updated_count} existing posts. Skipped {skipped_count}.")
            except Exception as commit_error:
                db.session.rollback()
                logger.error(f"Final database commit failed: {commit_error}", exc_info=True)
                self.progress_system.error_process(self.job_id, f"Database commit failed: {str(commit_error)}")
                return {
                    "success": False,
                    "error": f"Failed to save posts to database: {str(commit_error)}",
                    "saved_count": saved_count,
                    "updated_count": updated_count,
                    "skipped_count": skipped_count,
                    "job_id": self.job_id
                }
            
            logger.info(f"Successfully saved {saved_count} new posts and updated {updated_count} existing posts. Skipped {skipped_count}.")
            
            result_data = {
                "success": True,
                "posts": all_posts,
                "total_pulled": len(all_posts),
                "saved_count": saved_count,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "post_type": post_type,
                "job_id": self.job_id
            }
            
            self.progress_system.complete_process(
                self.job_id,
                f"Successfully pulled and saved {len(all_posts)} {post_type}s ({saved_count} new, {updated_count} updated)"
            )
            
            return result_data
            
        except Exception as e:
            logger.error(f"Error pulling page list: {e}", exc_info=True)
            self.progress_system.error_process(self.job_id, str(e))
            return {
                "success": False,
                "error": str(e),
                "job_id": self.job_id
            }
    
    def pull_single_page(self, post_id: int, post_type: str = "post", 
                        update_existing: bool = True) -> Dict[str, Any]:
        """
        Pull a single page/post from WordPress
        
        Args:
            post_id: WordPress post ID
            post_type: post, page, or custom post type
            update_existing: Whether to update if page already exists
        
        Returns:
            Dict with pull results
        """
        try:
            # Get post data
            result = self.wp_service.get_post(post_id, post_type=post_type)
            if not result["success"]:
                return result
            
            # Handle different response formats
            # WordPressAPIService returns: {"success": True, "post": {...}}
            # LlamanatorAPIService returns: {"success": True, "data": {...}}
            post = result.get("post") or result.get("data")
            
            if not post:
                return {
                    "success": False,
                    "error": "Post data not found in response"
                }
            
            # Check if page already exists
            existing_page = db.session.query(WordPressPage).filter_by(
                wordpress_site_id=self.site_id,
                wordpress_post_id=post_id
            ).first()
            
            if existing_page and not update_existing:
                return {
                    "success": True,
                    "data": existing_page.to_dict(),
                    "message": "Page already exists, skipping update"
                }
            
            # Extract categories and tags
            categories = []
            tags = []
            
            if post.get("categories"):
                cat_result = self.wp_service.get_categories()
                if cat_result["success"]:
                    # Handle different response formats
                    # WordPressAPIService returns: {"success": True, "categories": [...]}
                    # LlamanatorAPIService returns: {"success": True, "data": [...]}
                    cat_list = cat_result.get("categories") or cat_result.get("data", [])
                    cat_map = {cat["id"]: cat["name"] for cat in cat_list}
                    categories = [cat_map.get(cid, "") for cid in post.get("categories", [])]
            
            if post.get("tags"):
                tag_result = self.wp_service.get_tags()
                if tag_result["success"]:
                    # Handle different response formats
                    tag_list = tag_result.get("tags") or tag_result.get("data", [])
                    tag_map = {tag["id"]: tag["name"] for tag in tag_list}
                    tags = [tag_map.get(tid, "") for tid in post.get("tags", [])]
            
            # Extract featured image
            featured_image_url = None
            featured_image_id = None
            if post.get("featured_media"):
                media_result = self.wp_service.get_media(post["featured_media"])
                if media_result["success"]:
                    # Handle different response formats
                    # WordPressAPIService returns: {"success": True, "media": {...}}
                    # LlamanatorAPIService may return: {"success": True, "data": {...}}
                    media = media_result.get("media") or media_result.get("data", {})
                    featured_image_url = media.get("source_url")
                    featured_image_id = post["featured_media"]
            
            # Extract meta data (SEO, custom fields)
            meta_data = {}
            # WordPress REST API doesn't expose all meta by default
            # This would need custom endpoints or plugins
            
            # Calculate content hash
            content = post.get("content", {}).get("rendered", "")
            content_hash = WordPressAPIService.calculate_content_hash(content)
            
            # Parse dates
            date = None
            modified = None
            if post.get("date"):
                try:
                    date = datetime.fromisoformat(post["date"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            if post.get("modified"):
                try:
                    modified = datetime.fromisoformat(post["modified"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            
            # Extract author name if possible
            author_name = None
            if post.get("author"):
                author_name = f"Author {post['author']}"  # Would need user endpoint for full name
            
            if existing_page:
                # Update existing page
                existing_page.title = post.get("title", {}).get("rendered", "")
                existing_page.content = content
                existing_page.excerpt = post.get("excerpt", {}).get("rendered", "")
                existing_page.slug = post.get("slug")
                existing_page.status = post.get("status", "publish")
                existing_page.date = date
                existing_page.modified = modified
                existing_page.author_id = post.get("author")
                existing_page.author_name = author_name
                existing_page.categories = json.dumps(categories)
                existing_page.tags = json.dumps(tags)
                existing_page.featured_image_url = featured_image_url
                existing_page.featured_image_id = featured_image_id
                existing_page.meta_data = json.dumps(meta_data)
                existing_page.original_content_hash = content_hash
                existing_page.pull_status = "pulled"
                existing_page.pulled_at = datetime.now()
                existing_page.updated_at = datetime.now()
                
                # Extract and store SEO data
                self._extract_and_store_seo_data(existing_page, post)
                
                wp_page = existing_page
                action = "updated"
            else:
                # Create new page
                wp_page = WordPressPage(
                    wordpress_site_id=self.site_id,
                    wordpress_post_id=post_id,
                    post_type=post_type,
                    title=post.get("title", {}).get("rendered", ""),
                    content=content,
                    excerpt=post.get("excerpt", {}).get("rendered", ""),
                    slug=post.get("slug"),
                    status=post.get("status", "publish"),
                    date=date,
                    modified=modified,
                    author_id=post.get("author"),
                    author_name=author_name,
                    categories=json.dumps(categories),
                    tags=json.dumps(tags),
                    featured_image_url=featured_image_url,
                    featured_image_id=featured_image_id,
                    meta_data=json.dumps(meta_data),
                    original_content_hash=content_hash,
                    pull_status="pulled",
                    pulled_at=datetime.now()
                )
                db.session.add(wp_page)
                
                # Extract and store SEO data
                self._extract_and_store_seo_data(wp_page, post)
                
                action = "created"
            
            self.wp_site.last_pull_at = datetime.now()
            db.session.commit()
            
            logger.info(f"{action.capitalize()} WordPress page: {post_id} from site {self.site_id} (Guaardvark ID: {wp_page.id})")
            
            return {
                "success": True,
                "data": wp_page.to_dict(),
                "action": action
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error pulling WordPress page {post_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def pull_bulk_pages(self, post_ids: List[int], post_type: str = "post") -> Dict[str, Any]:
        """
        Pull multiple pages from WordPress with progress tracking
        
        Args:
            post_ids: List of WordPress post IDs
            post_type: post, page, or custom post type
        
        Returns:
            Dict with pull results
        """
        try:
            # Initialize progress tracking
            self.progress_system.create_process(
                ProcessType.WORDPRESS_PULL,
                f"Pulling {len(post_ids)} {post_type}s from {self.wp_site.url}"
            )
            
            results = {
                "success": True,
                "total": len(post_ids),
                "succeeded": 0,
                "failed": 0,
                "errors": [],
                "job_id": self.job_id
            }
            
            if not post_ids:
                self.progress_system.complete_process(self.job_id, "No posts to pull")
                return results
            
            for idx, post_id in enumerate(post_ids):
                progress = int((idx / len(post_ids)) * 90)
                self._update_progress(
                    progress,
                    f"Pulling {post_type} {post_id} ({idx + 1}/{len(post_ids)})",
                    {"current_post": post_id, "completed": idx}
                )
                
                result = self.pull_single_page(post_id, post_type=post_type)
                if result["success"]:
                    results["succeeded"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append({
                        "post_id": post_id,
                        "error": result.get("error", "Unknown error")
                    })
            
            results["success"] = results["failed"] == 0
            
            status_message = f"Pulled {results['succeeded']}/{results['total']} {post_type}s successfully"
            if results["failed"] > 0:
                status_message += f" ({results['failed']} failed)"
            
            self.progress_system.complete_process(self.job_id, status_message)
            
            return results
            
        except Exception as e:
            logger.error(f"Error in bulk pull: {e}", exc_info=True)
            self.progress_system.error_process(self.job_id, str(e))
            return {
                "success": False,
                "error": str(e),
                "job_id": self.job_id,
                "total": len(post_ids) if post_ids else 0,
                "succeeded": 0,
                "failed": 0,
                "errors": []
            }
    
    def pull_from_sitemap(self, extract_post_ids: bool = False) -> Dict[str, Any]:
        """
        Pull and parse WordPress sitemap with enhanced parsing
        
        Args:
            extract_post_ids: Whether to attempt extracting post IDs from URLs
        
        Returns:
            Dict with pull results
        """
        try:
            self._update_progress(0, "Fetching sitemap...")
            
            urls = self.wp_service.get_sitemap()
            
            if urls is None:
                return {
                    "success": False,
                    "error": "Could not find or parse sitemap"
                }
            
            self._update_progress(50, f"Found {len(urls)} URLs in sitemap")
            
            result = {
                "success": True,
                "urls": urls,
                "count": len(urls),
                "sitemap_urls": [
                    f"{self.wp_site.url}/sitemap.xml",
                    f"{self.wp_site.url}/wp-sitemap.xml",
                ]
            }
            
            # Enhanced parsing: Extract post IDs from URLs if possible
            if extract_post_ids:
                post_ids = []
                for url in urls:
                    # Try to extract post ID from URL patterns like:
                    # /2024/01/post-slug/
                    # /post-slug/
                    # ?p=123
                    # Try WordPress REST API endpoint matching
                    if "wp-json" in url or "wp/v2" in url:
                        continue
                    
                    # Try to match URL to post ID via REST API
                    # This would require additional API calls, so we'll skip for now
                    pass
                
                result["post_ids"] = post_ids
                result["extracted_post_ids"] = len(post_ids) > 0
            
            self._update_progress(100, f"Successfully parsed sitemap with {len(urls)} URLs")
            
            return result
            
        except Exception as e:
            logger.error(f"Error pulling from sitemap: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def _extract_and_store_seo_data(self, wp_page: WordPressPage, post_data: Dict[str, Any]) -> None:
        """
        Extract SEO data from post data and store in WordPressPage model
        
        Args:
            wp_page: WordPressPage instance
            post_data: Post data from API
        """
        # Only extract SEO data if using LLAMANATOR2 connection
        if not isinstance(self.wp_service, LlamanatorAPIService):
            return
        
        # Get SEO data from transformed post data
        seo_data = post_data.get("_seo_data") or {}
        
        if not seo_data:
            logger.debug(f"No SEO data found for post {wp_page.wordpress_post_id}")
            return
        
        try:
            # Store core SEO fields
            wp_page.seo_title = seo_data.get("title")
            wp_page.seo_description = seo_data.get("description")
            wp_page.canonical_url = seo_data.get("canonical_url")
            wp_page.seo_plugin = seo_data.get("seo_plugin")
            
            # Store focus keywords as JSON array
            focus_keywords = seo_data.get("focus_keywords", [])
            if isinstance(focus_keywords, str):
                # Handle comma-separated string
                focus_keywords = [k.strip() for k in focus_keywords.split(",") if k.strip()]
            wp_page.focus_keywords = json.dumps(focus_keywords) if focus_keywords else None
            
            # Store robots meta as JSON array
            robots_meta = seo_data.get("robots", [])
            if isinstance(robots_meta, str):
                robots_meta = [r.strip() for r in robots_meta.split(",") if r.strip()]
            wp_page.robots_meta = json.dumps(robots_meta) if robots_meta else None
            
            # Store schema markup as JSON
            schema_markup = seo_data.get("schema_markup", [])
            if schema_markup:
                wp_page.schema_markup = json.dumps(schema_markup)
            
            # Store image SEO data
            image_seo_data = seo_data.get("image_seo_data", [])
            if image_seo_data:
                wp_page.image_seo_data = json.dumps(image_seo_data)
            
            # Fetch and store SEO analysis if available (only for LLAMANATOR2)
            try:
                seo_analysis = self.wp_service.get_seo_analysis(wp_page.wordpress_post_id)
                if seo_analysis.get("success"):
                    analysis_data = seo_analysis.get("data", {})
                    wp_page.seo_score = analysis_data.get("seo_score")
                    wp_page.page_score = analysis_data.get("page_score")
            except Exception as e:
                logger.debug(f"Failed to fetch SEO analysis for post {wp_page.wordpress_post_id}: {e}")
            
            # Fetch and store analytics data
            try:
                analytics = self.wp_service.get_seo_analytics(wp_page.wordpress_post_id, days=30)
                if analytics.get("success"):
                    wp_page.analytics_data = json.dumps(analytics.get("data", {}))
                    wp_page.analytics_synced_at = datetime.now()
            except Exception as e:
                logger.debug(f"Failed to fetch analytics for post {wp_page.wordpress_post_id}: {e}")
            
            # Fetch and store PageSpeed data
            try:
                pagespeed = self.wp_service.get_pagespeed_data(wp_page.wordpress_post_id)
                if pagespeed.get("success"):
                    pagespeed_data = pagespeed.get("data", {})
                    wp_page.pagespeed_score_mobile = pagespeed_data.get("mobile", {}).get("score")
                    wp_page.pagespeed_score_desktop = pagespeed_data.get("desktop", {}).get("score")
                    wp_page.pagespeed_data = json.dumps(pagespeed_data)
                    wp_page.pagespeed_synced_at = datetime.now()
            except Exception as e:
                logger.debug(f"Failed to fetch PageSpeed for post {wp_page.wordpress_post_id}: {e}")
                
        except Exception as e:
            logger.warning(f"Error extracting SEO data for post {wp_page.wordpress_post_id}: {e}", exc_info=True)
    
    def get_pull_status(self) -> Dict[str, Any]:
        """
        Get status of pull operations for this site
        
        Returns:
            Dict with pull statistics
        """
        try:
            total_pages = db.session.query(WordPressPage).filter_by(
                wordpress_site_id=self.site_id
            ).count()
            
            pulled_pages = db.session.query(WordPressPage).filter_by(
                wordpress_site_id=self.site_id,
                pull_status="pulled"
            ).count()
            
            error_pages = db.session.query(WordPressPage).filter_by(
                wordpress_site_id=self.site_id,
                pull_status="error"
            ).count()
            
            pending_pages = db.session.query(WordPressPage).filter_by(
                wordpress_site_id=self.site_id,
                pull_status="pending"
            ).count()
            
            return {
                "success": True,
                "site_id": self.site_id,
                "site_url": self.wp_site.url,
                "total_pages": total_pages,
                "pulled": pulled_pages,
                "pending": pending_pages,
                "errors": error_pages,
                "last_pull_at": self.wp_site.last_pull_at.isoformat() if self.wp_site.last_pull_at else None
            }
        except Exception as e:
            logger.error(f"Error getting pull status: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

