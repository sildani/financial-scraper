import hashlib

def compute_md5(file_path: str) -> str:
    """Computes MD5 hash of a file to detect changes accurately."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
