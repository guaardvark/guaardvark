from setuptools import setup, find_packages

setup(
    name="llx",
    version="1.0.0",
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
    ],
    entry_points={
        "console_scripts": [
            "guaardvark=llx.main:run",
            "llx=llx.main:run",
        ],
    },
    python_requires=">=3.12",
)
