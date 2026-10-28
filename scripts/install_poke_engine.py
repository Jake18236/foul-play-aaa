"""Build the pinned native engine with the project's verified mechanics fixes."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

from engine_search_patch import patch_search
from engine_format_patch import patch_format_rules
from engine_neural_patch import patch_neural


SOURCE_URL = (
    "https://files.pythonhosted.org/packages/cf/69/47d572f526943c519927acff95685bffa1aba7024f8433bc1924d7941ae2/"
    "poke_engine-0.0.48.tar.gz"
)
SOURCE_SHA256 = "070010686f2aedff11e25137e696e301ccd80fd57c805d255464067fc905ca12"
PATCH_VERSION = "0.0.48+foulplay.6"


def patch_source(source: Path):
    damage_calc = source / "src/genx/damage_calc.rs"
    text = damage_calc.read_text()
    marker = """        _ => panic!("Can only calculate damage for physical or special moves"),
    }
"""
    if text.count(marker) != 1:
        raise ValueError("Pinned engine source does not match the Unaware patch")
    # Unaware ignores boosts for both normal hits and critical hits. The normal
    # stats already account for Body Press, Foul Play, Psyshock, and ability bypass.
    text = text.replace(
        marker,
        marker
        + """
    // A critical hit does not bypass Unaware's boost suppression.
    if !should_calc_attacker_boost {
        crit_attacking_stat = attacking_final_stat;
    }
    if !should_calc_defender_boost {
        crit_defending_stat = defending_final_stat;
    }
""",
    )
    damage_calc.write_text(text)
    patch_search(source)
    patch_format_rules(source)
    patch_neural(source)
    # Expose the patch revision in installed Python package metadata. The local
    # version still satisfies requirements.txt's ==0.0.48 constraint (PEP 440).
    project = source / "pyproject.toml"
    text = project.read_text()
    if text.count('version = "0.0.48"') != 1:
        raise ValueError("Unexpected pinned engine package version")
    project.write_text(
        text.replace('version = "0.0.48"', f'version = "{PATCH_VERSION}"')
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--feature",
        default="terastallization",
        choices=[
            "terastallization",
            *[f"gen{i}" for i in range(1, 10)],
            "champions",
            "bss",
        ],
    )
    parser.add_argument("--target", type=Path, help="Optional pip installation target")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="foul-play-engine-") as work:
        work = Path(work)
        archive = work / "engine.tar.gz"
        with urlopen(SOURCE_URL, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
            raise ValueError("Engine source checksum mismatch; refusing to build")
        archive.write_bytes(data)
        with tarfile.open(archive) as package:
            package.extractall(work, filter="data")
        source = work / "poke_engine-0.0.48"
        patch_source(source)
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--force-reinstall",
            str(source),
            f"--config-settings=build-args=--features poke-engine/{args.feature} --no-default-features",
        ]
        if args.target:
            command.extend(["--target", str(args.target), "--upgrade"])
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
