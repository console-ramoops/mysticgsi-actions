"""
LG KDZ and DZ package extractor.

Layouts follow kdztools (libexec/dz.py, libexec/kdz.py, undz.py).
"""

from typing import Dict, List, Optional, Set, Tuple
import hashlib
import os
import struct
import zlib

try:
    import zstandard

    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

KDZ_MAGIC = b"\x28\x05\x00\x00\x24\x38\x22\x25"
KDZ_RECORD = struct.Struct("<256sQQ")

DZ_MAGIC = b"\x32\x96\x18\x74"
DZ_CHUNK_MAGIC = b"\x30\x12\x95\x78"
DZ_HEADER_SIZE = 512
# magic, slice name, chunk name, target size, data size, md5, target addr,
# trim count, device, crc32; padded to 512 bytes.
DZ_CHUNK = struct.Struct("<4s32s64sII16sIIII372s")
ZLIB_MAGIC = b"\x78"
GPT_SIGNATURE = b"EFI PART"
MB = 1024 * 1024


class DzChunk:
    def __init__(self, header: bytes, data_offset: int):
        (
            _,
            slice_name,
            chunk_name,
            self.target_size,
            self.data_size,
            self.md5,
            self.target_addr,
            self.trim_count,
            self.dev,
            _,
            _,
        ) = DZ_CHUNK.unpack(header)
        self.slice_name = _decode_name(slice_name)
        self.chunk_name = _decode_name(chunk_name)
        self.data_offset = data_offset


def _read_magic(file_path: str, size: int) -> Optional[bytes]:
    if not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            return f.read(size)
    except OSError:
        return None


def is_kdz(file_path: str) -> bool:
    return _read_magic(file_path, 8) == KDZ_MAGIC


def is_dz(file_path: str) -> bool:
    return _read_magic(file_path, 4) == DZ_MAGIC


def _decode_name(raw: bytes) -> str:
    return raw.decode("latin1", errors="ignore").rstrip("\x00").strip()


def _read_chunks(f, start: int, end: int) -> List[DzChunk]:
    f.seek(start)
    if f.read(4) != DZ_MAGIC:
        return []
    chunks = []
    pos = start + DZ_HEADER_SIZE
    while pos + DZ_CHUNK.size <= end:
        f.seek(pos)
        header = f.read(DZ_CHUNK.size)
        if header[:4] != DZ_CHUNK_MAGIC:
            break
        chunk = DzChunk(header, pos + DZ_CHUNK.size)
        chunks.append(chunk)
        pos = chunk.data_offset + chunk.data_size
    return chunks


def _decompress(f, chunk: DzChunk) -> bytes:
    f.seek(chunk.data_offset)
    data = f.read(chunk.data_size)
    if data.startswith(ZLIB_MAGIC):
        out = zlib.decompress(data)
    elif HAS_ZSTD:
        # G7 and newer compress chunks with zstd instead of zlib.
        out = zstandard.ZstdDecompressor().decompressobj().decompress(data)
    else:
        raise RuntimeError("zstandard package required for this DZ file")
    if hashlib.md5(out).digest() != chunk.md5:
        raise RuntimeError(f"DZ chunk {chunk.chunk_name} failed MD5 check")
    return out


def _gpt_layout(f, chunks: List[DzChunk]) -> Tuple[int, Dict[str, int]]:
    """
    Reads the primary GPT: returns the sector shift (512-byte sectors on
    eMMC, 4096 on UFS) and each partition's first LBA.
    """
    gpt_chunk = next((c for c in chunks if c.slice_name.startswith("PrimaryGPT")), None)
    if gpt_chunk is None:
        return 9, {}
    gpt = _decompress(f, gpt_chunk)

    for shift in (9, 12):
        header = gpt[1 << shift : (1 << shift) + 92]
        if header[:8] == GPT_SIGNATURE:
            break
    else:
        return 9, {}

    entries_lba, count, entry_size = struct.unpack("<QII", header[72:88])
    starts = {}
    for i in range(count):
        pos = (entries_lba << shift) + i * entry_size
        entry = gpt[pos : pos + 128]
        if len(entry) < 128:
            break
        first_lba = struct.unpack("<Q", entry[32:40])[0]
        name = entry[56:128].decode("utf-16le", errors="ignore")
        name = name.rstrip("\x00")
        if name:
            starts[name] = first_lba
    return shift, starts


def _slice_key(chunk: DzChunk) -> Optional[str]:
    name = chunk.slice_name.lower()
    if chunk.dev or name.endswith("_b"):
        return None
    return name[:-2] if name.endswith("_a") else name


def _write_slice(f, chunks: List[DzChunk], shift: int, start_lba: int, out_path: str):
    base = start_lba << shift
    end = 0
    with open(out_path, "wb") as out_f:
        for chunk in sorted(chunks, key=lambda c: c.target_addr):
            offset = (chunk.target_addr << shift) - base
            data = _decompress(f, chunk)
            out_f.seek(offset)
            out_f.write(data)
            # trim_count covers the chunk's whole target area, of which the
            # tail past the data reads back as zeros.
            end = max(end, offset + len(data), offset + (chunk.trim_count << shift))
        out_f.truncate(end)


def _extract_dz_range(
    f,
    start: int,
    end: int,
    output_dir: str,
    target_partitions: Optional[Set[str]],
    logger,
) -> List[str]:
    chunks = _read_chunks(f, start, end)
    if not chunks:
        return []

    targets = {p.lower() for p in target_partitions} if target_partitions else None
    slices: Dict[str, List[DzChunk]] = {}
    for chunk in chunks:
        key = _slice_key(chunk)
        if key and (targets is None or key in targets):
            slices.setdefault(key, []).append(chunk)
    if not slices:
        return []

    shift, gpt_starts = _gpt_layout(f, chunks)
    extracted = []
    for key, slice_chunks in slices.items():
        slice_name = slice_chunks[0].slice_name
        first_addr = min(c.target_addr for c in slice_chunks)
        start_lba = min(gpt_starts.get(slice_name, first_addr), first_addr)
        if logger:
            size = sum(c.target_size for c in slice_chunks) // MB
            logger(f"Extracting DZ partition {key} ({size} MB)...")
        out_path = os.path.join(output_dir, f"{key}.img")
        _write_slice(f, slice_chunks, shift, start_lba, out_path)
        extracted.append(out_path)
    return extracted


def extract_dz(
    dz_path: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None,
) -> List[str]:
    if not is_dz(dz_path):
        return []
    os.makedirs(output_dir, exist_ok=True)
    with open(dz_path, "rb") as f:
        return _extract_dz_range(
            f, 0, os.path.getsize(dz_path), output_dir, target_partitions, logger
        )


def _kdz_records(f, total_size: int) -> List[Tuple[str, int, int]]:
    records = []
    f.seek(len(KDZ_MAGIC))
    while f.tell() + KDZ_RECORD.size <= total_size:
        name_raw, length, offset = KDZ_RECORD.unpack(f.read(KDZ_RECORD.size))
        name = _decode_name(name_raw)
        if not name or offset + length > total_size:
            break
        records.append((name, offset, length))
    return records


def extract_kdz(
    kdz_path: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None,
) -> List[str]:
    if not is_kdz(kdz_path):
        return []
    os.makedirs(output_dir, exist_ok=True)

    extracted: List[str] = []
    total_size = os.path.getsize(kdz_path)
    with open(kdz_path, "rb") as f:
        for name, offset, length in _kdz_records(f, total_size):
            if name.endswith(".dz"):
                extracted += _extract_dz_range(
                    f, offset, offset + length, output_dir, target_partitions, logger
                )
    return extracted
