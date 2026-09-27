import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load_state_script():
    text = (ROOT / ".github" / "workflows" / "scheduler.yml").read_text(encoding="utf-8")
    step = text.split("- name: Load state from `state` branch", 1)[1].split("\n      - name:", 1)[0]
    block = step.split("        run: |\n", 1)[1]
    return "\n".join(line[10:] if line.startswith("          ") else line for line in block.splitlines())


def _bash_executable():
    candidates = []
    if os.name == "nt":
        candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    candidates.append(Path(shutil.which("bash") or ""))
    return next((str(path) for path in candidates if str(path) and path.is_file()), None)


def test_load_state_loop_tolerates_missing_last_file(tmp_path):
    bash = _bash_executable()
    if not bash:
        pytest.skip("bash is unavailable")
    script = _load_state_script()
    loop = re.search(r"for f in \$STATE_FILES; do\n.*?\n\s*done", script, flags=re.DOTALL)
    assert loop, "Load state STATE_FILES loop not found"

    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    state_dir.mkdir()
    workspace.mkdir()
    (state_dir / "present.json").write_text('{"ok": true}', encoding="utf-8")
    command = loop.group(0).replace("/tmp/state", state_dir.as_posix())
    env = {**os.environ, "STATE_FILES": "present.json missing-final.json"}
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", command],
        cwd=workspace, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (workspace / "present.json").read_text(encoding="utf-8") == '{"ok": true}'
    assert not (workspace / "missing-final.json").exists()
