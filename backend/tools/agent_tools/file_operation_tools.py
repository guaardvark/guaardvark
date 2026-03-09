#!/usr/bin/env python3
"""
File Operation Tools
Tools for reading, listing, and processing files
"""

import logging
import os
from pathlib import Path
from typing import Optional, List

from backend.services.agent_tools import BaseTool, ToolResult, ToolParameter

logger = logging.getLogger(__name__)


class ReadFileTool(BaseTool):
    """Read file contents with optional line range"""
    
    name = "read_file"
    description = "Read the contents of a file, optionally specifying a line range"
    parameters = {
        "file_path": ToolParameter(
            name="file_path",
            type="string",
            required=True,
            description="Path to the file to read"
        ),
        "offset": ToolParameter(
            name="offset",
            type="int",
            required=False,
            description="Starting line number (1-indexed, optional)",
            default=None
        ),
        "limit": ToolParameter(
            name="limit",
            type="int",
            required=False,
            description="Number of lines to read (optional)",
            default=None
        )
    }
    
    def execute(self, file_path: str, offset: Optional[int] = None, limit: Optional[int] = None) -> ToolResult:
        """
        Read file contents
        
        Args:
            file_path: Path to file
            offset: Starting line number (1-indexed)
            limit: Number of lines to read
            
        Returns:
            ToolResult with file contents
        """
        try:
            # Resolve and validate path
            path = Path(file_path).resolve()
            
            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"File not found: {file_path}"
                )
            
            if not path.is_file():
                return ToolResult(
                    success=False,
                    error=f"Path is not a file: {file_path}"
                )
            
            # Read file
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    if offset is not None:
                        # Skip to offset
                        for _ in range(offset - 1):
                            f.readline()
                    
                    if limit is not None:
                        # Read limited lines
                        lines = [f.readline() for _ in range(limit)]
                        content = ''.join(lines)
                    else:
                        content = f.read()
                
                # Get file metadata
                file_size = path.stat().st_size
                line_count = content.count('\n') + 1 if content else 0
                
                return ToolResult(
                    success=True,
                    output=content,
                    metadata={
                        'file_path': str(path),
                        'file_size': file_size,
                        'line_count': line_count,
                        'offset': offset,
                        'limit': limit
                    }
                )
                
            except UnicodeDecodeError:
                return ToolResult(
                    success=False,
                    error=f"File is not a text file or has encoding issues: {file_path}"
                )
                
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to read file: {str(e)}"
            )


class ListFilesTool(BaseTool):
    """List files and directories in a given path"""
    
    name = "list_files"
    description = "List files and directories in a specified path"
    parameters = {
        "directory": ToolParameter(
            name="directory",
            type="string",
            required=True,
            description="Directory path to list"
        ),
        "recursive": ToolParameter(
            name="recursive",
            type="bool",
            required=False,
            description="List files recursively (default: False)",
            default=False
        ),
        "pattern": ToolParameter(
            name="pattern",
            type="string",
            required=False,
            description="File pattern to match (e.g., '*.py', optional)",
            default=None
        )
    }
    
    def execute(self, directory: str, recursive: bool = False, pattern: Optional[str] = None) -> ToolResult:
        """
        List files in directory
        
        Args:
            directory: Directory path
            recursive: List recursively
            pattern: Optional file pattern
            
        Returns:
            ToolResult with file list
        """
        try:
            path = Path(directory).resolve()
            
            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"Directory not found: {directory}"
                )
            
            if not path.is_dir():
                return ToolResult(
                    success=False,
                    error=f"Path is not a directory: {directory}"
                )
            
            # List files
            files = []
            dirs = []
            
            if recursive:
                if pattern:
                    file_list = path.rglob(pattern)
                else:
                    file_list = path.rglob('*')
            else:
                if pattern:
                    file_list = path.glob(pattern)
                else:
                    file_list = path.glob('*')
            
            for item in file_list:
                rel_path = str(item.relative_to(path))
                if item.is_file():
                    files.append({
                        'path': rel_path,
                        'size': item.stat().st_size,
                        'type': 'file'
                    })
                elif item.is_dir():
                    dirs.append({
                        'path': rel_path,
                        'type': 'directory'
                    })
            
            # Format output
            output = []
            if dirs:
                output.append("Directories:")
                for d in sorted(dirs, key=lambda x: x['path']):
                    output.append(f"  📁 {d['path']}/")
            
            if files:
                output.append("\nFiles:")
                for f in sorted(files, key=lambda x: x['path']):
                    size_kb = f['size'] / 1024
                    output.append(f"  📄 {f['path']} ({size_kb:.1f} KB)")
            
            output_text = "\n".join(output) if output else "Empty directory"
            
            return ToolResult(
                success=True,
                output=output_text,
                metadata={
                    'directory': str(path),
                    'file_count': len(files),
                    'dir_count': len(dirs),
                    'recursive': recursive,
                    'pattern': pattern,
                    'files': files,
                    'directories': dirs
                }
            )
            
        except Exception as e:
            logger.error(f"Error listing directory {directory}: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to list directory: {str(e)}"
            )


class ProcessFileTool(BaseTool):
    """Process and extract content from any supported file format"""
    
    name = "process_file"
    description = "Process and extract content from files (PDF, DOCX, CSV, images, Excel, etc.)"
    parameters = {
        "file_path": ToolParameter(
            name="file_path",
            type="string",
            required=True,
            description="Path to file to process"
        )
    }
    
    def execute(self, file_path: str) -> ToolResult:
        """
        Process file using EnhancedFileProcessor
        
        Args:
            file_path: Path to file
            
        Returns:
            ToolResult with extracted content
        """
        try:
            from backend.utils.enhanced_file_processor import create_file_processor
            
            path = Path(file_path).resolve()
            
            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"File not found: {file_path}"
                )
            
            # Create processor and process file
            processor = create_file_processor()
            result = processor.process_file(str(path))
            
            if result:
                return ToolResult(
                    success=True,
                    output=result.text_content,
                    metadata={
                        'file_path': str(path),
                        'format': result.metadata.format.value,
                        'size_bytes': result.metadata.size_bytes,
                        'word_count': result.metadata.word_count,
                        'page_count': result.metadata.page_count,
                        'mime_type': result.metadata.mime_type
                    }
                )
            else:
                return ToolResult(
                    success=False,
                    error=f"Failed to process file: {file_path}"
                )
                
        except ImportError as e:
            logger.error(f"Enhanced file processor not available: {e}")
            return ToolResult(
                success=False,
                error="File processing system not available"
            )
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to process file: {str(e)}"
            )

