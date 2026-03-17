# Guaardvark — Installation

## Install

1. **Extract the release:**
   ```bash
   unzip guaardvark.v2.5.1.zip
   cd guaardvark
   ```

2. **Start:**
   ```bash
   ./start.sh
   ```

That's it. The startup script handles everything: Python venv, Node dependencies, PostgreSQL, Redis, database migrations, frontend build, and all services. First run requires your system password once (to provision PostgreSQL).

## Access

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

## Requirements

- Python 3.12+
- Node.js 20+
- Linux (Ubuntu/Debian recommended)
- NVIDIA GPU with 16GB VRAM recommended (not required for chat/RAG)

## Troubleshooting

- If you encounter permission issues: `chmod +x *.sh`
- Check logs in the `logs/` directory
- Run `./start.sh --test` for comprehensive health diagnostics

## Data

This release contains source code and configuration only. Database and user data are created fresh on first run. To restore existing data, use a Guaardvark data backup.
