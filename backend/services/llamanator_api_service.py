# backend/services/llamanator_api_service.py
"""
LLAMANATOR2 API Client Service
Handles communication with WordPress sites via LLAMANATOR2 plugin
This avoids direct WordPress REST API exposure and firewall issues
"""

import json
import logging
import requests
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class LlamanatorAPIService:
    """Service for interacting with WordPress sites via LLAMANATOR2 plugin"""
    
    def __init__(self, site_url: str, api_key: str, timeout: int = 30):
        """
        Initialize LLAMANATOR2 API client
        
        Args:
            site_url: WordPress site URL (e.g., https://example.com)
            api_key: LLAMANATOR2 API key (configured in plugin settings)
            timeout: Request timeout in seconds
        """
        self.site_url = site_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.base_api_url = f"{self.site_url}/wp-json/llamanator/v1"
        
        # Prepare authentication headers
        self.headers = {
            "X-LLAMANATOR-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Guaardvark-Llamanator-Client/1.0"
        }
    
    def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, 
                     data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make HTTP request to LLAMANATOR2 API
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (without base URL)
            params: Query parameters
            data: Request body data
        
        Returns:
            Dict with response data
        """
        url = f"{self.base_api_url}/{endpoint.lstrip('/')}"
        
        try:
            if method.upper() == "GET":
                response = requests.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=self.timeout
                )
            elif method.upper() == "POST":
                response = requests.post(
                    url,
                    headers=self.headers,
                    params=params,
                    json=data,
                    timeout=self.timeout
                )
            elif method.upper() == "PUT":
                response = requests.put(
                    url,
                    headers=self.headers,
                    params=params,
                    json=data,
                    timeout=self.timeout
                )
            elif method.upper() == "DELETE":
                response = requests.delete(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=self.timeout
                )
            else:
                return {
                    "success": False,
                    "error": f"Unsupported HTTP method: {method}"
                }
            
            # Parse response
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {"raw": response.text}
            
            # Check for errors
            if response.status_code >= 400:
                # WordPress REST API error format: {"code": "...", "message": "...", "data": {...}}
                error_msg = response_data.get("message", response_data.get("error", "Unknown error"))
                
                # Handle nested error messages
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                
                # WordPress REST API uses "code" field for error type
                error_code = response_data.get("code", "")
                if error_code:
                    # Use the code to provide more context
                    if error_code == "invalid_api_key":
                        error_msg = "Invalid API key"
                    elif error_code == "missing_api_key":
                        error_msg = "API key is required"
                    elif error_code == "api_key_not_configured":
                        error_msg = "API key not configured on WordPress site"
                
                return {
                    "success": False,
                    "error": error_msg,
                    "status_code": response.status_code,
                    "error_code": error_code,
                    "data": response_data
                }
            
            return {
                "success": True,
                "status_code": response.status_code,
                "data": response_data.get("data", response_data)
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return {
                "success": False,
                "error": f"Connection error: {str(e)}"
            }
    
    def test_connection(self) -> Tuple[bool, Optional[str]]:
        """
        Test LLAMANATOR2 API connection
        
        Returns:
            Tuple of (success, error_message)
        """
        # Use GET /status endpoint instead of POST /auth/test for compatibility
        result = self._make_request("GET", "/status")
        
        if result["success"]:
            return True, None
        else:
            error_msg = result.get("error", "Connection test failed")
            error_code = result.get("error_code", "")
            
            # Provide more specific error messages based on error code
            if error_code == "invalid_api_key" or "Invalid API key" in error_msg:
                return False, "Invalid API key. Please check the API key in LLAMANATOR2 plugin settings and ensure it matches exactly."
            elif error_code == "missing_api_key" or "API key is required" in error_msg:
                return False, "API key is required. Please provide the LLAMANATOR2 API key."
            elif error_code == "api_key_not_configured":
                return False, "API key not configured on WordPress site. Please generate one in LLAMANATOR2 plugin settings (Settings → LLAMANATOR2 → Guaardvark Integration)."
            elif "Connection error" in error_msg:
                return False, f"Cannot connect to WordPress site: {error_msg}. Please check the site URL and ensure it's accessible."
            return False, error_msg
    
    def get_site_info(self) -> Dict[str, Any]:
        """Get WordPress site information"""
        return self._make_request("GET", "/site/info")
    
    def get_posts(self, post_type: str = "post", per_page: int = 100, page: int = 1, 
                  status: Optional[str] = None, **filters) -> Dict[str, Any]:
        """
        Get posts/pages from WordPress via LLAMANATOR2
        
        Args:
            post_type: post, page, or custom post type
            per_page: Number of items per page
            page: Page number
            status: Filter by status (publish, draft, etc.)
            **filters: Additional filters (date_from, date_to, categories, tags, etc.)
        
        Returns:
            Dict with posts data and pagination info
        """
        data = {
            "post_type": post_type,
            "per_page": per_page,
            "page": page,
        }
        
        if status:
            data["status"] = status
        
        # Add any additional filters
        for key, value in filters.items():
            if value is not None:
                data[key] = value
        
        result = self._make_request("POST", "/content/list", data=data)
        
        if result["success"]:
            # Transform response to match WordPress REST API format
            posts_data = result["data"]
            posts = posts_data.get("posts", [])
            
            # Transform post format
            transformed_posts = []
            for post in posts:
                transformed_posts.append(self._transform_post_data(post))
            
            return {
                "success": True,
                "data": transformed_posts,
                "total": posts_data.get("total", len(posts)),
                "pages": posts_data.get("pages", 1),
                "current_page": posts_data.get("current_page", page)
            }
        
        return result
    
    def get_post(self, post_id: int, post_type: str = "post") -> Dict[str, Any]:
        """
        Get single post/page
        
        Args:
            post_id: WordPress post ID
            post_type: post, page, or custom post type
        
        Returns:
            Dict with post data
        """
        params = {"post_type": post_type}
        result = self._make_request("GET", f"/content/{post_id}", params=params)
        
        if result["success"]:
            return {
                "success": True,
                "data": self._transform_post_data(result["data"])
            }
        
        return result
    
    def get_bulk_posts(self, post_ids: List[int], post_type: str = "post") -> Dict[str, Any]:
        """
        Get multiple posts by IDs
        
        Args:
            post_ids: List of WordPress post IDs
            post_type: post, page, or custom post type
        
        Returns:
            Dict with posts data
        """
        data = {
            "post_ids": post_ids,
            "post_type": post_type
        }
        
        result = self._make_request("POST", "/content/bulk", data=data)
        
        if result["success"]:
            posts_data = result["data"]
            posts = posts_data.get("posts", [])
            
            transformed_posts = [self._transform_post_data(p) for p in posts]
            
            return {
                "success": True,
                "data": transformed_posts,
                "requested": posts_data.get("requested", len(post_ids)),
                "found": posts_data.get("found", len(posts))
            }
        
        return result
    
    def update_post(self, post_id: int, post_type: str = "post", data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Update post/page
        
        Args:
            post_id: WordPress post ID
            post_type: post, page, or custom post type
            data: Update data (title, content, excerpt, status, meta, categories, tags, etc.)
        
        Returns:
            Dict with updated post data
        """
        if data is None:
            data = {}
        
        data["post_type"] = post_type
        result = self._make_request("POST", f"/content/{post_id}/update", data=data)
        
        if result["success"]:
            return {
                "success": True,
                "data": self._transform_post_data(result["data"]["data"])
            }
        
        return result
    
    def get_categories(self, per_page: int = 100, page: int = 1) -> Dict[str, Any]:
        """Get categories"""
        result = self._make_request("GET", "/taxonomy/categories")
        
        if result["success"]:
            return {
                "success": True,
                "data": result["data"]
            }
        
        return result
    
    def get_tags(self, per_page: int = 100, page: int = 1) -> Dict[str, Any]:
        """Get tags"""
        result = self._make_request("GET", "/taxonomy/tags")
        
        if result["success"]:
            return {
                "success": True,
                "data": result["data"]
            }
        
        return result
    
    def get_sitemap(self) -> Dict[str, Any]:
        """Get sitemap"""
        return self._make_request("GET", "/sitemap")
    
    def get_status(self) -> Dict[str, Any]:
        """Get plugin status"""
        return self._make_request("GET", "/status")
    
    def get_seo_analysis(self, post_id: int) -> Dict[str, Any]:
        """
        Get SEO analysis for a post
        
        Args:
            post_id: WordPress post ID
        
        Returns:
            Dict with SEO score data
        """
        result = self._make_request("GET", f"/seo/analyze/{post_id}")
        
        if result["success"]:
            return {
                "success": True,
                "data": result["data"]
            }
        
        return result
    
    def get_seo_analytics(self, post_id: int, days: int = 30) -> Dict[str, Any]:
        """
        Get SEO analytics data (GSC + GA) for a post
        
        Args:
            post_id: WordPress post ID
            days: Number of days to look back
        
        Returns:
            Dict with analytics data
        """
        params = {"days": days}
        result = self._make_request("GET", f"/seo/analytics/{post_id}", params=params)
        
        if result["success"]:
            return {
                "success": True,
                "data": result["data"]
            }
        
        return result
    
    def get_pagespeed_data(self, post_id: int) -> Dict[str, Any]:
        """
        Get PageSpeed scores for a post
        
        Args:
            post_id: WordPress post ID
        
        Returns:
            Dict with PageSpeed data
        """
        result = self._make_request("GET", f"/seo/pagespeed/{post_id}")
        
        if result["success"]:
            return {
                "success": True,
                "data": result["data"]
            }
        
        return result
    
    def get_bulk_seo_analytics(self, post_ids: List[int], days: int = 30) -> Dict[str, Any]:
        """
        Get SEO analytics for multiple posts
        
        Args:
            post_ids: List of WordPress post IDs
            days: Number of days to look back
        
        Returns:
            Dict with analytics data per post
        """
        data = {
            "post_ids": post_ids,
            "days": days
        }
        
        result = self._make_request("POST", "/seo/bulk-analytics", data=data)
        
        if result["success"]:
            return {
                "success": True,
                "data": result["data"]
            }
        
        return result
    
    def get_llms_txt(self) -> Dict[str, Any]:
        """
        Get llms.txt file content
        
        Returns:
            Dict with llms.txt content and URL
        """
        result = self._make_request("GET", "/seo/llms-txt")
        
        if result["success"]:
            return {
                "success": True,
                "data": result["data"]
            }
        
        return result
    
    def _transform_post_data(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform LLAMANATOR2 post format to WordPress REST API format
        for compatibility with existing code
        """
        transformed = {
            "id": post_data.get("id"),
            "date": post_data.get("date"),
            "date_gmt": post_data.get("date_gmt"),
            "modified": post_data.get("modified"),
            "modified_gmt": post_data.get("modified_gmt"),
            "slug": post_data.get("slug"),
            "status": post_data.get("status"),
            "type": post_data.get("type"),
            "link": post_data.get("permalink"),
            "title": {"rendered": post_data.get("title", "")},
            "content": {"rendered": post_data.get("content", ""), "raw": post_data.get("content", "")},
            "excerpt": {"rendered": post_data.get("excerpt", ""), "raw": post_data.get("excerpt", "")},
            "author": post_data.get("author", {}).get("id"),
            "featured_media": post_data.get("featured_image", {}).get("id"),
            "categories": [cat.get("id") for cat in post_data.get("categories", [])],
            "tags": [tag.get("id") for tag in post_data.get("tags", [])],
            "meta": post_data.get("meta", {}),
            # Preserve SEO data
            "_seo_data": post_data.get("seo", {}),
            # Additional LLAMANATOR2 fields
            "_llamanator_data": {
                "featured_image_url": post_data.get("featured_image", {}).get("url"),
                "author_name": post_data.get("author", {}).get("name"),
                "author_email": post_data.get("author", {}).get("email"),
                "category_details": post_data.get("categories", []),
                "tag_details": post_data.get("tags", []),
            }
        }
        
        return transformed

