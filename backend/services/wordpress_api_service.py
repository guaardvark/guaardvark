# backend/services/wordpress_api_service.py
"""
WordPress REST API Client Service
Handles all communication with WordPress sites via REST API
"""

import json
import logging
import requests
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


class WordPressAPIService:
    """Service for interacting with WordPress REST API"""
    
    def __init__(self, site_url: str, username: str, api_key: str, timeout: int = 30):
        """
        Initialize WordPress API client
        
        Args:
            site_url: WordPress site URL (e.g., https://example.com)
            username: WordPress username
            api_key: WordPress Application Password
            timeout: Request timeout in seconds
        """
        self.site_url = site_url.rstrip('/')
        self.username = username
        self.api_key = api_key
        self.timeout = timeout
        self.base_api_url = f"{self.site_url}/wp-json/wp/v2"
        
        # Prepare authentication
        import base64
        credentials = f"{username}:{api_key}"
        token = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    def test_connection(self) -> Tuple[bool, Optional[str]]:
        """
        Test WordPress API connection
        
        Returns:
            Tuple of (success, error_message)
        """
        try:
            response = requests.get(
                f"{self.base_api_url}/",
                headers=self.headers,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return True, None
            elif response.status_code == 401:
                return False, "Authentication failed - check username and API key"
            else:
                return False, f"Connection failed with status {response.status_code}: {response.text[:200]}"
        except requests.exceptions.RequestException as e:
            return False, f"Connection error: {str(e)}"
    
    def get_posts(self, post_type: str = "post", per_page: int = 100, page: int = 1, 
                  status: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Get posts/pages from WordPress
        
        Args:
            post_type: post, page, or custom post type
            per_page: Number of items per page
            page: Page number
            status: Filter by status (publish, draft, etc.)
            **kwargs: Additional query parameters
        
        Returns:
            Dict with posts data and pagination info
        """
        try:
            params = {
                "per_page": per_page,
                "page": page,
                **kwargs
            }
            
            if status:
                params["status"] = status
            
            url = f"{self.base_api_url}/{post_type}s"
            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                posts = response.json()
                total_pages = int(response.headers.get('X-WP-TotalPages', 1))
                total_items = int(response.headers.get('X-WP-Total', len(posts)))
                
                return {
                    "success": True,
                    "posts": posts,
                    "pagination": {
                        "page": page,
                        "per_page": per_page,
                        "total_pages": total_pages,
                        "total_items": total_items
                    }
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to fetch posts: {response.status_code} - {response.text[:200]}"
                }
        except Exception as e:
            logger.error(f"Error fetching posts: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_post(self, post_id: int, post_type: str = "post") -> Dict[str, Any]:
        """
        Get a single post/page by ID
        
        Args:
            post_id: WordPress post ID
            post_type: post, page, or custom post type
        
        Returns:
            Dict with post data or error
        """
        try:
            url = f"{self.base_api_url}/{post_type}s/{post_id}"
            response = requests.get(
                url,
                headers=self.headers,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "post": response.json()
                }
            elif response.status_code == 404:
                return {
                    "success": False,
                    "error": f"Post {post_id} not found"
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to fetch post: {response.status_code} - {response.text[:200]}"
                }
        except Exception as e:
            logger.error(f"Error fetching post {post_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_categories(self, per_page: int = 100) -> Dict[str, Any]:
        """Get all categories"""
        try:
            url = f"{self.base_api_url}/categories"
            response = requests.get(
                url,
                headers=self.headers,
                params={"per_page": per_page},
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "categories": response.json()
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to fetch categories: {response.status_code}"
                }
        except Exception as e:
            logger.error(f"Error fetching categories: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_tags(self, per_page: int = 100) -> Dict[str, Any]:
        """Get all tags"""
        try:
            url = f"{self.base_api_url}/tags"
            response = requests.get(
                url,
                headers=self.headers,
                params={"per_page": per_page},
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "tags": response.json()
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to fetch tags: {response.status_code}"
                }
        except Exception as e:
            logger.error(f"Error fetching tags: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_media(self, media_id: int) -> Dict[str, Any]:
        """Get media item by ID"""
        try:
            url = f"{self.base_api_url}/media/{media_id}"
            response = requests.get(
                url,
                headers=self.headers,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "media": response.json()
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to fetch media: {response.status_code}"
                }
        except Exception as e:
            logger.error(f"Error fetching media {media_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_sitemap(self) -> Optional[List[str]]:
        """
        Attempt to get sitemap URLs from WordPress
        Tries common sitemap locations
        
        Returns:
            List of URLs from sitemap or None if not found
        """
        sitemap_urls = [
            f"{self.site_url}/sitemap.xml",
            f"{self.site_url}/wp-sitemap.xml",
            f"{self.site_url}/sitemap_index.xml",
        ]
        
        for sitemap_url in sitemap_urls:
            try:
                response = requests.get(sitemap_url, timeout=self.timeout)
                if response.status_code == 200:
                    # Parse XML sitemap (basic parsing)
                    import xml.etree.ElementTree as ET
                    try:
                        root = ET.fromstring(response.content)
                        urls = []
                        # Handle sitemap index
                        for sitemap in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap'):
                            loc = sitemap.find('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                            if loc is not None:
                                urls.append(loc.text)
                        # Handle URL set
                        for url_elem in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}url'):
                            loc = url_elem.find('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                            if loc is not None:
                                urls.append(loc.text)
                        return urls if urls else None
                    except ET.ParseError:
                        logger.warning(f"Failed to parse sitemap XML from {sitemap_url}")
                        continue
            except Exception as e:
                logger.debug(f"Error checking sitemap {sitemap_url}: {e}")
                continue
        
        return None
    
    def update_post(self, post_id: int, post_type: str = "post", **data) -> Dict[str, Any]:
        """
        Update a WordPress post/page
        
        Args:
            post_id: WordPress post ID
            post_type: post, page, or custom post type
            **data: Post data to update (title, content, excerpt, etc.)
        
        Returns:
            Dict with updated post data or error
        """
        try:
            url = f"{self.base_api_url}/{post_type}s/{post_id}"
            response = requests.post(
                url,
                headers=self.headers,
                json=data,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "post": response.json()
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to update post: {response.status_code} - {response.text[:200]}"
                }
        except Exception as e:
            logger.error(f"Error updating post {post_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def update_post_meta(self, post_id: int, meta_key: str, meta_value: Any, post_type: str = "post") -> Dict[str, Any]:
        """
        Update post meta (custom fields, SEO data, etc.)
        
        Args:
            post_id: WordPress post ID
            meta_key: Meta key name
            meta_value: Meta value
            post_type: post, page, or custom post type
        
        Returns:
            Dict with success status
        """
        try:
            # WordPress REST API doesn't have direct meta endpoint, so we use update_post
            # This requires the meta to be registered in the REST API
            url = f"{self.base_api_url}/{post_type}s/{post_id}"
            
            # For custom meta, we might need to use a custom endpoint or plugin
            # For now, we'll try to update via the standard endpoint
            data = {"meta": {meta_key: meta_value}}
            
            response = requests.post(
                url,
                headers=self.headers,
                json=data,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "message": f"Meta {meta_key} updated successfully"
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to update meta: {response.status_code} - {response.text[:200]}"
                }
        except Exception as e:
            logger.error(f"Error updating post meta {meta_key}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    @staticmethod
    def calculate_content_hash(content: str) -> str:
        """Calculate SHA256 hash of content for change detection"""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

