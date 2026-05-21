from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path | None = None) -> Path | None:
    """Load simple KEY=VALUE pairs into os.environ if they are missing.

    The dashboards use this tiny loader instead of storing credentials in
    source code. Existing process variables win over values from `.env`, which
    lets Task Scheduler or the shell override local development settings.
    """
    env_path = Path(path) if path is not None else Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ[key] = value
    return env_path
