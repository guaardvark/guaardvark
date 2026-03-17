#!/usr/bin/env python3
"""
RAG-Enhanced Output Generator
Improves CSV and XML outputs with retrieval-augmented generation metadata and context
"""

import json
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class RAGContext:
    """Retrieval-Augmented Generation context for content generation"""

    # Source documents used in retrieval
    retrieved_documents: List[Dict[str, Any]]

    # Similarity scores for retrieved documents
    similarity_scores: List[float]

    # Query used for retrieval
    retrieval_query: str

    # Entity relationships identified
    entities: List[str]
    entity_relationships: Dict[str, List[str]]

    # Semantic keywords extracted
    semantic_keywords: List[str]

    # Context sources
    context_sources: List[
        str
    ]  # e.g., ['client_docs', 'competitor_analysis', 'web_scraping']

    # Metadata
    timestamp: str
    confidence_score: float


@dataclass
class EnhancedContentMetadata:
    """Enhanced metadata for RAG-aware content"""

    # Core identification
    content_id: str
    title: str

    # RAG context
    rag_context: RAGContext

    # Content classification
    content_type: str  # 'blog_post', 'product_page', 'service_page', etc.
    primary_topic: str
    secondary_topics: List[str]

    # SEO and semantic metadata
    target_keywords: List[str]
    semantic_clusters: List[str]
    related_entities: List[str]

    # Generation metadata
    generation_model: str
    generation_timestamp: str
    word_count: int

    # Quality metrics
    relevance_score: float
    coherence_score: float

    # Source attribution
    source_documents: List[str]
    competitor_insights: Optional[str] = None


class RAGEnhancedCSVWriter:
    """CSV writer with RAG metadata integration"""

    def __init__(self, include_rag_metadata: bool = True):
        self.include_rag_metadata = include_rag_metadata

    def enhance_csv_row(
        self,
        base_row: Dict[str, str],
        rag_context: RAGContext,
        metadata: EnhancedContentMetadata,
    ) -> Dict[str, str]:
        """
        Enhance a CSV row with RAG metadata

        Args:
            base_row: Original CSV row (ID, Title, Content, Excerpt, etc.)
            rag_context: RAG context from retrieval
            metadata: Enhanced content metadata

        Returns:
            Enhanced CSV row with additional RAG fields
        """
        enhanced_row = base_row.copy()

        if not self.include_rag_metadata:
            return enhanced_row

        # Add RAG metadata as additional columns
        enhanced_row["_rag_entities"] = json.dumps(rag_context.entities)
        enhanced_row["_rag_keywords"] = ", ".join(rag_context.semantic_keywords)
        enhanced_row["_rag_sources"] = ", ".join(rag_context.context_sources)
        enhanced_row["_rag_confidence"] = str(rag_context.confidence_score)

        # Add semantic metadata
        enhanced_row["_semantic_topics"] = ", ".join(metadata.secondary_topics)
        enhanced_row["_semantic_clusters"] = ", ".join(metadata.semantic_clusters)
        enhanced_row["_related_entities"] = ", ".join(metadata.related_entities)

        # Add quality metrics
        enhanced_row["_relevance_score"] = str(metadata.relevance_score)
        enhanced_row["_coherence_score"] = str(metadata.coherence_score)

        # Add source attribution
        enhanced_row["_source_docs"] = json.dumps(metadata.source_documents)

        if metadata.competitor_insights:
            enhanced_row["_competitor_insights"] = metadata.competitor_insights

        return enhanced_row

    def create_rag_metadata_sidecar(
        self,
        content_id: str,
        rag_context: RAGContext,
        metadata: EnhancedContentMetadata,
        output_path: str,
    ):
        """
        Create a separate JSON sidecar file with full RAG metadata

        Args:
            content_id: Content identifier
            rag_context: RAG context
            metadata: Content metadata
            output_path: Path for sidecar file
        """
        sidecar_data = {
            "content_id": content_id,
            "rag_context": {
                "retrieved_documents": rag_context.retrieved_documents,
                "similarity_scores": rag_context.similarity_scores,
                "retrieval_query": rag_context.retrieval_query,
                "entities": rag_context.entities,
                "entity_relationships": rag_context.entity_relationships,
                "semantic_keywords": rag_context.semantic_keywords,
                "context_sources": rag_context.context_sources,
                "timestamp": rag_context.timestamp,
                "confidence_score": rag_context.confidence_score,
            },
            "metadata": asdict(metadata),
            "generated_at": datetime.now().isoformat(),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(sidecar_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Created RAG metadata sidecar: {output_path}")


class RAGEnhancedXMLWriter:
    """XML writer with RAG metadata integration"""

    def __init__(self, schema_version: str = "3.1"):
        self.schema_version = schema_version

    def create_rag_metadata_element(
        self, parent_element, rag_context: RAGContext, metadata: EnhancedContentMetadata
    ):
        """
        Add RAG metadata as XML subelements

        Args:
            parent_element: Parent XML element (typically <post>)
            rag_context: RAG context
            metadata: Content metadata
        """
        import xml.etree.ElementTree as ET

        # Create RAG metadata container
        rag_meta = ET.SubElement(parent_element, "rag_metadata")
        rag_meta.set("version", self.schema_version)
        rag_meta.set("timestamp", rag_context.timestamp)

        # Add retrieval context
        retrieval = ET.SubElement(rag_meta, "retrieval")
        ET.SubElement(retrieval, "query").text = rag_context.retrieval_query
        ET.SubElement(retrieval, "confidence").text = str(rag_context.confidence_score)

        # Add retrieved documents
        docs = ET.SubElement(retrieval, "documents")
        for i, (doc, score) in enumerate(
            zip(rag_context.retrieved_documents, rag_context.similarity_scores)
        ):
            doc_elem = ET.SubElement(docs, "document")
            doc_elem.set("rank", str(i + 1))
            doc_elem.set("similarity", str(score))
            ET.SubElement(doc_elem, "source").text = doc.get("source", "unknown")
            ET.SubElement(doc_elem, "excerpt").text = doc.get("text", "")[:200]

        # Add entity information
        entities = ET.SubElement(rag_meta, "entities")
        for entity in rag_context.entities:
            ent_elem = ET.SubElement(entities, "entity")
            ent_elem.text = entity

            # Add relationships if available
            if entity in rag_context.entity_relationships:
                rels = rag_context.entity_relationships[entity]
                ent_elem.set("relationships", ", ".join(rels))

        # Add semantic keywords
        keywords = ET.SubElement(rag_meta, "semantic_keywords")
        for kw in rag_context.semantic_keywords:
            ET.SubElement(keywords, "keyword").text = kw

        # Add content classification
        classification = ET.SubElement(rag_meta, "classification")
        ET.SubElement(classification, "content_type").text = metadata.content_type
        ET.SubElement(classification, "primary_topic").text = metadata.primary_topic

        topics = ET.SubElement(classification, "secondary_topics")
        for topic in metadata.secondary_topics:
            ET.SubElement(topics, "topic").text = topic

        # Add quality metrics
        quality = ET.SubElement(rag_meta, "quality_metrics")
        ET.SubElement(quality, "relevance").text = str(metadata.relevance_score)
        ET.SubElement(quality, "coherence").text = str(metadata.coherence_score)
        ET.SubElement(quality, "word_count").text = str(metadata.word_count)

        # Add source attribution
        sources = ET.SubElement(rag_meta, "sources")
        for source in metadata.source_documents:
            ET.SubElement(sources, "source").text = source

        if metadata.competitor_insights:
            insights = ET.SubElement(rag_meta, "competitor_insights")
            insights.text = metadata.competitor_insights

        return rag_meta

    def create_enhanced_wordpress_xml(
        self,
        posts: List[Dict[str, Any]],
        rag_contexts: List[RAGContext],
        metadata_list: List[EnhancedContentMetadata],
    ) -> str:
        """
        Create WordPress-compatible XML with embedded RAG metadata

        Args:
            posts: List of post dictionaries
            rag_contexts: List of RAG contexts for each post
            metadata_list: List of metadata for each post

        Returns:
            XML string
        """
        import xml.etree.ElementTree as ET
        from xml.dom import minidom

        # Create root element
        root = ET.Element("llamanator_export")
        root.set("version", self.schema_version)
        root.set("rag_enhanced", "true")
        root.set("generator", "LLM010.1 RAG-Enhanced Generator")
        root.set("date", datetime.now().isoformat())

        # Add global RAG statistics
        stats = ET.SubElement(root, "rag_statistics")
        ET.SubElement(stats, "total_posts").text = str(len(posts))

        avg_confidence = (
            sum(rc.confidence_score for rc in rag_contexts) / len(rag_contexts)
            if rag_contexts
            else 0
        )
        ET.SubElement(stats, "avg_confidence").text = str(avg_confidence)

        all_sources = set()
        for rc in rag_contexts:
            all_sources.update(rc.context_sources)
        ET.SubElement(stats, "context_sources").text = ", ".join(all_sources)

        # Add each post with RAG metadata
        for post_data, rag_ctx, meta in zip(posts, rag_contexts, metadata_list):
            post = ET.SubElement(root, "post")

            # Standard WordPress fields
            ET.SubElement(post, "ID").text = post_data.get("id", "")
            ET.SubElement(post, "Title").text = post_data.get("title", "")

            content_elem = ET.SubElement(post, "Content")
            content_elem.text = post_data.get("content", "")
            content_elem.set("cdata", "true")

            excerpt_elem = ET.SubElement(post, "Excerpt")
            excerpt_elem.text = post_data.get("excerpt", "")
            excerpt_elem.set("cdata", "true")

            ET.SubElement(post, "Category").text = post_data.get("category", "")
            ET.SubElement(post, "Tags").text = post_data.get("tags", "")
            ET.SubElement(post, "slug").text = post_data.get("slug", "")

            # Add RAG metadata
            self.create_rag_metadata_element(post, rag_ctx, meta)

        # Convert to formatted XML string
        xml_string = ET.tostring(root, encoding="utf-8", method="xml")
        dom = minidom.parseString(xml_string)
        pretty_xml = dom.toprettyxml(indent="  ")

        return pretty_xml


class RAGContextBuilder:
    """Builder for creating RAG context from retrieval results"""

    def __init__(self, query_engine=None, entity_enhancer=None):
        self.query_engine = query_engine
        self.entity_enhancer = entity_enhancer

    def build_rag_context(
        self,
        query: str,
        retrieved_docs: List[Dict[str, Any]],
        similarity_scores: List[float],
        context_sources: List[str],
    ) -> RAGContext:
        """
        Build RAG context from retrieval results

        Args:
            query: The retrieval query
            retrieved_docs: Retrieved documents
            similarity_scores: Similarity scores for documents
            context_sources: Sources of context (e.g., 'client_docs', 'competitor_analysis')

        Returns:
            RAGContext object
        """
        # Extract entities from retrieved documents
        entities = self._extract_entities(retrieved_docs)

        # Build entity relationships
        entity_relationships = self._build_entity_relationships(
            entities, retrieved_docs
        )

        # Extract semantic keywords
        semantic_keywords = self._extract_semantic_keywords(retrieved_docs)

        # Calculate confidence score
        confidence_score = self._calculate_confidence(
            similarity_scores, len(retrieved_docs)
        )

        return RAGContext(
            retrieved_documents=retrieved_docs,
            similarity_scores=similarity_scores,
            retrieval_query=query,
            entities=entities,
            entity_relationships=entity_relationships,
            semantic_keywords=semantic_keywords,
            context_sources=context_sources,
            timestamp=datetime.now().isoformat(),
            confidence_score=confidence_score,
        )

    def _extract_entities(self, docs: List[Dict[str, Any]]) -> List[str]:
        """Extract named entities from documents"""
        entities = set()

        if self.entity_enhancer:
            try:
                for doc in docs:
                    text = doc.get("text", "")
                    # Use entity enhancer if available
                    enhanced = self.entity_enhancer.enhance_query_context(text, [])
                    entities.update(enhanced.get("entity_mentions", {}).keys())
            except Exception as e:
                logger.warning(f"Entity extraction failed: {e}")
        else:
            # Simple extraction - look for capitalized phrases
            import re

            for doc in docs:
                text = doc.get("text", "")
                # Find capitalized words (simple entity detection)
                found = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
                entities.update(found[:10])  # Limit to top 10

        return list(entities)[:20]  # Return top 20 entities

    def _build_entity_relationships(
        self, entities: List[str], docs: List[Dict[str, Any]]
    ) -> Dict[str, List[str]]:
        """Build entity relationship map"""
        relationships = {}

        # Simple co-occurrence based relationships
        for entity in entities:
            related = []
            for doc in docs:
                text = doc.get("text", "").lower()
                if entity.lower() in text:
                    # Find other entities in same document
                    for other_entity in entities:
                        if other_entity != entity and other_entity.lower() in text:
                            if other_entity not in related:
                                related.append(other_entity)

            if related:
                relationships[entity] = related[:5]  # Limit to top 5 related

        return relationships

    def _extract_semantic_keywords(self, docs: List[Dict[str, Any]]) -> List[str]:
        """Extract semantic keywords using TF-IDF-like approach"""
        from collections import Counter
        import re

        # Common stop words to filter
        stop_words = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "from",
            "as",
            "is",
            "was",
            "are",
            "were",
            "been",
            "be",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "can",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
            "they",
            "them",
            "their",
        }

        word_counts = Counter()

        for doc in docs:
            text = doc.get("text", "").lower()
            # Extract words (4+ characters)
            words = re.findall(r"\b[a-z]{4,}\b", text)
            # Filter stop words
            meaningful_words = [w for w in words if w not in stop_words]
            word_counts.update(meaningful_words)

        # Return top keywords
        top_keywords = [word for word, _ in word_counts.most_common(30)]
        return top_keywords

    def _calculate_confidence(self, scores: List[float], doc_count: int) -> float:
        """Calculate overall confidence score"""
        if not scores:
            return 0.0

        # Average of top 3 scores, weighted by document count
        avg_score = sum(scores[:3]) / min(3, len(scores))

        # Penalize if too few documents
        doc_penalty = min(1.0, doc_count / 5)

        return round(avg_score * doc_penalty, 3)


# Convenience functions
def enhance_csv_with_rag(
    csv_rows: List[Dict[str, str]],
    rag_contexts: List[RAGContext],
    metadata_list: List[EnhancedContentMetadata],
    include_metadata: bool = True,
) -> List[Dict[str, str]]:
    """
    Enhance CSV rows with RAG metadata

    Args:
        csv_rows: Original CSV rows
        rag_contexts: RAG contexts for each row
        metadata_list: Metadata for each row
        include_metadata: Whether to include RAG metadata columns

    Returns:
        Enhanced CSV rows
    """
    writer = RAGEnhancedCSVWriter(include_rag_metadata=include_metadata)

    enhanced_rows = []
    for row, rag_ctx, meta in zip(csv_rows, rag_contexts, metadata_list):
        enhanced_row = writer.enhance_csv_row(row, rag_ctx, meta)
        enhanced_rows.append(enhanced_row)

    return enhanced_rows


def create_rag_enhanced_xml(
    posts: List[Dict[str, Any]],
    rag_contexts: List[RAGContext],
    metadata_list: List[EnhancedContentMetadata],
) -> str:
    """
    Create RAG-enhanced WordPress XML

    Args:
        posts: List of post dictionaries
        rag_contexts: RAG contexts for each post
        metadata_list: Metadata for each post

    Returns:
        XML string
    """
    writer = RAGEnhancedXMLWriter()
    return writer.create_enhanced_wordpress_xml(posts, rag_contexts, metadata_list)
