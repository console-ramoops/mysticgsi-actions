import os
import struct

from tools.extractor.formats import qfil, sin, sparse

BLK = 4096


def _sparse_raw(data):
    header = struct.pack(
        "<I4H4I", sparse.SPARSE_HEADER_MAGIC, 1, 0, 28, 12, BLK, len(data) // BLK, 1, 0
    )
    chunk = struct.pack(
        "<2H2I", sparse.CHUNK_TYPE_RAW, 0, len(data) // BLK, 12 + len(data)
    )
    return header + chunk + data


def test_qfil_places_split_pieces_by_start_sector(tmp_path):
    piece0, piece1, other = (os.urandom(2 * BLK) for _ in range(3))
    (tmp_path / "system.img").write_bytes(_sparse_raw(piece0))
    (tmp_path / "system_2.img").write_bytes(piece1)
    (tmp_path / "vendor_b.img").write_bytes(other)
    (tmp_path / "rawprogram_unsparse0.xml").write_text("""<data>
      <program SECTOR_SIZE_IN_BYTES="4096" label="system_a"
               filename="images/system_2.img" start_sector="1004"/>
      <program SECTOR_SIZE_IN_BYTES="4096" label="system_a"
               filename="system.img" start_sector="1000"/>
      <program SECTOR_SIZE_IN_BYTES="4096" label="vendor_b"
               filename="vendor_b.img" start_sector="3000"/>
    </data>""")

    extracted = qfil.process_qfil(
        str(tmp_path), str(tmp_path), target_partitions={"system", "vendor"}
    )

    assert extracted == [str(tmp_path / "system.img")]
    assert (tmp_path / "system.img").read_bytes() == piece0 + bytes(2 * BLK) + piece1
    assert not (tmp_path / "system_2.img").exists()


def _ext4_like(size):
    superblock = struct.pack("<II16xI", 16, 64, 2).ljust(0x38, b"\0")
    superblock += sin.EXT4_MAGIC
    image = bytes(1024) + superblock
    return image + os.urandom(size - len(image))


def test_sin_skips_stray_ext4_magic_and_keeps_full_name(tmp_path):
    header = bytearray(5000)
    header[1100:1102] = sin.EXT4_MAGIC
    image = _ext4_like(3 * BLK)
    path = tmp_path / "system_ext_X-FLASH-ALL-C93B.sin"
    path.write_bytes(bytes(header) + image)

    out = sin.extract_sin(str(path), str(tmp_path))

    assert out == str(tmp_path / "system_ext.img")
    assert (tmp_path / "system_ext.img").read_bytes() == image
