"""Samsung AP tar / tar.md5 extractor with LZ4 decompression."""

from typing import List, Optional, Set
import os
import shutil
import tarfile

try:
    import lz4.frame

    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False

from . import super as lp_super

MB = 1024 * 1024


def is_samsung_tar(file_path: str) -> bool:
    if not os.path.isfile(file_path):
        return False
    name = os.path.basename(file_path).lower()
    if not name.endswith((".tar.md5", ".tar")):
        return False
    try:
        with tarfile.open(file_path, "r") as tar:
            return any(n.endswith((".lz4", ".img")) for n in tar.getnames())
    except (tarfile.TarError, OSError):
        return False


def _image_name(member_name: str):
    """Map system.img.ext4.lz4 and friends to ("system.img", is_lz4)."""
    base = os.path.basename(member_name)
    is_lz4 = base.endswith(".lz4")
    if is_lz4:
        base = base[:-4]
    if base.endswith(".ext4"):
        base = base[:-5]
    if not base.endswith(".img"):
        base += ".img"
    return base, is_lz4


def _decompress_lz4(src_f, dst_f):
    # decompress_chunk may stop short of the input it's given (and drop the
    # rest), so let LZ4FrameFile manage the buffering. It also handles
    # images made of several concatenated frames.
    with lz4.frame.open(src_f, "rb") as lz4_f:
        shutil.copyfileobj(lz4_f, dst_f, MB)


def extract_samsung_tar(
    tar_path: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None,
) -> List[str]:
    if not HAS_LZ4:
        raise RuntimeError("lz4 package is required for Samsung firmware extraction")

    os.makedirs(output_dir, exist_ok=True)
    extracted: List[str] = []
    targets = {p.lower() for p in target_partitions} if target_partitions else None
    super_img_path: Optional[str] = None

    try:
        with tarfile.open(tar_path, "r") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue

                base, is_lz4 = _image_name(member.name)
                part_name = base[:-4].lower()

                # super.img is always needed: it holds the target partitions.
                if (
                    part_name != "super"
                    and targets is not None
                    and part_name not in targets
                ):
                    continue

                out_path = os.path.join(output_dir, base)
                if logger:
                    logger(
                        f"Extracting Samsung partition {base} "
                        f"({member.size // MB} MB)..."
                    )

                src_f = tar.extractfile(member)
                if src_f is None:
                    continue

                with src_f, open(out_path, "wb") as dst_f:
                    if is_lz4:
                        _decompress_lz4(src_f, dst_f)
                    else:
                        shutil.copyfileobj(src_f, dst_f, MB)

                if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                    if part_name == "super":
                        super_img_path = out_path
                    else:
                        extracted.append(out_path)

    except tarfile.TarError as e:
        if logger:
            logger(f"Tar error reading Samsung archive: {e}")

    if super_img_path and os.path.isfile(super_img_path):
        extracted.extend(
            lp_super.unpack_super(super_img_path, output_dir, target_partitions, logger)
        )
        try:
            os.remove(super_img_path)
        except OSError:
            pass

    return extracted
