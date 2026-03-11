
from datetime import datetime

OUTPUT_DIR = os.environ.get('GUAARDVARK_OUTPUT_DIR', os.path.join(os.environ.get('GUAARDVARK_ROOT', '.'), 'data', 'outputs'))
PROGRESS_DIR = Path(OUTPUT_DIR) / ".progress_jobs"
PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

PROCESS_ID = f"test_process_{uuid.uuid4().hex[:8]}"
JOB_DIR = PROGRESS_DIR / PROCESS_ID
JOB_DIR.mkdir(parents=True, exist_ok=True)
METADATA_FILE = JOB_DIR / "metadata.json"

print(f"Creating progress job: {PROCESS_ID}")
print(f"Writing to: {METADATA_FILE}")

def update_progress(progress, status="processing"):
    metadata = {
        "job_id": PROCESS_ID,
        "process_type": "test_process",
        "status": status,
        "progress": progress,
        "message": f"Test progress {progress}%",
        "timestamp": datetime.now().isoformat(),
        "last_update_utc": datetime.utcnow().isoformat() + "Z"
    }
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=4)
    print(f"Updated progress to {progress}%")

try:
    update_progress(0, "start")
    time.sleep(2)
    update_progress(25)
    time.sleep(2)
    update_progress(50)
    time.sleep(2)
    update_progress(75)
    time.sleep(2)
    update_progress(100, "complete")
    print("Process complete.")
except Exception as e:
    print(f"Error: {e}")
