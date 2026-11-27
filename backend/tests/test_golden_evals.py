import pathlib
import subprocess
import sys


def test_golden_evals_pass():
    root = pathlib.Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, str(root / "evals" / "run_golden.py")], capture_output=True, text=True,
                       env={"PYTHONPATH": str(root / "backend" / "src"), "PATH": ""}, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
