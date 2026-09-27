import json
import os
import struct

import pytest

from tools.fs import read_labels
from tools.fs.ext4 import Ext4Filesystem
from tools.host import configure_environment, find_tool
from tools.image import build_system_image

configure_environment()
pytestmark = pytest.mark.skipif(
    not (find_tool("mke2fs") and find_tool("e2fsdroid")),
    reason="needs mke2fs and e2fsdroid",
)


def _fs_config_entry(prefix, mode, uid, gid):
    raw = prefix.encode() + b"\0"
    raw += b"\0" * (-(16 + len(raw)) % 8)
    return struct.pack("<HHHHQ", 16 + len(raw), mode, uid, gid, 0) + raw


def test_image_gets_stock_owners_and_modes(tmp_path):
    src = tmp_path / "src"
    for path in ("system/bin/sh", "system/product/bin/tool"):
        os.makedirs(src / os.path.dirname(path), exist_ok=True)
        (src / path).write_bytes(b"x")
        os.chmod(src / path, 0o644)
    os.makedirs(src / "system/product/etc")
    (src / "system/product/etc/fs_config_files").write_bytes(
        _fs_config_entry("product/bin/tool", 0o750, 1000, 1001)
    )

    image = tmp_path / "system.img"
    assert (
        build_system_image(
            str(src),
            str(image),
            16 << 20,
            staging_dir=str(tmp_path / "st"),
            logger=lambda m: None,
        )
        == 0
    )

    with Ext4Filesystem(str(image)) as fs:
        sh = fs.stat("system/bin/sh")
        tool = fs.stat("system/product/bin/tool")
    assert (sh.mode & 0o7777, sh.uid, sh.gid) == (0o755, 0, 2000)
    assert (tool.mode & 0o7777, tool.uid, tool.gid) == (0o750, 1000, 1001)


def test_stock_labels_fill_only_the_rom_rules_gaps(tmp_path):
    src = tmp_path / "src"
    os.makedirs(src / "system/bin")
    os.makedirs(src / "system/etc/selinux")
    (src / "system/etc/selinux/plat_file_contexts").write_text(
        "/ u:object_r:rootfs:s0\n"
        "/lost\\+found u:object_r:rootfs:s0\n"
        "/cache u:object_r:cache_file:s0\n"
        "/system(/.*)? u:object_r:system_file:s0\n"
        "/system/bin/sh u:object_r:shell_exec:s0\n"
    )
    for path in ("system/bin/sh", ".marker", "odd name (1).bin", "файл"):
        (src / path).write_bytes(b"x")
    stock = {
        "/system/bin/sh": "u:object_r:stock_sh:s0",
        "/.marker": "u:object_r:marker_file:s0",
        "/odd name (1).bin": "u:object_r:odd_file:s0",
        "/файл": "u:object_r:named_file:s0",
        "/gone": "u:object_r:gone_file:s0",
    }
    stock_path = tmp_path / "stock_labels.json"
    stock_path.write_text(json.dumps(stock))

    image = tmp_path / "system.img"
    assert (
        build_system_image(
            str(src),
            str(image),
            16 << 20,
            staging_dir=str(tmp_path / "st"),
            stock_labels_path=str(stock_path),
            logger=lambda m: None,
        )
        == 0
    )

    labels = read_labels(str(image), "ext4")
    assert labels["/system/bin/sh"] == "u:object_r:shell_exec:s0"
    assert labels["/.marker"] == "u:object_r:marker_file:s0"
    assert labels["/odd name (1).bin"] == "u:object_r:odd_file:s0"
    assert labels["/файл"] == "u:object_r:named_file:s0"
    assert "/gone" not in labels
