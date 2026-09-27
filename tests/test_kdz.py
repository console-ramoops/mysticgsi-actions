import hashlib
import os
import struct
import zlib

import zstandard

from tools.extractor.formats import kdz

SECTOR = 4096
SYSTEM_LBA = 100


def _gpt(partitions):
    """UFS-style GPT (4096-byte sectors) listing (name, first_lba)."""
    header = kdz.GPT_SIGNATURE.ljust(72, b"\0")
    header += struct.pack("<QII", 2, len(partitions), 128)
    entries = b"".join(
        bytes(32)
        + struct.pack("<QQQ", lba, lba + 1000, 0)
        + name.encode("utf-16le").ljust(72, b"\0")
        for name, lba in partitions
    )
    return bytes(SECTOR) + header.ljust(SECTOR, b"\0") + entries


def _chunk(slice_name, data, addr, trim, compress=zlib.compress):
    payload = compress(data)
    name = f"{slice_name}_{addr}"
    header = kdz.DZ_CHUNK.pack(
        kdz.DZ_CHUNK_MAGIC,
        slice_name.encode(),
        name.encode(),
        len(data),
        len(payload),
        hashlib.md5(data).digest(),
        addr,
        trim,
        0,
        0,
        b"",
    )
    return header + payload


def test_kdz_places_dz_chunks_by_gpt_lba(tmp_path):
    first, second = os.urandom(2 * SECTOR), os.urandom(SECTOR)
    zstd = zstandard.ZstdCompressor(write_content_size=False).compress
    dz = kdz.DZ_MAGIC.ljust(kdz.DZ_HEADER_SIZE, b"\0") + b"".join(
        [
            _chunk("PrimaryGPT", _gpt([("system_a", SYSTEM_LBA)]), 0, 6),
            _chunk("system_a", second, SYSTEM_LBA + 5, 3, zstd),
            _chunk("system_a", first, SYSTEM_LBA + 1, 2),
            _chunk("system_b", os.urandom(SECTOR), 5000, 1),
        ]
    )
    record = kdz.KDZ_RECORD.pack(b"system.dz", len(dz), 1024)
    (tmp_path / "fw.kdz").write_bytes((kdz.KDZ_MAGIC + record).ljust(1024, b"\0") + dz)

    extracted = kdz.extract_kdz(
        str(tmp_path / "fw.kdz"), str(tmp_path), target_partitions={"system"}
    )

    assert extracted == [str(tmp_path / "system.img")]
    zero = bytes(SECTOR)
    assert (
        tmp_path / "system.img"
    ).read_bytes() == zero + first + zero * 2 + second + zero * 2
