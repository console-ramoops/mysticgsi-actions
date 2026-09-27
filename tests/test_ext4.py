import os
import subprocess

import pytest

from tools.fs.ext4 import extract_ext4
from tools.host import configure_environment, find_tool
from tools.image.builder import MKE2FS_CONFIG

configure_environment()
MKE2FS = find_tool("mke2fs")
E2FSDROID = find_tool("e2fsdroid")

pytestmark = pytest.mark.skipif(
    not (MKE2FS and E2FSDROID), reason="needs mke2fs and e2fsdroid"
)


def _populate(src):
    for d in range(30):
        os.makedirs(src / f"d{d}")
        for i in range(60):
            (src / f"d{d}" / f"f{i}").write_bytes(os.urandom(i * 7))
    (src / "big").write_bytes(os.urandom(3 * 1024 * 1024 + 5))
    os.symlink("d1/f1", src / "link")


def _tree(root):
    tree = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if os.path.islink(path):
                tree[rel] = ("link", os.readlink(path))
            else:
                with open(path, "rb") as f:
                    tree[rel] = ("file", f.read())
    return tree


# Small block groups spread inodes over many groups. The layouts cover
# 64-byte group descriptors with metadata_csum dirent tails, ext3-style
# block maps (1 KiB blocks push the big file into double-indirect blocks)
# and inline data in i_block plus the system.data xattr.
@pytest.mark.parametrize(
    "features,block_size",
    [
        ("^has_journal", 4096),
        ("^has_journal,64bit,metadata_csum", 4096),
        ("^has_journal,^extent", 1024),
        ("^has_journal,inline_data", 4096),
    ],
)
def test_extract_matches_source_across_block_groups(tmp_path, features, block_size):
    src = tmp_path / "src"
    _populate(src)
    image = str(tmp_path / "system.img")
    subprocess.run(
        [
            MKE2FS,
            "-q",
            "-O",
            features,
            "-t",
            "ext4",
            "-b",
            str(block_size),
            "-I",
            "256",
            "-N",
            "8192",
            "-g",
            "4096",
            image,
            "40000",
        ],
        env=dict(os.environ, MKE2FS_CONFIG=MKE2FS_CONFIG),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [E2FSDROID, "-e", "-f", str(src), "-a", "/", image],
        check=True,
        capture_output=True,
    )

    out = tmp_path / "out"
    assert extract_ext4(image, str(out))

    extracted = _tree(out)
    extracted.pop("lost+found", None)
    assert extracted == _tree(src)
