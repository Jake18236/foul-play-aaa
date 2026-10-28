"""Save source and dependency identity before changing the benchmark baseline."""

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import subprocess
import tarfile


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "benchmarks/baseline"
    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists():
        raise SystemExit("Baseline already frozen; choose a new baseline explicitly")
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    files = {}
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for name in sorted(set(names)):
            path = root / name
            if not name or name.startswith("benchmarks/") or not path.is_file():
                continue
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            archive.add(path, arcname=name)
    import poke_engine.poke_engine as native

    manifest = {
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "engine": version("poke-engine"),
        "native_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
        "source": files,
        "archive_sha256": hashlib.sha256(
            (output / "source.tar.gz").read_bytes()
        ).hexdigest(),
        "scope": "Current dirty checkout, before payoff-matrix and sampling changes",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
