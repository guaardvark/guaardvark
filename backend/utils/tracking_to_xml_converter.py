#!/usr/bin/env python3
"""
Tracking JSON to XML Converter for Llamanator Import
Converts bulk generation tracking JSON files to Llamanator-compatible XML format
"""

import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional
from xml.dom import minidom

logger = logging.getLogger(__name__)


class TrackingToXMLConverter:
    """
    Converts bulk generation tracking JSON files to Llamanator-compatible XML format.

    Reads tracking JSON files created by the bulk CSV generator and converts them
    to XML format that can be imported directly into WordPress via Llamanator plugin.
    """

    def __init__(self):
        """Initialize the converter."""
        self.logger = logging.getLogger(__name__)

    def convert_tracking_to_xml(
        self,
        tracking_json_path: str,
        output_xml_path: str,
        include_inactive: bool = False,
    ) -> Dict:
        """
        Convert tracking JSON file to Llamanator-compatible XML.

        Args:
            tracking_json_path: Path to tracking JSON file
            output_xml_path: Path for output XML file
            include_inactive: Whether to include inactive rows (default: False)

        Returns:
            Dict with conversion results and statistics
        """
        try:
            self.logger.info(f"Converting tracking JSON: {tracking_json_path}")

            # Load tracking data
            tracking_data = self._load_tracking_json(tracking_json_path)
            if not tracking_data:
                return {"success": False, "error": "Failed to load tracking JSON"}

            # Extract active row records
            active_rows = self._extract_active_rows(tracking_data, include_inactive)
            if not active_rows:
                return {
                    "success": False,
                    "error": "No active rows found in tracking data",
                }

            # Build XML structure
            xml_root = self._build_xml_structure(
                active_rows, tracking_data.get("metadata", {})
            )

            # Write XML file
            self._write_xml_file(xml_root, output_xml_path)

            # Calculate statistics
            stats = {
                "total_rows_in_tracking": len(tracking_data.get("row_records", [])),
                "active_rows_exported": len(active_rows),
                "inactive_rows_skipped": len(tracking_data.get("row_records", []))
                - len(active_rows),
                "output_file": output_xml_path,
                "file_size": (
                    os.path.getsize(output_xml_path)
                    if os.path.exists(output_xml_path)
                    else 0
                ),
            }

            self.logger.info(
                f"XML conversion complete: {stats['active_rows_exported']} rows exported"
            )

            return {
                "success": True,
                "file_path": output_xml_path,
                "statistics": stats,
                "message": f"Converted {stats['active_rows_exported']} rows to XML format",
            }

        except Exception as e:
            self.logger.error(f"XML conversion failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"XML conversion failed: {e}",
            }

    def _load_tracking_json(self, json_path: str) -> Optional[Dict]:
        """Load and validate tracking JSON file."""
        try:
            if not os.path.exists(json_path):
                self.logger.error(f"Tracking JSON file not found: {json_path}")
                return None

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "row_records" not in data:
                self.logger.error("Invalid tracking JSON: missing 'row_records'")
                return None

            self.logger.info(
                f"Loaded tracking JSON with {len(data.get('row_records', []))} row records"
            )
            return data

        except Exception as e:
            self.logger.error(f"Error loading tracking JSON: {e}")
            return None

    def _extract_active_rows(
        self, tracking_data: Dict, include_inactive: bool = False
    ) -> List[Dict]:
        """Extract active row records from tracking data."""
        active_rows = []

        for record in tracking_data.get("row_records", []):
            # Check if row is active
            is_active = record.get("is_active", False)
            if not is_active and not include_inactive:
                continue

            # Get current row data
            current_row_data = record.get("current_row_data", {})
            if not current_row_data:
                self.logger.warning(
                    f"Row {record.get('unique_id', 'unknown')} has no current_row_data"
                )
                continue

            # Add sequence number for sorting
            current_row_data["_sequence_number"] = record.get("sequence_number", 0)
            current_row_data["_unique_id"] = record.get("unique_id", "")
            current_row_data["_status"] = record.get("status", "unknown")

            active_rows.append(current_row_data)

        # Sort by sequence number to maintain order
        active_rows.sort(key=lambda x: x.get("_sequence_number", 0))

        self.logger.info(f"Extracted {len(active_rows)} active rows")
        return active_rows

    def _build_xml_structure(
        self, active_rows: List[Dict], metadata: Dict
    ) -> ET.Element:
        """Build XML structure compatible with Llamanator plugin."""
        # Create root element - Llamanator expects simple root without attributes
        root = ET.Element("llamanator_export")

        # Process each active row
        for row_data in active_rows:
            post = ET.SubElement(root, "post")

            # Core WordPress fields - map from tracking data to XML
            self._add_xml_element(post, "ID", row_data.get("ID", ""))
            self._add_xml_element(post, "Title", row_data.get("Title", ""))

            # Content with CDATA
            content = row_data.get("Content", "")
            self._add_xml_element(post, "Content", content, use_cdata=True)

            # Excerpt with CDATA
            excerpt = row_data.get("Excerpt", "")
            self._add_xml_element(post, "Excerpt", excerpt, use_cdata=True)

            # Category - fix malformed JSON array format
            category = self._fix_category_field(row_data.get("Category", ""))
            self._add_xml_element(post, "Category", category)

            # Tags
            tags = row_data.get("Tags", "")
            self._add_xml_element(post, "Tags", tags)

            # Slug
            slug = row_data.get("slug", "")
            self._add_xml_element(post, "slug", slug)

            # Status and Type (if available)
            status = row_data.get("Status", "draft")
            self._add_xml_element(post, "Status", status)

            post_type = row_data.get("Type", "post")
            self._add_xml_element(post, "Type", post_type)

            # Date (if available)
            date = row_data.get("Date", "")
            if date:
                self._add_xml_element(post, "Date", date)

            # Featured Image (if available)
            featured_image = row_data.get(
                "Featured_Image", row_data.get("Featured Image", "")
            )
            if featured_image:
                self._add_xml_element(post, "Featured_Image", featured_image)

            # Meta Description (if available)
            meta_description = row_data.get(
                "Meta Description", row_data.get("meta_description", "")
            )
            if meta_description:
                self._add_xml_element(post, "Meta_Description", meta_description)

            # Add tracking metadata for reference
            self._add_xml_element(
                post, "_llamanator_unique_id", row_data.get("_unique_id", "")
            )
            self._add_xml_element(
                post, "_llamanator_status", row_data.get("_status", "")
            )

        return root

    def _fix_category_field(self, category: str) -> str:
        """Fix malformed category field from tracking data."""
        if not category:
            return ""

        # Handle malformed JSON array format like '[\"Law Firm\"'
        if category.startswith('["') and not category.endswith('"]'):
            # Extract the category name from malformed JSON
            category = category.replace('["', "").replace('"', "").strip()
        elif category.startswith("[") and category.endswith("]"):
            # Handle proper JSON array format
            try:
                import json

                categories = json.loads(category)
                if isinstance(categories, list):
                    category = "|".join(categories)
            except (ValueError, TypeError):
                # If JSON parsing fails, clean up the string
                category = category.strip("[]").replace('"', "").replace("'", "")

        return category

    def _add_xml_element(
        self, parent: ET.Element, tag: str, text: str, use_cdata: bool = False
    ):
        """Add an XML element with proper escaping."""
        if text is None or (isinstance(text, str) and not text.strip()):
            return

        element = ET.SubElement(parent, tag)

        if use_cdata:
            # Mark for CDATA wrapping during file writing
            element.text = text
            element.set("cdata", "true")
        else:
            element.text = str(text)

    def _write_xml_file(self, root: ET.Element, output_path: str):
        """Write XML to file with proper formatting and CDATA sections."""
        # Convert to string
        xml_string = ET.tostring(root, encoding="utf-8", method="xml")

        # Pretty print with minidom
        dom = minidom.parseString(xml_string)
        pretty_xml_str = dom.toprettyxml(indent="    ")

        # Add proper XML declaration
        if not pretty_xml_str.startswith("<?xml"):
            pretty_xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + pretty_xml_str

        # Replace CDATA markers with actual CDATA sections
        def replace_cdata(match):
            tag_open = match.group(1)
            content = match.group(2)
            tag_close = match.group(3)
            tag_open_clean = tag_open.replace(' cdata="true"', "")
            return f"{tag_open_clean}<![CDATA[{content}]]>{tag_close}"

        # Pattern matches: <Tag cdata="true">content</Tag>
        import re

        pattern = r'(<[^>]+ cdata="true"[^>]*>)(.*?)(<\/[^>]+>)'
        output_lines = []

        for line in pretty_xml_str.split("\n"):
            if 'cdata="true"' in line:
                # Handle single-line CDATA elements
                if ">" in line and "</" in line and line.count(">") >= 2:
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

        self.logger.info(f"XML file written to: {output_path}")


def convert_tracking_file(
    tracking_json_path: str, output_xml_path: str, include_inactive: bool = False
) -> Dict:
    """
    Convenience function to convert a tracking JSON file to XML.

    Args:
        tracking_json_path: Path to tracking JSON file
        output_xml_path: Path for output XML file
        include_inactive: Whether to include inactive rows

    Returns:
        Dict with conversion results
    """
    converter = TrackingToXMLConverter()
    return converter.convert_tracking_to_xml(
        tracking_json_path, output_xml_path, include_inactive
    )


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 3:
        print(
            "Usage: python tracking_to_xml_converter.py <tracking_json> <output_xml> [include_inactive]"
        )
        sys.exit(1)

    tracking_file = sys.argv[1]
    output_file = sys.argv[2]
    include_inactive = len(sys.argv) > 3 and sys.argv[3].lower() == "true"

    result = convert_tracking_file(tracking_file, output_file, include_inactive)

    if result["success"]:
        print(f"✅ Conversion successful: {result['message']}")
        print(f"📁 Output file: {result['file_path']}")
        print(f"📊 Statistics: {result['statistics']}")
    else:
        print(f"❌ Conversion failed: {result['error']}")
        sys.exit(1)
