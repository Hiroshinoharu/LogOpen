"""Collect installed runtime distribution notices into the packaged licence file."""
from importlib.metadata import distribution
from pathlib import Path


def collect(root):
    lines = ["LogOpen third-party software notices", "Versions from requirements.txt; original licences follow.", ""]
    for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if "==" not in line or line.lstrip().startswith("#"):
            continue
        name, version = line.strip().split("==", 1)
        package = distribution(name)
        if package.version != version:
            raise RuntimeError(f"Installed version differs from runtime lock: {name}")
        lines += ["=" * 72, f"{name} {version}"]
        for field in ("Author", "Author-email", "License-Expression", "License", "Home-page", "Project-URL"):
            for value in package.metadata.get_all(field, []):
                lines.append(f"{field}: {value}")
        for entry in sorted(package.files or [], key=str):
            # Licence texts shipped by the distribution, never arbitrary user files.
            if any(part.lower().startswith(("license", "licence", "copying", "notice")) for part in entry.parts):
                path = Path(package.locate_file(entry))
                if path.is_file() and path.stat().st_size < 2_000_000:
                    lines += [f"--- {entry} ---", path.read_text(encoding="utf-8", errors="replace")]
        lines.append("")
    return "\n".join(lines)


def write_notices(root):
    destination = root / "build" / "licenses" / "THIRD_PARTY_NOTICES.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(collect(root), encoding="utf-8")
    return destination


if __name__ == "__main__":
    print(write_notices(Path(__file__).resolve().parents[1]))
