import pytest
from backend.utils.gitignore_filter import GitignoreFilter


class TestGitignoreFilter:

    def test_default_ignores_node_modules(self):
        f = GitignoreFilter()
        assert f.should_ignore("node_modules/react/index.js") is True

    def test_default_ignores_pycache(self):
        f = GitignoreFilter()
        assert f.should_ignore("__pycache__/module.cpython-312.pyc") is True

    def test_default_ignores_git_dir(self):
        f = GitignoreFilter()
        assert f.should_ignore(".git/objects/abc123") is True

    def test_default_ignores_pyc_files(self):
        f = GitignoreFilter()
        assert f.should_ignore("src/utils.pyc") is True

    def test_default_ignores_min_js(self):
        f = GitignoreFilter()
        assert f.should_ignore("dist/bundle.min.js") is True

    def test_default_ignores_env_files(self):
        f = GitignoreFilter()
        assert f.should_ignore(".env") is True
        assert f.should_ignore(".env.local") is True

    def test_default_ignores_lock_files(self):
        f = GitignoreFilter()
        assert f.should_ignore("package-lock.json") is True
        assert f.should_ignore("yarn.lock") is True

    def test_allows_normal_code_files(self):
        f = GitignoreFilter()
        assert f.should_ignore("src/app.py") is False
        assert f.should_ignore("frontend/src/App.jsx") is False
        assert f.should_ignore("README.md") is False

    def test_allows_package_json(self):
        f = GitignoreFilter()
        assert f.should_ignore("package.json") is False

    def test_custom_gitignore_content(self):
        f = GitignoreFilter(gitignore_content="*.log\nbuild/\n")
        assert f.should_ignore("debug.log") is True
        assert f.should_ignore("build/output.js") is True
        assert f.should_ignore("src/app.py") is False

    def test_additional_patterns(self):
        f = GitignoreFilter(additional_patterns=["*.tmp", "scratch/"])
        assert f.should_ignore("data.tmp") is True
        assert f.should_ignore("scratch/notes.txt") is True
        assert f.should_ignore("src/app.py") is False

    def test_filter_file_list(self):
        f = GitignoreFilter()
        files = [
            "src/app.py",
            "node_modules/react/index.js",
            "src/utils.py",
            "__pycache__/app.cpython-312.pyc",
            "README.md",
        ]
        kept, ignored = f.filter_file_list(files)
        assert set(kept) == {"src/app.py", "src/utils.py", "README.md"}
        assert len(ignored) == 2

    def test_filter_returns_counts(self):
        f = GitignoreFilter()
        files = ["a.py", "node_modules/x.js", "b.py"]
        kept, ignored = f.filter_file_list(files)
        assert len(kept) == 2
        assert len(ignored) == 1
