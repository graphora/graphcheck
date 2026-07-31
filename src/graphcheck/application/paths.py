from pathlib import Path


def project_path(root: Path, configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else root / path
