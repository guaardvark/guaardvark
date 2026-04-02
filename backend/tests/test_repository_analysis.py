import pytest
from backend.services.repository_analysis_service import RepositoryAnalysisService


class TestFrameworkDetection:

    def test_detects_nodejs(self):
        files = ["package.json", "src/index.js", "src/app.js"]
        frameworks = RepositoryAnalysisService.detect_frameworks(files)
        assert "Node.js" in frameworks

    def test_detects_python(self):
        files = ["requirements.txt", "src/app.py"]
        frameworks = RepositoryAnalysisService.detect_frameworks(files)
        assert "Python" in frameworks

    def test_detects_react(self):
        files = ["package.json", "src/App.jsx", "src/index.js"]
        frameworks = RepositoryAnalysisService.detect_frameworks(files)
        assert "React" in frameworks

    def test_detects_flask(self):
        files = ["requirements.txt", "backend/app.py", "backend/routes.py"]
        frameworks = RepositoryAnalysisService.detect_frameworks(files)
        assert "Python" in frameworks

    def test_detects_go(self):
        files = ["go.mod", "main.go", "pkg/server.go"]
        frameworks = RepositoryAnalysisService.detect_frameworks(files)
        assert "Go" in frameworks

    def test_detects_rust(self):
        files = ["Cargo.toml", "src/main.rs"]
        frameworks = RepositoryAnalysisService.detect_frameworks(files)
        assert "Rust" in frameworks

    def test_no_framework(self):
        files = ["readme.txt", "data.csv"]
        frameworks = RepositoryAnalysisService.detect_frameworks(files)
        assert frameworks == []


class TestLanguageBreakdown:

    def test_counts_extensions(self):
        files = ["a.py", "b.py", "c.js", "d.ts", "readme.md"]
        breakdown = RepositoryAnalysisService.get_language_breakdown(files)
        assert breakdown["py"] == 2
        assert breakdown["js"] == 1
        assert breakdown["ts"] == 1
        assert breakdown["md"] == 1
