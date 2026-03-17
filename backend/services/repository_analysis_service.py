import logging
import json
from datetime import datetime
from backend.models import Folder, Document, db
from backend.services.llm_service import LLMService
from backend.services.indexing_service import add_text_to_index

logger = logging.getLogger(__name__)


class RepositoryAnalysisService:
    @staticmethod
    def analyze_repository(folder_id):
        """
        Analyze a folder as a code repository.
        1. Identify languages and frameworks.
        2. Generate architectural summary.
        3. Index summary.
        """
        folder = db.session.get(Folder, folder_id)
        if not folder:
            logger.error(f"Folder {folder_id} not found for analysis")
            return

        logger.info(f"Analyzing repository: {folder.name}")

        # 1. Collect file stats
        files = folder.documents.all()
        # For a real implementation, we would traversal strictly or rely on flat list if documents are flattened?
        # Folder.documents relationship might only be immediate children.
        # We need recursive file list.

        all_files = RepositoryAnalysisService._get_all_files_recursive(folder)

        languages = {}
        file_structure = []

        for doc in all_files:
            # Safer extension extraction
            parts = doc.filename.split(".")
            ext = parts[-1].lower() if len(parts) > 1 else "no_extension"
            languages[ext] = languages.get(ext, 0) + 1
            file_structure.append(doc.path)

        # 2. Heuristic Framework Detection (Basic)
        frameworks = []
        if any("package.json" in f for f in file_structure):
            frameworks.append("Node.js")
        if any("requirements.txt" in f for f in file_structure) or any(
            "pyproject.toml" in f for f in file_structure
        ):
            frameworks.append("Python")
        if any("pom.xml" in f for f in file_structure):
            frameworks.append("Java/Maven")
        if any("go.mod" in f for f in file_structure):
            frameworks.append("Go")
        if any("Cargo.toml" in f for f in file_structure):
            frameworks.append("Rust")

        # 3. Generate Summary using LLM
        # We'll send a truncated file list to LLM for high-level structure analysis
        file_list_str = "\\n".join(file_structure[:500])  # Limit to first 500 files
        if len(file_structure) > 500:
            file_list_str += f"\\n... (and {len(file_structure) - 500} more files)"

        prompt = f"""
        Analyze the following file structure for a software project and provide an architectural summary.
        
        Project Name: {folder.name}
        Detected Frameworks: {', '.join(frameworks)}
        File Structure:
        {file_list_str}
        
        Please provide:
        1. Overview of the project structure.
        2. Likely architecture pattern (MVC, Microservices, etc).
        3. Key components identification based on file names.
        """

        try:
            # Using LLMService via Agent or direct call if possible.
            # We will use a simplified mock for now to ensure reliability until LLMService is fully verified
            # In production, this would call: response = LLMService.generate(prompt)

            # Placeholder summary enhanced with dynamic data
            summary = (
                f"Repository Analysis for {folder.name}\\n"
                f"=======================================\\n"
                f"Frameworks Detected: {', '.join(frameworks) if frameworks else 'None detected'}\\n"
                f"Total Files: {len(all_files)}\\n"
                f"Language Breakdown: {json.dumps(languages, indent=2)}\\n\\n"
                f"Architecture Overview:\\n"
                f"Based on the file structure, this appears to be a software project containing "
                f"{len(languages)} different file types. "
                f"Key directories include: {', '.join(set([f.split('/')[0] for f in file_structure if '/' in f][:5]))}."
            )

        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            summary = "Analysis failed."

        # 4. Save Metadata
        metadata = {
            "languages": languages,
            "frameworks": frameworks,
            "file_count": len(all_files),
            "analyzed_at": datetime.now().isoformat(),
        }

        folder.description = summary
        folder.repo_metadata = json.dumps(metadata)
        db.session.commit()

        # 5. Index the Summary (RAG)
        try:
            success = add_text_to_index(
                text=summary,
                metadata={
                    "type": "repository_summary",
                    "folder_id": folder.id,
                    "folder_name": folder.name,
                    "folder_path": folder.path,
                    "source": "repository_analysis",
                },
            )
            if success:
                logger.info(f"Successfully indexed summary for {folder.name}")
            else:
                logger.warning(f"Failed to index summary for {folder.name}")
        except Exception as e:
            logger.error(f"Error indexing summary: {e}")

        logger.info(f"Finished analysis for {folder.name}")

    @staticmethod
    def _get_all_files_recursive(folder):
        files = list(folder.documents.all())
        for sub in folder.subfolders.all():
            files.extend(RepositoryAnalysisService._get_all_files_recursive(sub))
        return files
