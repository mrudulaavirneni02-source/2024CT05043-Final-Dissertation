from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    env = dotenv_values(ROOT / ".env")
    base_url = str(env.get("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
    model = str(env.get("OLLAMA_MODEL", "llama3.1:8b"))
    with urllib.request.urlopen(f"{base_url}/api/tags", timeout=10) as response:
        tags = json.loads(response.read().decode("utf-8"))
    names = {item["name"] for item in tags.get("models", [])}
    print(f"Ollama reachable at {base_url}")
    print("Installed models:", ", ".join(sorted(names)) or "none")
    if model not in names:
        print(f"Missing configured model: {model}")
        print(f"Install with: ollama pull {model}")
    else:
        print(f"Configured model is ready: {model}")


if __name__ == "__main__":
    main()
