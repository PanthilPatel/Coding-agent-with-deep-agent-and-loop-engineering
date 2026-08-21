import os
import sys
import subprocess

examples_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "my-buggy-test-repo", "examples"))
python_exe = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "venv", "Scripts", "python.exe"))

subdirs = sorted([d for d in os.listdir(examples_dir) if os.path.isdir(os.path.join(examples_dir, d))])

for subdir in subdirs:
    full_path = os.path.join(examples_dir, subdir)
    print(f"\n==================================================")
    print(f"Running tests in {subdir}...")
    print(f"==================================================")
    
    # Run pytest inside the directory or by setting PYTHONPATH
    env = os.environ.copy()
    env["PYTHONPATH"] = full_path + os.pathsep + env.get("PYTHONPATH", "")
    
    res = subprocess.run(
        [python_exe, "-m", "pytest", full_path],
        env=env,
        capture_output=True,
        text=True
    )
    
    print(res.stdout)
    if res.stderr:
        print("STDERR:")
        print(res.stderr)
