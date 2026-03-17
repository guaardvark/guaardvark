#!/usr/bin/env python3
"""
Bulk XML Generator for WordPress Imports
Generates Llamanator-compatible XML files for WordPress import
Version 1.0: Initial implementation with full metadata support
"""

import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional
from xml.dom import minidom

from backend.utils.bulk_csv_generator import BulkCSVGenerator, GenerationTask

logger = logging.getLogger(__name__)


class BulkXMLGenerator(BulkCSVGenerator):
    """
    Enhanced XML generator for WordPress imports via Llamanator plugin.
    Extends BulkCSVGenerator to maintain same batch processing architecture.
    """

    def __init__(
        self,
        output_dir: str,
        concurrent_workers: int = 10,
        batch_size: int = 50,
        target_word_count: int = 500,
        prompt_rule: Optional[object] = None,
        unified_progress_system=None,
        job_id=None,
        **kwargs,
    ):
        """Initialize XML generator with same parameters as CSV generator"""
        super().__init__(
            output_dir=output_dir,
            concurrent_workers=concurrent_workers,
            batch_size=batch_size,
            target_word_count=target_word_count,
            unified_progress_system=unified_progress_system,
            job_id=job_id,
            **kwargs,
        )
        self.prompt_rule = prompt_rule
        self.output_format = "xml"
        logger.info(
            f"BulkXMLGenerator initialized with {concurrent_workers} workers, batch size {batch_size}"
        )

    def generate_bulk_content(
        self, tasks: List[GenerationTask], output_filename: str, **kwargs
    ) -> Dict:
        """
        Generate XML file with WordPress-compatible structure.

        Args:
            tasks: List of generation tasks
            output_filename: Name of output XML file
            **kwargs: Additional parameters (insert_content, insert_position, etc.)

        Returns:
            Dict with success status, file path, and statistics
        """
        try:
            logger.info(f"Starting XML generation for {len(tasks)} tasks")

            # FIX BUG #36: Use correct batch processing method from parent class
            # Store tasks for progress calculation
            self._current_tasks = tasks

            # Update initial progress
            self._update_progress(f"Starting XML generation: {len(tasks)} tasks", 0.0)

            # Process tasks in batches using parent class method
            all_results = []
            total_batches = (len(tasks) + self.batch_size - 1) // self.batch_size

            for batch_num in range(total_batches):
                batch_start = batch_num * self.batch_size
                batch_end = min(batch_start + self.batch_size, len(tasks))
                batch_tasks = tasks[batch_start:batch_end]

                logger.info(
                    f"Processing batch {batch_num + 1}/{total_batches} ({len(batch_tasks)} tasks)"
                )

                # Generate content for batch using parent's concurrent method
                batch_results = self._generate_batch_concurrent(batch_tasks)
                all_results.extend(batch_results)

                # Update progress
                progress_pct = (len(all_results) / len(tasks)) * 100
                self._update_progress(
                    f"Batch {batch_num + 1}/{total_batches} complete. Generated: {len(all_results)}/{len(tasks)}",
                    progress_pct,
                )

            # Convert ContentRow objects to dictionaries for XML building
            results = []
            for content_row in all_results:
                if content_row:
                    results.append(
                        {
                            "id": content_row.id,
                            "title": content_row.title,
                            "content": content_row.content,
                            "excerpt": content_row.excerpt,
                            "category": content_row.category,
                            "tags": content_row.tags,
                            "slug": content_row.slug,
                            "image": getattr(content_row, "image", ""),
                        }
                    )

            # Build XML structure
            xml_data = self._build_xml_structure(results, **kwargs)

            # Write XML file
            output_path = os.path.join(self.output_dir, output_filename)
            self._write_xml_file(xml_data, output_path)

            # Calculate statistics
            stats = {
                "total_tasks": len(tasks),
                "successful": len([r for r in results if r is not None]),
                "failed": len([r for r in results if r is None]),
                "output_file": output_path,
                "file_size": (
                    os.path.getsize(output_path) if os.path.exists(output_path) else 0
                ),
            }

            logger.info(
                f"XML generation complete: {stats['successful']}/{stats['total_tasks']} successful"
            )

            return {
                "success": True,
                "file_path": output_path,
                "statistics": stats,
                "message": f"Generated {stats['successful']} posts in XML format",
            }

        except Exception as e:
            logger.error(f"XML generation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"XML generation failed: {e}",
            }

    def _build_xml_structure(self, results: List[Dict], **kwargs) -> ET.Element:
        """
        Build XML structure compatible with Llamanator plugin.

        Args:
            results: List of generated content dictionaries
            **kwargs: Additional options (insert_content, insert_position)

        Returns:
            XML root element
        """
        # Create root element
        root = ET.Element("llamanator_export")
        root.set("version", "3.02")
        root.set("generator", "LLM009.2 Bulk Generator")
        from datetime import timezone

        root.set("date", datetime.now(timezone.utc).isoformat())

        # Get insert content options
        insert_content = kwargs.get("insert_content", "")
        insert_position = kwargs.get("insert_position", "none")

        # Process each result
        for idx, result in enumerate(results, 1):
            if result is None:
                continue

            post = ET.SubElement(root, "post")

            # Core WordPress fields
            self._add_xml_element(post, "ID", result.get("id", str(idx)))
            self._add_xml_element(
                post, "Title", result.get("title", f"Untitled Post {idx}")
            )

            # Handle content with insert options
            content = result.get("content", "")
            if insert_content and insert_position != "none":
                if insert_position == "top":
                    content = f"{insert_content}\n\n{content}"
                elif insert_position == "bottom":
                    content = f"{content}\n\n{insert_content}"

            self._add_xml_element(post, "Content", content, use_cdata=True)
            self._add_xml_element(
                post, "Excerpt", result.get("excerpt", ""), use_cdata=True
            )

            # Taxonomy fields
            categories = result.get("category", result.get("categories", ""))
            if isinstance(categories, list):
                categories = "|".join(categories)
            self._add_xml_element(post, "Category", categories)

            tags = result.get("tags", "")
            if isinstance(tags, list):
                tags = "|".join(tags)
            self._add_xml_element(post, "Tags", tags)

            # Permalink/slug
            self._add_xml_element(post, "slug", result.get("slug", ""))

            # Featured image (if available)
            if "image" in result or "featured_image" in result:
                image_url = result.get("image", result.get("featured_image", ""))
                if image_url:
                    self._add_xml_element(post, "Featured Image", image_url)

            # Custom metadata fields (anything not in core fields)
            core_fields = {
                "id",
                "title",
                "content",
                "excerpt",
                "category",
                "categories",
                "tags",
                "slug",
                "image",
                "featured_image",
            }
            for key, value in result.items():
                if key not in core_fields and value:
                    # Custom fields with underscore prefix are treated as private meta
                    field_name = key if key.startswith("_") else f"_{key}"
                    self._add_xml_element(post, field_name, str(value))

        return root

    def _add_xml_element(
        self, parent: ET.Element, tag: str, text: str, use_cdata: bool = False
    ):
        """
        Add an XML element with proper escaping.

        Args:
            parent: Parent XML element
            tag: Tag name
            text: Text content
            use_cdata: Whether to wrap content in CDATA section
        """
        if text is None or (isinstance(text, str) and not text.strip()):
            return

        element = ET.SubElement(parent, tag)

        if use_cdata:
            # CDATA sections are added during prettification
            element.text = text
            element.set("cdata", "true")  # Mark for CDATA wrapping
        else:
            element.text = str(text)

    def _write_xml_file(self, root: ET.Element, output_path: str):
        """
        Write XML to file with proper formatting and CDATA sections.

        Args:
            root: Root XML element
            output_path: Path to output file
        """
        # Convert to string
        xml_string = ET.tostring(root, encoding="utf-8", method="xml")

        # Pretty print with minidom (without XML declaration to avoid double declaration)
        dom = minidom.parseString(xml_string)
        pretty_xml_str = dom.toprettyxml(indent="    ")

        # Add proper XML declaration manually
        if not pretty_xml_str.startswith("<?xml"):
            pretty_xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + pretty_xml_str

        pretty_xml = pretty_xml_str.encode("utf-8")

        # Replace CDATA markers with actual CDATA sections
        pretty_xml_str = pretty_xml.decode("utf-8")

        # Find elements marked for CDATA and wrap them (handles multiline elements)
        import re

        # Use regex to find cdata="true" marked elements across multiple lines
        def replace_cdata(match):
            """Replace cdata marked elements with proper CDATA sections"""
            tag_open = match.group(1)
            content = match.group(2)
            tag_close = match.group(3)
            # Remove cdata attribute and wrap content
            tag_open_clean = tag_open.replace(' cdata="true"', "")
            return f"{tag_open_clean}<![CDATA[{content}]]>{tag_close}"

        # Pattern matches: <Tag cdata="true">content</Tag> (multiline-aware)
        pattern = r'(<[^>]+ cdata="true"[^>]*>)(.*?)(<\/[^>]+>)'
        output_lines = []

        for line in pretty_xml_str.split("\n"):
            if 'cdata="true"' in line:
                # Check if this is a single-line CDATA element
                if ">" in line and "</" in line and line.count(">") >= 2:
                    # Single line - use simple replacement
                    line = line.replace(' cdata="true"', "")
                    if ">" in line and "</" in line:
                        start_tag_end = line.index(">")
                        end_tag_start = line.rindex("</")
                        tag_start = line[: start_tag_end + 1]
                        content = line[start_tag_end + 1 : end_tag_start]
                        tag_end = line[end_tag_start:]
                        line = f"{tag_start}<![CDATA[{content}]]>{tag_end}"
            output_lines.append(line)

        final_xml = "\n".join(output_lines)

        # Write to file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(final_xml)

        logger.info(f"XML file written to: {output_path}")

    def _parse_csv_response(
        self, response_text: str, task: GenerationTask
    ) -> Optional["ContentRow"]:
        """
        Parse LLM response into ContentRow object.
        Overrides parent method to ensure XML-compatible output.

        Args:
            response_text: Raw LLM response
            task: Original generation task

        Returns:
            ContentRow object with structured content fields
        """
        try:
            # Use parent class parsing logic - returns ContentRow object
            content_row = super()._parse_csv_response(response_text, task)

            if content_row:
                # Ensure ID is set (ContentRow already has id field)
                if not content_row.id or content_row.id == "":
                    content_row.id = task.item_id

                # Ensure slug is set
                if not content_row.slug or content_row.slug == "":
                    fallback_title = getattr(task, "topic", "Untitled Post")
                    title = content_row.title if content_row.title else fallback_title
                    content_row.slug = self._generate_slug(title)

            return content_row

        except Exception as e:
            logger.error(f"Error parsing response for XML: {e}")
            return None

    def _generate_slug(self, title: str) -> str:
        """
        Generate URL-safe slug from title.

        Args:
            title: Post title

        Returns:
            URL-safe slug
        """
        import re

        if not title or not isinstance(title, str):
            return "untitled-post"

        # Convert to lowercase
        slug = title.lower()

        # Replace spaces and special characters with hyphens
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[-\s]+", "-", slug)

        # Remove leading/trailing hyphens
        slug = slug.strip("-")

        # Limit slug length but warn if truncated
        if len(slug) > 200:
            logger.warning(
                f"Slug truncated from {len(slug)} to 200 characters: '{slug[:50]}...'"
            )
            slug = slug[:200]

        return slug or "untitled-post"  # Fallback if slug is empty after processing


# Convenience function for direct usage
def generate_xml_file(
    output_dir: str,
    output_filename: str,
    tasks: List[GenerationTask],
    concurrent_workers: int = 10,
    batch_size: int = 50,
    target_word_count: int = 500,
    prompt_rule: Optional[object] = None,
    unified_progress_system=None,
    job_id=None,
    **kwargs,
) -> Dict:
    """
    Generate XML file with batch processing.

    Args:
        output_dir: Output directory path
        output_filename: Name of output XML file
        tasks: List of generation tasks
        concurrent_workers: Number of concurrent workers
        batch_size: Batch size for processing
        target_word_count: Target word count per item
        prompt_rule: Optional prompt rule for generation
        unified_progress_system: Progress tracking system
        job_id: Job ID for progress tracking
        **kwargs: Additional options (insert_content, insert_position)

    Returns:
        Result dictionary with success status and file path
    """
    generator = BulkXMLGenerator(
        output_dir=output_dir,
        concurrent_workers=concurrent_workers,
        batch_size=batch_size,
        target_word_count=target_word_count,
        prompt_rule=prompt_rule,
        unified_progress_system=unified_progress_system,
        job_id=job_id,
        **kwargs,
    )

    return generator.generate_bulk_content(tasks, output_filename, **kwargs)
