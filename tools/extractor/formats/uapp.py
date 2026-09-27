"""Huawei UPDATE.APP extractor."""

from typing import List, Optional, Set
import os
import struct

UAPP_MAGIC = b"\x55\xaa\x5a\xa5"
ALIGNMENT = 4
# Huawei pads the start of UPDATE.APP with zeros (92 bytes in practice).
MAX_LEADING_PADDING = 512
# magic, header size, unk1, hw_id, seq, size, date, time, type
FIXED_HEADER_SIZE = 4 + 4 + 20 + 48
MB = 1024 * 1024


def is_uapp(file_path: str) -> bool:
    if not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            head = f.read(MAX_LEADING_PADDING + len(UAPP_MAGIC))
    except OSError:
        return False
    start = len(head) - len(head.lstrip(b"\0"))
    start -= start % ALIGNMENT
    return head[start : start + len(UAPP_MAGIC)] == UAPP_MAGIC


def extract_uapp(
    uapp_path: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None,
) -> List[str]:
    if not os.path.isfile(uapp_path):
        return []

    os.makedirs(output_dir, exist_ok=True)
    extracted: List[str] = []
    targets = {p.lower() for p in target_partitions} if target_partitions else None
    # Huawei splits big sparse images (SUPER) into several entries of the
    # same name; postprocess merges <name>.img_sparsechunk.N back together.
    pieces = {}

    with open(uapp_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        total_size = f.tell()
        f.seek(0)

        while f.tell() < total_size:
            magic = f.read(4)
            if len(magic) < 4:
                break
            if magic != UAPP_MAGIC:
                continue

            hdr_sz_data = f.read(4)
            if len(hdr_sz_data) < 4:
                break
            hdr_sz = struct.unpack("<I", hdr_sz_data)[0]

            hdr_meta = f.read(20)
            if len(hdr_meta) < 20:
                break
            _, _, _, part_size = struct.unpack("<IQII", hdr_meta)

            f.seek(32, os.SEEK_CUR)
            type_raw = f.read(16)
            part_name = (
                type_raw.decode("latin1", errors="ignore").strip("\x00").strip().lower()
            )

            if hdr_sz > FIXED_HEADER_SIZE:
                f.seek(hdr_sz - FIXED_HEADER_SIZE, os.SEEK_CUR)

            if part_size > 0:
                if (
                    targets is not None
                    and part_name not in targets
                    and part_name != "super"
                ):
                    f.seek(part_size, os.SEEK_CUR)
                else:
                    count = pieces.get(part_name, 0)
                    pieces[part_name] = count + 1
                    out_path = os.path.join(output_dir, f"{part_name}.img")
                    if count == 1:
                        first = f"{out_path}_sparsechunk.0"
                        os.replace(out_path, first)
                        extracted = [first if p == out_path else p for p in extracted]
                    if count:
                        out_path = f"{out_path}_sparsechunk.{count}"
                    if logger:
                        logger(
                            f"Extracting UPDATE.APP partition {part_name} "
                            f"({part_size // MB} MB)..."
                        )

                    with open(out_path, "wb") as out_f:
                        remaining = part_size
                        while remaining > 0:
                            chunk = f.read(min(remaining, MB))
                            if not chunk:
                                break
                            out_f.write(chunk)
                            remaining -= len(chunk)

                    if os.path.getsize(out_path) > 0:
                        extracted.append(out_path)

            rem = (ALIGNMENT - (f.tell() % ALIGNMENT)) % ALIGNMENT
            if rem > 0:
                f.seek(rem, os.SEEK_CUR)

    return extracted
