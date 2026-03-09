# backend/services/seo_content_processor.py
"""
SEO Content Processor Service
Analyzes SEO scores and generates improvement recommendations
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from backend.models import WordPressPage, db

logger = logging.getLogger(__name__)


class SEOContentProcessor:
    """Service for analyzing SEO scores and generating recommendations"""
    
    def __init__(self, page_id: int):
        """
        Initialize SEO processor for a WordPress page
        
        Args:
            page_id: WordPressPage ID
        """
        self.page_id = page_id
        self.wp_page = db.session.get(WordPressPage, page_id)
        
        if not self.wp_page:
            raise ValueError(f"WordPress page {page_id} not found")
    
    def analyze_seo_score(self) -> Dict[str, Any]:
        """
        Analyze current SEO score and generate breakdown
        
        Returns:
            Dict with SEO score analysis
        """
        score_breakdown = {
            "title": self._analyze_title_seo(),
            "description": self._analyze_description_seo(),
            "content": self._analyze_content_seo(),
            "keywords": self._analyze_keywords_seo(),
            "images": self._analyze_images_seo(),
            "schema": self._analyze_schema_seo(),
            "performance": self._analyze_performance_seo(),
        }
        
        # Calculate overall score
        total_score = sum(item.get("score", 0) for item in score_breakdown.values())
        max_score = len(score_breakdown) * 100
        overall_score = int((total_score / max_score) * 100) if max_score > 0 else 0
        
        # Update page score breakdown
        self.wp_page.seo_score_breakdown = json.dumps(score_breakdown)
        self.wp_page.seo_score = overall_score
        self.wp_page.updated_at = datetime.now()
        
        # Update score history
        self._update_score_history(overall_score)
        
        db.session.commit()
        
        return {
            "success": True,
            "overall_score": overall_score,
            "breakdown": score_breakdown,
            "recommendations": self._generate_recommendations(score_breakdown)
        }
    
    def _analyze_title_seo(self) -> Dict[str, Any]:
        """Analyze title SEO"""
        title = self.wp_page.title or ""
        seo_title = self.wp_page.seo_title or ""
        
        score = 0
        issues = []
        recommendations = []
        
        # Title length check
        if len(title) > 0:
            score += 20
        else:
            issues.append("Title is missing")
            recommendations.append("Add a descriptive title")
        
        if 30 <= len(title) <= 60:
            score += 20
        elif len(title) > 0:
            issues.append(f"Title length is {len(title)} characters (optimal: 30-60)")
            recommendations.append("Optimize title length to 30-60 characters")
        
        # SEO title check
        if seo_title:
            score += 20
            if 50 <= len(seo_title) <= 60:
                score += 20
            else:
                issues.append(f"SEO title length is {len(seo_title)} characters (optimal: 50-60)")
                recommendations.append("Optimize SEO title to 50-60 characters")
        else:
            issues.append("SEO meta title is missing")
            recommendations.append("Add SEO meta title")
        
        # Keyword in title
        focus_keywords = self._get_focus_keywords()
        if focus_keywords and any(kw.lower() in title.lower() for kw in focus_keywords):
            score += 20
        elif focus_keywords:
            issues.append("Focus keywords not found in title")
            recommendations.append("Include focus keywords in title")
        
        return {
            "score": min(score, 100),
            "max_score": 100,
            "issues": issues,
            "recommendations": recommendations
        }
    
    def _analyze_description_seo(self) -> Dict[str, Any]:
        """Analyze meta description SEO"""
        description = self.wp_page.seo_description or ""
        excerpt = self.wp_page.excerpt or ""
        
        score = 0
        issues = []
        recommendations = []
        
        # Description exists
        if description:
            score += 30
            if 150 <= len(description) <= 160:
                score += 30
            else:
                issues.append(f"Description length is {len(description)} characters (optimal: 150-160)")
                recommendations.append("Optimize description length to 150-160 characters")
        elif excerpt:
            score += 20
            issues.append("Using excerpt instead of SEO description")
            recommendations.append("Add dedicated SEO meta description")
        else:
            issues.append("Meta description is missing")
            recommendations.append("Add SEO meta description")
        
        # Keyword in description
        focus_keywords = self._get_focus_keywords()
        if focus_keywords and description and any(kw.lower() in description.lower() for kw in focus_keywords):
            score += 20
        elif focus_keywords:
            issues.append("Focus keywords not found in description")
            recommendations.append("Include focus keywords in description")
        
        # Call-to-action check
        cta_words = ["learn", "discover", "get", "buy", "shop", "try", "start", "explore"]
        if description and any(word in description.lower() for word in cta_words):
            score += 20
        else:
            recommendations.append("Add a call-to-action in description")
        
        return {
            "score": min(score, 100),
            "max_score": 100,
            "issues": issues,
            "recommendations": recommendations
        }
    
    def _analyze_content_seo(self) -> Dict[str, Any]:
        """Analyze content SEO"""
        content = self.wp_page.content or ""
        
        score = 0
        issues = []
        recommendations = []
        
        # Content length
        word_count = len(content.split())
        if word_count >= 300:
            score += 25
        else:
            issues.append(f"Content is {word_count} words (recommended: 300+)")
            recommendations.append("Increase content length to at least 300 words")
        
        if word_count >= 1000:
            score += 15
        
        # Heading structure
        h1_count = content.lower().count("<h1")
        h2_count = content.lower().count("<h2")
        
        if h1_count == 1:
            score += 15
        elif h1_count == 0:
            issues.append("H1 heading is missing")
            recommendations.append("Add one H1 heading")
        else:
            issues.append(f"Multiple H1 headings found ({h1_count})")
            recommendations.append("Use only one H1 heading")
        
        if h2_count >= 2:
            score += 15
        else:
            recommendations.append("Add more H2 headings for better structure")
        
        # Keyword density
        focus_keywords = self._get_focus_keywords()
        if focus_keywords and content:
            keyword = focus_keywords[0].lower()
            keyword_count = content.lower().count(keyword)
            keyword_density = (keyword_count / word_count * 100) if word_count > 0 else 0
            
            if 1 <= keyword_density <= 3:
                score += 20
            elif keyword_density > 3:
                issues.append(f"Keyword density is {keyword_density:.2f}% (optimal: 1-3%)")
                recommendations.append("Reduce keyword density to avoid over-optimization")
            else:
                issues.append(f"Keyword density is {keyword_density:.2f}% (optimal: 1-3%)")
                recommendations.append("Increase keyword usage naturally")
        else:
            recommendations.append("Include focus keywords naturally in content")
        
        # Internal linking (basic check)
        link_count = content.lower().count("<a href")
        if link_count >= 2:
            score += 10
        else:
            recommendations.append("Add internal links to related content")
        
        return {
            "score": min(score, 100),
            "max_score": 100,
            "issues": issues,
            "recommendations": recommendations
        }
    
    def _analyze_keywords_seo(self) -> Dict[str, Any]:
        """Analyze keyword optimization"""
        focus_keywords = self._get_focus_keywords()
        
        score = 0
        issues = []
        recommendations = []
        
        if focus_keywords:
            score += 50
            if len(focus_keywords) > 1:
                score += 20
            else:
                recommendations.append("Consider adding secondary keywords")
        else:
            issues.append("Focus keywords are missing")
            recommendations.append("Add focus keywords")
            return {
                "score": 0,
                "max_score": 100,
                "issues": issues,
                "recommendations": recommendations
            }
        
        # Check if keywords are used in content
        content = self.wp_page.content or ""
        title = self.wp_page.title or ""
        
        if any(kw.lower() in title.lower() for kw in focus_keywords):
            score += 15
        else:
            recommendations.append("Include focus keywords in title")
        
        if content and any(kw.lower() in content.lower() for kw in focus_keywords):
            score += 15
        else:
            recommendations.append("Include focus keywords in content")
        
        return {
            "score": min(score, 100),
            "max_score": 100,
            "issues": issues,
            "recommendations": recommendations
        }
    
    def _analyze_images_seo(self) -> Dict[str, Any]:
        """Analyze image SEO"""
        image_seo_data = []
        if self.wp_page.image_seo_data:
            try:
                image_seo_data = json.loads(self.wp_page.image_seo_data)
            except (ValueError, TypeError):
                pass
        
        if not image_seo_data:
            return {
                "score": 50,
                "max_score": 100,
                "issues": ["No image SEO data available"],
                "recommendations": ["Add alt text to images"]
            }
        
        score = 0
        issues = []
        recommendations = []
        
        images_with_alt = sum(1 for img in image_seo_data if img.get("has_alt"))
        total_images = len(image_seo_data)
        
        if total_images > 0:
            alt_percentage = (images_with_alt / total_images) * 100
            
            if alt_percentage == 100:
                score = 100
            elif alt_percentage >= 80:
                score = 80
            elif alt_percentage >= 50:
                score = 50
            else:
                score = 20
            
            if alt_percentage < 100:
                missing = total_images - images_with_alt
                issues.append(f"{missing} images missing alt text")
                recommendations.append(f"Add alt text to {missing} images")
        else:
            issues.append("No images found in content")
            recommendations.append("Add relevant images with alt text")
        
        return {
            "score": score,
            "max_score": 100,
            "issues": issues,
            "recommendations": recommendations
        }
    
    def _analyze_schema_seo(self) -> Dict[str, Any]:
        """Analyze schema markup"""
        schema_markup = []
        if self.wp_page.schema_markup:
            try:
                schema_markup = json.loads(self.wp_page.schema_markup)
            except (ValueError, TypeError):
                pass
        
        score = 0
        issues = []
        recommendations = []
        
        if schema_markup:
            score = 100
            if isinstance(schema_markup, list) and len(schema_markup) > 1:
                score = 100
                recommendations.append("Multiple schema types detected - ensure they're compatible")
        else:
            issues.append("Schema markup is missing")
            recommendations.append("Add structured data (JSON-LD schema)")
        
        return {
            "score": score,
            "max_score": 100,
            "issues": issues,
            "recommendations": recommendations
        }
    
    def _analyze_performance_seo(self) -> Dict[str, Any]:
        """Analyze performance metrics"""
        score = 0
        issues = []
        recommendations = []
        
        # PageSpeed scores
        mobile_score = self.wp_page.pagespeed_score_mobile
        desktop_score = self.wp_page.pagespeed_score_desktop
        
        if mobile_score is not None:
            if mobile_score >= 90:
                score += 25
            elif mobile_score >= 70:
                score += 15
            else:
                issues.append(f"Mobile PageSpeed score is {mobile_score} (target: 90+)")
                recommendations.append("Improve mobile page speed")
        else:
            recommendations.append("Run PageSpeed analysis")
        
        if desktop_score is not None:
            if desktop_score >= 90:
                score += 25
            elif desktop_score >= 70:
                score += 15
            else:
                issues.append(f"Desktop PageSpeed score is {desktop_score} (target: 90+)")
                recommendations.append("Improve desktop page speed")
        else:
            recommendations.append("Run PageSpeed analysis")
        
        # Analytics data
        analytics_data = {}
        if self.wp_page.analytics_data:
            try:
                analytics_data = json.loads(self.wp_page.analytics_data)
            except (ValueError, TypeError):
                pass
        
        if analytics_data:
            score += 25
            
            # Check for engagement metrics
            if analytics_data.get("avg_position", 0) > 0:
                position = analytics_data.get("avg_position", 0)
                if position <= 10:
                    score += 25
                elif position <= 30:
                    score += 15
                else:
                    recommendations.append(f"Average position is {position} - improve ranking")
        else:
            recommendations.append("Connect Google Search Console for analytics")
        
        return {
            "score": min(score, 100),
            "max_score": 100,
            "issues": issues,
            "recommendations": recommendations
        }
    
    def _get_focus_keywords(self) -> List[str]:
        """Get focus keywords from page"""
        if not self.wp_page.focus_keywords:
            return []
        
        try:
            keywords = json.loads(self.wp_page.focus_keywords)
            if isinstance(keywords, list):
                return keywords
            elif isinstance(keywords, str):
                return [k.strip() for k in keywords.split(",")]
        except (ValueError, TypeError):
            pass
        
        return []
    
    def _generate_recommendations(self, breakdown: Dict[str, Any]) -> List[str]:
        """Generate prioritized recommendations"""
        all_recommendations = []
        
        for category, analysis in breakdown.items():
            if analysis.get("score", 100) < 70:
                all_recommendations.extend(analysis.get("recommendations", []))
        
        # Remove duplicates while preserving order
        seen = set()
        unique_recommendations = []
        for rec in all_recommendations:
            if rec not in seen:
                seen.add(rec)
                unique_recommendations.append(rec)
        
        return unique_recommendations[:10]  # Top 10 recommendations
    
    def _update_score_history(self, score: int) -> None:
        """Update SEO score history"""
        history = []
        if self.wp_page.seo_score_history:
            try:
                history = json.loads(self.wp_page.seo_score_history)
            except (ValueError, TypeError):
                pass
        
        history.append({
            "date": datetime.now().isoformat(),
            "score": score
        })
        
        # Keep only last 30 entries
        history = history[-30:]
        
        self.wp_page.seo_score_history = json.dumps(history)
    
    def get_recommendations_summary(self) -> Dict[str, Any]:
        """Get summary of recommendations"""
        analysis = self.analyze_seo_score()
        
        return {
            "current_score": analysis["overall_score"],
            "recommendations": analysis["recommendations"],
            "priority_issues": [
                issue for category, data in analysis["breakdown"].items()
                for issue in data.get("issues", [])
            ]
        }

