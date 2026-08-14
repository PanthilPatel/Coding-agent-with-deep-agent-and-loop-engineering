import subprocess

def run():
    # run pytest via venv python
    try:
        result = subprocess.run(["/venv/Scripts/python.exe", "-m", "pytest", "/test_agent.py"], capture_output=True, text=True)
        print(result.stdout)
        print(result.stderr)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run()
