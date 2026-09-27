"""
Filesystem detection and extraction package.
Supports ext2/3/4, EROFS, and F2FS.
"""

from typing import Optional
import os
import shutil

from .detect import detect_filesystem
from .erofs import extract_erofs
from .ext4 import extract_ext4
from .f2fs import extract_f2fs
from .labels import read_labels


def unpack_filesystem(
    image_path: str, output_dir: str, fs_type: Optional[str] = None, logger=None
) -> int:
    """
    Unpacks an ext4, erofs or f2fs image into output_dir (replacing it).
    Returns 0 on success, -1 on failure.
    """
    log = logger or print

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir, ignore_errors=True)

    if not fs_type or fs_type == "unknown":
        fs_type = detect_filesystem(image_path)

    if fs_type in ("ext2", "ext3", "ext4"):
        success = extract_ext4(image_path, output_dir, logger=log)
    elif fs_type == "erofs":
        success = extract_erofs(image_path, output_dir, logger=log)
    elif fs_type == "f2fs":
        success = extract_f2fs(image_path, output_dir, logger=log)
    else:
        log(f"Unsupported or unidentified filesystem: {image_path}")
        return -1

    if not success or not os.path.isdir(output_dir) or not os.listdir(output_dir):
        log(f"Unpacking failed or produced empty directory: {image_path}")
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)
        return -1

    return 0


__all__ = [
    "detect_filesystem",
    "unpack_filesystem",
    "extract_ext4",
    "extract_erofs",
    "extract_f2fs",
    "read_labels",
]
