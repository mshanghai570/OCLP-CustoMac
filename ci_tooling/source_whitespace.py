"""Scan complete tracked source files, including a clean CI checkout."""
import subprocess
from pathlib import Path


def violations(repository: Path):
    files = subprocess.run(["git", "ls-files", "-z"], cwd=repository, capture_output=True, check=True).stdout.split(b"\0")
    for filename in files:
        if not filename:
            continue
        path = repository / filename.decode("utf-8")
        if path.suffix not in {".py", ".m", ".command", ".sh", ".yml", ".yaml"}:
            continue
        for number, line in enumerate(path.read_bytes().splitlines(), 1):
            if line.endswith((b" ", b"\t")):
                yield f"{path.relative_to(repository)}:{number}: trailing whitespace"


if __name__ == "__main__":
    errors = list(violations(Path.cwd()))
    if errors:
        print("\n".join(errors))
    raise SystemExit(bool(errors))
