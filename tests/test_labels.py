import os
import shutil
import subprocess

import pytest

from tools.fs import read_labels
from tools.host import configure_environment, find_tool
from tools.image.builder import MKE2FS_CONFIG

configure_environment()
MKFS_EROFS = shutil.which("mkfs.erofs")
MKE2FS = find_tool("mke2fs")
E2FSDROID = find_tool("e2fsdroid")


def _set_label(path, label):
    """Tags path with security.selinux; False if this host can't."""
    if hasattr(os, "setxattr"):
        try:
            os.setxattr(
                path, "security.selinux", label.encode() + b"\0", follow_symlinks=False
            )
            return True
        except OSError:
            return False
    # macOS keeps any xattr name for ordinary users.
    return (
        subprocess.run(
            ["xattr", "-s", "-w", "security.selinux", label, path], capture_output=True
        ).returncode
        == 0
    )


def _labelled_tree(src):
    """Builds a tree covering inline, shared and multi-block cases and
    returns the labels it was given."""
    labels = {"/": "u:object_r:rootfs:s0"}
    os.makedirs(src / "many")
    labels["/many"] = "u:object_r:system_file:s0"
    # Enough entries for a directory spanning several blocks, all sharing
    # one label, which mkfs.erofs moves to the shared xattr area.
    for i in range(300):
        (src / "many" / f"entry_{i:03}").write_bytes(b"x")
        labels[f"/many/entry_{i:03}"] = "u:object_r:system_file:s0"
    (src / "unique").write_bytes(b"y")
    labels["/unique"] = "u:object_r:unique_file:s0"
    (src / "файл").write_bytes(b"z")
    labels["/файл"] = "u:object_r:other_file:s0"
    os.symlink("unique", src / "link")
    labels["/link"] = "u:object_r:link_file:s0"
    for path, label in labels.items():
        if not _set_label(str(src) + ("" if path == "/" else path), label):
            pytest.skip("can't set security.selinux here")
    return labels


@pytest.mark.skipif(not MKFS_EROFS, reason="needs mkfs.erofs")
@pytest.mark.parametrize("options", [[], ["-Eforce-inode-extended"]])
def test_erofs_labels_match_source(tmp_path, options):
    src = tmp_path / "src"
    src.mkdir()
    labels = _labelled_tree(src)
    image = str(tmp_path / "test.img")
    subprocess.run(
        [MKFS_EROFS, "-T0", *options, image, str(src)], check=True, capture_output=True
    )

    assert read_labels(image, "erofs") == labels


@pytest.mark.skipif(not (MKE2FS and E2FSDROID), reason="needs mke2fs and e2fsdroid")
# 128-byte inodes have no room for xattrs, so labels go to an xattr block.
@pytest.mark.parametrize("inode_size", [256, 128])
def test_ext4_labels_match_file_contexts(tmp_path, inode_size):
    src = tmp_path / "src"
    os.makedirs(src / "d")
    (src / "d" / "plain").write_bytes(b"x")
    (src / "d" / "special").write_bytes(b"y")
    contexts = tmp_path / "file_contexts"
    contexts.write_text(
        "/ u:object_r:rootfs:s0\n"
        "/lost\\+found u:object_r:rootfs:s0\n"
        "/d(/.*)? u:object_r:system_file:s0\n"
        "/d/special u:object_r:special_file:s0\n"
    )
    image = str(tmp_path / "system.img")
    subprocess.run(
        [
            MKE2FS,
            "-q",
            "-O",
            "^has_journal",
            "-t",
            "ext4",
            "-b",
            "4096",
            "-I",
            str(inode_size),
            image,
            "2048",
        ],
        env=dict(os.environ, MKE2FS_CONFIG=MKE2FS_CONFIG),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [E2FSDROID, "-e", "-S", str(contexts), "-f", str(src), "-a", "/", image],
        check=True,
        capture_output=True,
    )

    labels = read_labels(image, "ext4")
    assert {
        path: labels.get(path) for path in ("/", "/d", "/d/plain", "/d/special")
    } == {
        "/": "u:object_r:rootfs:s0",
        "/d": "u:object_r:system_file:s0",
        "/d/plain": "u:object_r:system_file:s0",
        "/d/special": "u:object_r:special_file:s0",
    }
