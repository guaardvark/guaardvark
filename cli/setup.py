import re
from pathlib import Path
from setuptools import setup, find_packages

# Pull the top-level README in as long_description so the PyPI project
# page has the same content as the GitHub landing page. Relative image
# paths are rewritten to absolute raw.githubusercontent.com URLs so they
# render on PyPI, which does not serve repo-relative assets.
_repo_root = Path(__file__).resolve().parent.parent
_readme_path = _repo_root / "README.md"
_long_description = ""
if _readme_path.exists():
    _long_description = _readme_path.read_text(encoding="utf-8")
    _raw_base = "https://raw.githubusercontent.com/guaardvark/guaardvark/main/"
    _long_description = re.sub(
        r'\(docs/screenshots/', f'({_raw_base}docs/screenshots/', _long_description
    )
    _long_description = re.sub(
        r'src="docs/screenshots/', f'src="{_raw_base}docs/screenshots/', _long_description
    )

setup(
    name="guaardvark",
    version="2.6.0",
    description="Guaardvark CLI — full-stack AI platform with RAG, image/video generation, and agents",
    long_description=_long_description,
    long_description_content_type="text/markdown",
    author="Guaardvark",
    url="https://guaardvark.com",
    project_urls={
        "Source": "https://github.com/guaardvark/guaardvark",
        "Homepage": "https://guaardvark.com",
        "Issues": "https://github.com/guaardvark/guaardvark/issues",
    },
    packages=find_packages(),
    install_requires=[
        "typer[all]>=0.9.0",
        "rich>=13.0.0",
        "python-socketio>=5.10.0",
        "httpx>=0.25.0",
        "websocket-client>=1.6.0",
        "requests>=2.31.0",
        "prompt_toolkit>=3.0.0",
        "tenacity>=8.0.0",
        "flask>=3.0.0",
    ],
    extras_require={
        "rag": [
            "llama-index-core>=0.13.0,<0.15.0",
            "llama-index-llms-ollama>=0.7.0",
            "llama-index-embeddings-ollama>=0.8.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "guaardvark=llx.main:run",
        ],
    },
    python_requires=">=3.12",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
)
