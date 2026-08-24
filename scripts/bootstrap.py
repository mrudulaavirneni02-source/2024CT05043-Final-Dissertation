from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path = ROOT) -> None:
    print(f"+ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


def main() -> None:
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")])

    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    run([str(venv_python), "-m", "pip", "install", "-r", str(ROOT / "backend" / "requirements.txt")])

    npm = "npm.cmd" if os.name == "nt" else "npm"
    run([npm, "install"], cwd=ROOT / "frontend")
    print("Bootstrap complete.")


if __name__ == "__main__":
    main()
