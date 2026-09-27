"""Unisoc / Spreadtrum PAC package extractor."""

from typing import List, Optional, Set
import os
import struct

PAC_HEADER_FMT = "<44s I I 512s 512s I I I I I I I 200s I I I 800s I H H"
FILE_HEADER_FMT = "<I 512s 512s 504s I I I I I I I I 5I 996s"
PAC_MAGIC = 0xFFFAFFFA
# dwMagic follows the reserved block, just before the two CRC16s.
PAC_MAGIC_OFFSET = struct.calcsize(PAC_HEADER_FMT) - 8
PAC_PARTITION_COUNT = 5
PAC_PARTITIONS_START = 6
MB = 1024 * 1024


def _decode_utf16(raw: bytes) -> str:
    try:
        return raw.decode("utf-16le").rstrip("\x00")
    except UnicodeDecodeError:
        return raw.decode("latin1").rstrip("\x00")


def is_pac(file_path: str) -> bool:
    if not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            f.seek(PAC_MAGIC_OFFSET)
            return struct.unpack("<I", f.read(4))[0] == PAC_MAGIC
    except (OSError, struct.error):
        return False


def extract_pac(
    pac_path: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None,
) -> List[str]:
    if not is_pac(pac_path):
        return []

    os.makedirs(output_dir, exist_ok=True)
    extracted: List[str] = []
    targets = {p.lower() for p in target_partitions} if target_partitions else None

    pac_hdr_size = struct.calcsize(PAC_HEADER_FMT)
    file_hdr_size = struct.calcsize(FILE_HEADER_FMT)

    with open(pac_path, "rb") as f:
        hdr_data = f.read(pac_hdr_size)
        if len(hdr_data) < pac_hdr_size:
            return []

        header = struct.unpack(PAC_HEADER_FMT, hdr_data)
        entry_offset = header[PAC_PARTITIONS_START]

        for _ in range(header[PAC_PARTITION_COUNT]):
            f.seek(entry_offset)
            fh_data = f.read(file_hdr_size)
            if len(fh_data) < file_hdr_size:
                break

            fh = struct.unpack(FILE_HEADER_FMT, fh_data)
            entry_offset += fh[0] or file_hdr_size
            part_name = _decode_utf16(fh[1]).strip()
            file_name = _decode_utf16(fh[2]).strip() or part_name
            file_name = file_name.replace(" ", "_")
            file_size = (fh[4] << 32) + fh[5]
            data_offset = (fh[8] << 32) + fh[9]

            if file_size == 0:
                continue

            base_name = file_name.split(".")[0]
            if (
                targets is not None
                and base_name.lower() not in targets
                and part_name.lower() not in targets
            ):
                continue

            out_name = file_name
            if not out_name.endswith((".img", ".bin")):
                out_name += ".img"

            out_path = os.path.join(output_dir, out_name)
            if logger:
                logger(f"Extracting PAC file {out_name} ({file_size // MB} MB)...")

            f.seek(data_offset)
            with open(out_path, "wb") as out_f:
                remaining = file_size
                while remaining > 0:
                    chunk = f.read(min(remaining, MB))
                    if not chunk:
                        break
                    out_f.write(chunk)
                    remaining -= len(chunk)

            if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                extracted.append(out_path)

    return extracted
