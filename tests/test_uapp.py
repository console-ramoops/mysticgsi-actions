import os
import struct
import zipfile

from tools.extractor import extract_firmware
from tools.extractor.formats import uapp

HEADER_SIZE = 98


def _update_app(entries):
    """UPDATE.APP bytes laid out like Huawei's: zero padding, then one
    98-byte header per (name, data) entry, each 4-byte aligned."""
    out = bytearray(92)
    for name, data in entries:
        header = (
            uapp.UAPP_MAGIC
            + struct.pack("<IIQII", HEADER_SIZE, 1, 0, 0, len(data))
            + b"\0" * 32
            + name.encode().ljust(16, b"\0")
        )
        out += header.ljust(HEADER_SIZE, b"\0") + data
        out += b"\0" * (-len(out) % uapp.ALIGNMENT)
    return bytes(out)


def test_repeated_entries_become_sparse_chunks(tmp_path):
    chunks = [os.urandom(5), os.urandom(7)]
    system = os.urandom(9)
    path = tmp_path / "UPDATE.APP"
    path.write_bytes(
        _update_app(
            [
                ("SUPER", chunks[0]),
                ("CUST", b"skipped"),
                ("SYSTEM", system),
                ("SUPER", chunks[1]),
            ]
        )
    )
    out = tmp_path / "out"

    assert uapp.is_uapp(str(path))
    uapp.extract_uapp(str(path), str(out), target_partitions={"system"})

    assert sorted(os.listdir(out)) == [
        "super.img_sparsechunk.0",
        "super.img_sparsechunk.1",
        "system.img",
    ]
    assert (out / "super.img_sparsechunk.0").read_bytes() == chunks[0]
    assert (out / "super.img_sparsechunk.1").read_bytes() == chunks[1]
    assert (out / "system.img").read_bytes() == system


def test_update_app_in_a_nested_zip_is_found(tmp_path):
    system = os.urandom(4096)
    inner = tmp_path / "update_sd_base.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("UPDATE.APP", _update_app([("SYSTEM", system)]))
    outer = tmp_path / "firmware.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "NEL-AN00/dload/update_sd_base.zip")

    out = tmp_path / "out"
    assert extract_firmware(str(outer), str(out), logger=lambda m: None) == 0
    assert (out / "system.img").read_bytes() == system
