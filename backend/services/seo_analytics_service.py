# backend/services/seo_analytics_service.py
"""
SEO Analytics Service
Tracks SEO performance, rankings, and generates reports
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict

from backend.models import WordPressPage, WordPressSite, db
from backend.services.llamanator_api_service import LlamanatorAPIService

logger = logging.getLogger(__name__)


class SEOAnalyticsService:
    """Service for tracking SEO performance and generating reports"""
    
    def __init__(self, site_id: Optional[int] = None):
        """
        Initialize SEO analytics service
        
        Args:
            site_id: Optional WordPressSite ID to limit analysis to specific site
        """
        self.site_id = site_id
    
    def sync_analytics_for_site(self, site_id: int, days: int = 30) -> Dict[str, Any]:
        """
        Sync analytics data for all pages in a site
        
        Args:
            site_id: WordPressSite ID
            days: Number of days to look back
        
        Returns:
            Dict with sync results
        """
        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return {"success": False, "error": "Site not found"}
        
        # Only works with LLAMANATOR2 connections
        connection_type = getattr(wp_site, 'connection_type', 'llamanator') or 'llamanator'
        if connection_type != 'llamanator':
            return {"success": False, "error": "Analytics sync only available for LLAMANATOR2 connections"}
        
        try:
            wp_service = LlamanatorAPIService(
                site_url=wp_site.url,
                api_key=wp_site.api_key
            )
            
            # Get all pages for this site
            pages = db.session.query(WordPressPage).filter_by(
                wordpress_site_id=site_id,
                pull_status="pulled"
            ).all()
            
            synced_count = 0
            failed_count = 0
            
            for page in pages:
                try:
                    # Sync analytics
                    analytics = wp_service.get_seo_analytics(page.wordpress_post_id, days=days)
                    if analytics.get("success"):
                        page.analytics_data = json.dumps(analytics.get("data", {}))
                        page.analytics_synced_at = datetime.now()
                        synced_count += 1
                    else:
                        failed_count += 1
                        
                except Exception as e:
                    logger.warning(f"Failed to sync analytics for page {page.id}: {e}")
                    failed_count += 1
            
            db.session.commit()
            
            return {
                "success": True,
                "synced": synced_count,
                "failed": failed_count,
                "total": len(pages)
            }
            
        except Exception as e:
            logger.error(f"Error syncing analytics for site {site_id}: {e}", exc_info=True)
            db.session.rollback()
            return {"success": False, "error": str(e)}
    
    def sync_pagespeed_for_site(self, site_id: int) -> Dict[str, Any]:
        """
        Sync PageSpeed data for all pages in a site
        
        Args:
            site_id: WordPressSite ID
        
        Returns:
            Dict with sync results
        """
        wp_site = db.session.get(WordPressSite, site_id)
        if not wp_site:
            return {"success": False, "error": "Site not found"}
        
        connection_type = getattr(wp_site, 'connection_type', 'llamanator') or 'llamanator'
        if connection_type != 'llamanator':
            return {"success": False, "error": "PageSpeed sync only available for LLAMANATOR2 connections"}
        
        try:
            wp_service = LlamanatorAPIService(
                site_url=wp_site.url,
                api_key=wp_site.api_key
            )
            
            pages = db.session.query(WordPressPage).filter_by(
                wordpress_site_id=site_id,
                pull_status="pulled"
            ).all()
            
            synced_count = 0
            failed_count = 0
            
            for page in pages:
                try:
                    pagespeed = wp_service.get_pagespeed_data(page.wordpress_post_id)
                    if pagespeed.get("success"):
                        pagespeed_data = pagespeed.get("data", {})
                        page.pagespeed_score_mobile = pagespeed_data.get("mobile", {}).get("score")
                        page.pagespeed_score_desktop = pagespeed_data.get("desktop", {}).get("score")
                        page.pagespeed_data = json.dumps(pagespeed_data)
                        page.pagespeed_synced_at = datetime.now()
                        synced_count += 1
                    else:
                        failed_count += 1
                        
                except Exception as e:
                    logger.warning(f"Failed to sync PageSpeed for page {page.id}: {e}")
                    failed_count += 1
            
            db.session.commit()
            
            return {
                "success": True,
                "synced": synced_count,
                "failed": failed_count,
                "total": len(pages)
            }
            
        except Exception as e:
            logger.error(f"Error syncing PageSpeed for site {site_id}: {e}", exc_info=True)
            db.session.rollback()
            return {"success": False, "error": str(e)}
    
    def get_site_performance_report(self, site_id: int, days: int = 30) -> Dict[str, Any]:
        """
        Generate performance report for a site
        
        Args:
            site_id: WordPressSite ID
            days: Number of days to analyze
        
        Returns:
            Dict with performance metrics
        """
        pages = db.session.query(WordPressPage).filter_by(
            wordpress_site_id=site_id,
            pull_status="pulled"
        ).all()
        
        if not pages:
            return {"success": False, "error": "No pages found"}
        
        total_pages = len(pages)
        pages_with_analytics = 0
        pages_with_pagespeed = 0
        
        total_clicks = 0
        total_impressions = 0
        total_pageviews = 0
        avg_position = 0
        avg_ctr = 0
        
        avg_mobile_score = 0
        avg_desktop_score = 0
        
        seo_scores = []
        pages_by_score_range = defaultdict(int)
        
        for page in pages:
            # Analytics data
            if page.analytics_data:
                try:
                    analytics = json.loads(page.analytics_data)
                    pages_with_analytics += 1
                    total_clicks += analytics.get("clicks", 0)
                    total_impressions += analytics.get("impressions", 0)
                    total_pageviews += analytics.get("pageviews", 0)
                    
                    if analytics.get("avg_position"):
                        avg_position += analytics.get("avg_position", 0)
                    if analytics.get("avg_ctr"):
                        avg_ctr += analytics.get("avg_ctr", 0)
                except (ValueError, TypeError):
                    pass
            
            # PageSpeed data
            if page.pagespeed_score_mobile is not None:
                pages_with_pagespeed += 1
                avg_mobile_score += page.pagespeed_score_mobile
            if page.pagespeed_score_desktop is not None:
                avg_desktop_score += page.pagespeed_score_desktop
            
            # SEO scores
            if page.seo_score is not None:
                seo_scores.append(page.seo_score)
                
                # Categorize by score range
                if page.seo_score >= 90:
                    pages_by_score_range["excellent"] += 1
                elif page.seo_score >= 70:
                    pages_by_score_range["good"] += 1
                elif page.seo_score >= 50:
                    pages_by_score_range["fair"] += 1
                else:
                    pages_by_score_range["poor"] += 1
        
        # Calculate averages
        if pages_with_analytics > 0:
            avg_position = avg_position / pages_with_analytics if pages_with_analytics > 0 else 0
            avg_ctr = avg_ctr / pages_with_analytics if pages_with_analytics > 0 else 0
        
        if pages_with_pagespeed > 0:
            avg_mobile_score = avg_mobile_score / pages_with_pagespeed if pages_with_pagespeed > 0 else 0
        
        avg_seo_score = sum(seo_scores) / len(seo_scores) if seo_scores else None
        
        return {
            "success": True,
            "site_id": site_id,
            "period_days": days,
            "summary": {
                "total_pages": total_pages,
                "pages_with_analytics": pages_with_analytics,
                "pages_with_pagespeed": pages_with_pagespeed,
                "pages_with_seo_score": len(seo_scores)
            },
            "analytics": {
                "total_clicks": total_clicks,
                "total_impressions": total_impressions,
                "total_pageviews": total_pageviews,
                "avg_position": round(avg_position, 2) if avg_position > 0 else None,
                "avg_ctr": round(avg_ctr, 4) if avg_ctr > 0 else None
            },
            "performance": {
                "avg_mobile_score": round(avg_mobile_score, 0) if avg_mobile_score > 0 else None,
                "avg_desktop_score": round(avg_desktop_score, 0) if avg_desktop_score > 0 else None
            },
            "seo_scores": {
                "average": round(avg_seo_score, 1) if avg_seo_score else None,
                "distribution": dict(pages_by_score_range)
            }
        }
    
    def get_top_performing_pages(self, site_id: int, metric: str = "clicks", limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get top performing pages by metric
        
        Args:
            site_id: WordPressSite ID
            metric: Metric to sort by (clicks, impressions, position, pageviews, seo_score)
            limit: Number of pages to return
        
        Returns:
            List of page performance data
        """
        pages = db.session.query(WordPressPage).filter_by(
            wordpress_site_id=site_id,
            pull_status="pulled"
        ).all()
        
        page_data = []
        
        for page in pages:
            data = {
                "page_id": page.id,
                "wordpress_post_id": page.wordpress_post_id,
                "title": page.title,
                "slug": page.slug,
                "permalink": f"{page.wordpress_site.url}/{page.slug}" if page.slug else None,
                "metric_value": 0
            }
            
            # Get metric value
            if metric == "seo_score":
                data["metric_value"] = page.seo_score or 0
            elif metric == "pagespeed_mobile":
                data["metric_value"] = page.pagespeed_score_mobile or 0
            elif metric == "pagespeed_desktop":
                data["metric_value"] = page.pagespeed_score_desktop or 0
            elif page.analytics_data:
                try:
                    analytics = json.loads(page.analytics_data)
                    data["metric_value"] = analytics.get(metric, 0)
                    data["analytics"] = analytics
                except (ValueError, TypeError):
                    pass
            
            if data["metric_value"] > 0:
                page_data.append(data)
        
        # Sort by metric value (descending)
        page_data.sort(key=lambda x: x["metric_value"], reverse=True)
        
        return page_data[:limit]
    
    def get_keyword_performance(self, site_id: int, keyword: str) -> Dict[str, Any]:
        """
        Get performance data for a specific keyword
        
        Args:
            site_id: WordPressSite ID
            keyword: Keyword to search for
        
        Returns:
            Dict with keyword performance data
        """
        pages = db.session.query(WordPressPage).filter_by(
            wordpress_site_id=site_id,
            pull_status="pulled"
        ).all()
        
        matching_pages = []
        
        for page in pages:
            focus_keywords = []
            if page.focus_keywords:
                try:
                    focus_keywords = json.loads(page.focus_keywords)
                except (ValueError, TypeError):
                    pass
            
            # Check if keyword matches
            if keyword.lower() in [kw.lower() for kw in focus_keywords]:
                analytics = {}
                if page.analytics_data:
                    try:
                        analytics = json.loads(page.analytics_data)
                    except (ValueError, TypeError):
                        pass
                
                matching_pages.append({
                    "page_id": page.id,
                    "title": page.title,
                    "slug": page.slug,
                    "seo_score": page.seo_score,
                    "analytics": analytics
                })
        
        # Aggregate metrics
        total_clicks = sum(p.get("analytics", {}).get("clicks", 0) for p in matching_pages)
        total_impressions = sum(p.get("analytics", {}).get("impressions", 0) for p in matching_pages)
        avg_position = sum(p.get("analytics", {}).get("avg_position", 0) for p in matching_pages)
        avg_position = avg_position / len(matching_pages) if matching_pages else 0
        
        return {
            "keyword": keyword,
            "pages_using_keyword": len(matching_pages),
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "avg_position": round(avg_position, 2) if avg_position > 0 else None,
            "pages": matching_pages
        }
    
    def generate_seo_report(self, site_id: Optional[int] = None, 
                           client_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Generate comprehensive SEO report
        
        Args:
            site_id: Optional WordPressSite ID
            client_id: Optional Client ID
        
        Returns:
            Dict with comprehensive SEO report
        """
        query = db.session.query(WordPressPage).filter_by(pull_status="pulled")
        
        if site_id:
            query = query.filter_by(wordpress_site_id=site_id)
        elif client_id:
            # Get sites for client
            sites = db.session.query(WordPressSite).filter_by(client_id=client_id).all()
            site_ids = [s.id for s in sites]
            query = query.filter(WordPressPage.wordpress_site_id.in_(site_ids))
        
        pages = query.all()
        
        if not pages:
            return {"success": False, "error": "No pages found"}
        
        # Generate report sections
        report = {
            "generated_at": datetime.now().isoformat(),
            "total_pages": len(pages),
            "sites_analyzed": len(set(p.wordpress_site_id for p in pages)),
            "performance_summary": self._calculate_performance_summary(pages),
            "top_pages": self._get_top_pages_summary(pages),
            "improvement_opportunities": self._identify_improvement_opportunities(pages),
            "recommendations": self._generate_report_recommendations(pages)
        }
        
        return {
            "success": True,
            "report": report
        }
    
    def _calculate_performance_summary(self, pages: List[WordPressPage]) -> Dict[str, Any]:
        """Calculate overall performance summary"""
        seo_scores = [p.seo_score for p in pages if p.seo_score is not None]
        mobile_scores = [p.pagespeed_score_mobile for p in pages if p.pagespeed_score_mobile is not None]
        desktop_scores = [p.pagespeed_score_desktop for p in pages if p.pagespeed_score_desktop is not None]
        
        total_clicks = 0
        total_impressions = 0
        
        for page in pages:
            if page.analytics_data:
                try:
                    analytics = json.loads(page.analytics_data)
                    total_clicks += analytics.get("clicks", 0)
                    total_impressions += analytics.get("impressions", 0)
                except (ValueError, TypeError):
                    pass

        return {
            "avg_seo_score": round(sum(seo_scores) / len(seo_scores), 1) if seo_scores else None,
            "avg_mobile_pagespeed": round(sum(mobile_scores) / len(mobile_scores), 0) if mobile_scores else None,
            "avg_desktop_pagespeed": round(sum(desktop_scores) / len(desktop_scores), 0) if desktop_scores else None,
            "total_clicks": total_clicks,
            "total_impressions": total_impressions
        }
    
    def _get_top_pages_summary(self, pages: List[WordPressPage], limit: int = 5) -> Dict[str, Any]:
        """Get summary of top performing pages"""
        # Top by SEO score
        top_seo = sorted(
            [p for p in pages if p.seo_score is not None],
            key=lambda x: x.seo_score or 0,
            reverse=True
        )[:limit]
        
        return {
            "top_by_seo_score": [
                {"title": p.title, "score": p.seo_score, "id": p.id}
                for p in top_seo
            ]
        }
    
    def _identify_improvement_opportunities(self, pages: List[WordPressPage]) -> List[Dict[str, Any]]:
        """Identify pages that need improvement"""
        opportunities = []
        
        for page in pages:
            issues = []
            
            if page.seo_score is not None and page.seo_score < 70:
                issues.append(f"Low SEO score: {page.seo_score}/100")
            
            if page.pagespeed_score_mobile is not None and page.pagespeed_score_mobile < 70:
                issues.append(f"Low mobile PageSpeed: {page.pagespeed_score_mobile}")
            
            if not page.seo_title or not page.seo_description:
                issues.append("Missing SEO metadata")
            
            if issues:
                opportunities.append({
                    "page_id": page.id,
                    "title": page.title,
                    "issues": issues
                })
        
        return opportunities[:20]  # Top 20 opportunities
    
    def _generate_report_recommendations(self, pages: List[WordPressPage]) -> List[str]:
        """Generate actionable recommendations"""
        recommendations = []
        
        # Count pages by status
        pages_without_seo = sum(1 for p in pages if not p.seo_title)
        pages_with_low_score = sum(1 for p in pages if p.seo_score is not None and p.seo_score < 70)
        pages_without_pagespeed = sum(1 for p in pages if p.pagespeed_score_mobile is None)
        
        if pages_without_seo > 0:
            recommendations.append(f"{pages_without_seo} pages missing SEO metadata - add meta titles and descriptions")
        
        if pages_with_low_score > 0:
            recommendations.append(f"{pages_with_low_score} pages have SEO scores below 70 - review and optimize")
        
        if pages_without_pagespeed > 0:
            recommendations.append(f"{pages_without_pagespeed} pages missing PageSpeed data - run performance analysis")
        
        return recommendations

