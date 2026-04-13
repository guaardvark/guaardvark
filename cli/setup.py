from setuptools import setup, find_packages

setup(
    name="guaardvark",
    version="2.6.0",
    description="Guaardvark CLI — full-stack AI platform with RAG, image/video generation, and agents",
    author="Guaardvark",
    url="https://guaardvark.com",
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
