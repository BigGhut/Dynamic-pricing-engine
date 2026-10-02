"""Start the API, the traffic simulator, and the dashboard.

The dashboard does not import the API. This script is what turns SIM_MODE on.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    env = os.environ.copy()
    env["SIM_MODE"] = "true"
    env["PYTHONPATH"] = str(ROOT)
    commands = [
        [sys.executable, "-m", "uvicorn", "src.api.main:app", "--host", "127.0.0.1", "--port", "8000"],
        [sys.executable, str(ROOT / "scripts" / "run_simulation.py")],
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "app" / "dashboard.py")],
    ]
    processes = [subprocess.Popen(command, cwd=ROOT, env=env) for command in commands]
    print("API http://127.0.0.1:8000  SIM_MODE=true")
    print("Dashboard http://127.0.0.1:8501")
    try:
        processes[-1].wait()
    except KeyboardInterrupt:
        print("\nОстановка.")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    main()
