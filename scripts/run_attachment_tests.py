import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
cmd = [sys.executable, "-m", "pytest", "tests/test_odata_attached_file.py", "tests/test_erp_attachments.py", "-q", "--tb=short"]
raise SystemExit(subprocess.run(cmd, cwd=root).returncode)
