import bz2
import lzma
import os
import struct

import pytest
import zstandard

from tools.extractor import extract_firmware
from tools.extractor.formats import payload
from tools.extractor.formats import update_metadata_pb2 as pb

BLK = 4096
Op = pb.InstallOperation


def _write_payload(path, ops, minor_version=0, raw_ops=(), old_info=False):
    """ops: [(type, data, [(start_block, num_blocks)])]"""
    manifest = pb.DeltaArchiveManifest(minor_version=minor_version)
    part = manifest.partitions.add(partition_name="system")
    if old_info:
        part.old_partition_info.size = BLK
    blobs = b""
    for op_type, data, extents in ops:
        op = part.operations.add(
            type=op_type, data_offset=len(blobs), data_length=len(data)
        )
        for start, num in extents:
            op.dst_extents.add(start_block=start, num_blocks=num)
        blobs += data
    manifest_raw = manifest.SerializeToString() + b"".join(raw_ops)

    with open(path, "wb") as f:
        f.write(payload.PAYLOAD_MAGIC + struct.pack(">QQI", 2, len(manifest_raw), 0))
        f.write(manifest_raw + blobs)


# Partial updates (OnePlus/OPPO full OTAs) have a non-zero minor version.
@pytest.mark.parametrize("minor_version", [0, 9])
def test_full_payload_places_every_op(tmp_path, minor_version):
    a, b, c, d = (os.urandom(BLK) for _ in range(4))
    ops = [
        (Op.REPLACE, a, [(0, 1)]),
        (Op.REPLACE_BZ, bz2.compress(b + c), [(5, 1), (2, 1)]),
        (Op.ZERO, b"", [(3, 2)]),
        (Op.REPLACE_XZ, lzma.compress(d), [(6, 1)]),
        (
            Op.REPLACE,
            zstandard.ZstdCompressor(write_content_size=False).compress(a),
            [(1, 1)],
        ),
    ]
    _write_payload(tmp_path / "payload.bin", ops, minor_version)

    payload.extract_payload(str(tmp_path / "payload.bin"), str(tmp_path))

    zero = bytes(BLK)
    assert (tmp_path / "system.img").read_bytes() == a + a + c + zero + zero + b + d


def _partition_with_op_type(value):
    """Raw manifest.partitions entry whose single op has type `value`."""
    op = bytes([0x08, value])
    part = b"\x0a\x06system" + bytes([0x42, len(op)]) + op
    return bytes([0x6A, len(part)]) + part


@pytest.mark.parametrize(
    "kind, reason",
    [
        ("old_info", "system has source partition info"),
        ("diff_op", "SOURCE_COPY operation in system"),
        ("unknown_op", "Unknown operation type in system"),
    ],
)
def test_incremental_payload_fails_extraction(tmp_path, kind, reason):
    path = tmp_path / "payload.bin"
    if kind == "old_info":
        _write_payload(
            path, [(Op.REPLACE, bytes(BLK), [(0, 1)])], minor_version=8, old_info=True
        )
    elif kind == "diff_op":
        _write_payload(path, [(Op.SOURCE_COPY, b"", [(0, 1)])])
    else:
        _write_payload(path, [], raw_ops=[_partition_with_op_type(99)])

    messages = []
    rc = extract_firmware(str(path), str(tmp_path / "out"), logger=messages.append)

    assert rc == 1
    assert messages[-1].startswith("Firmware extraction failed")
    assert reason in messages[-1]
    assert not (tmp_path / "out" / "system.img").exists()
