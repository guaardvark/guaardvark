# Guaardvark Code-Only Backup - Installation Instructions

## Backup Information
- **Backup Date:** 2026-02-23 18:39:13
- **Version:** 5.0
- **Type:** Code-Only Backup (No Data)

## Important Notes

⚠️ **This backup contains source code and configuration files ONLY.**

**This backup does NOT include:**
- Database files
- User uploaded files
- Client logos
- Chat history
- Any runtime data

## Installation Steps

1. **Extract the backup:**
   ```bash
   unzip claude_code_install_20260223_183913.zip
   cd guaardvark_code_only_backup_20260223_183913
   ```

2. **Install system dependencies:**
   ```bash
   # Ubuntu/Debian
   sudo apt-get update
   sudo apt-get install -y python3 python3-venv python3-dev python3-pip nodejs npm redis-server
   
   # CentOS/RHEL
   sudo yum install -y python3 python3-pip nodejs npm redis
   ```

3. **Install Python dependencies:**
   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Install Node.js dependencies:**
   ```bash
   cd frontend
   npm install
   ```

5. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

6. **Initialize database:**
   ```bash
   cd backend
   python3 -m flask db upgrade  # If using Flask-Migrate
   # OR
   python3 -c "from backend.models import db; from backend.app import create_app; app = create_app(); app.app_context().push(); db.create_all()"
   ```

7. **Start the system:**
   ```bash
   chmod +x start.sh
   ./start.sh
   ```

8. **Access the application:**
   - Frontend: http://localhost:5173
   - Backend API: http://localhost:5000
   - Health Check: http://localhost:5000/api/health

## Troubleshooting

- If you encounter permission issues, run: `chmod +x *.sh`
- If Python dependencies fail, ensure you have Python 3.8+ and pip installed
- If Node.js dependencies fail, ensure you have Node.js 16+ and npm installed
- Database will be created automatically on first run if it doesn't exist

## Data Restoration

If you need to restore data:
1. Use a separate data backup (full, granular, or system backup)
2. OR start with a fresh installation and configure manually

## Support

For issues, check the logs in the `logs/` directory.
