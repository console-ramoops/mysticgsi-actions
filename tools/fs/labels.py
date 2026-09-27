"""
Reads the SELinux label of every file in an ext4, EROFS, or F2FS image straight
from its metadata, without extracting anything.
"""

from typing import Dict, Optional
import os
import stat
import struct

from .ext4 import Ext4Error, Ext4Filesystem
from .ext4.filesystem import ROOT_INODE
from .f2fs import F2FSError, F2FSFilesystem

EROFS_SUPERBLOCK_OFFSET = 1024
EROFS_MAGIC = 0xE0F5E1E2
# Incompat features that move metadata out of the places read here.
EROFS_UNSUPPORTED = {0x80: "48bit", 0x100: "metabox"}
EROFS_SLOT = 32
EROFS_LAYOUT_FLAT_PLAIN = 0
EROFS_LAYOUT_FLAT_INLINE = 2
EROFS_XATTR_INDEX_SECURITY = 6
EROFS_DIRENT = struct.Struct("<QHBB")


class LabelError(Exception):
    pass


class _ErofsInode:
    def __init__(self, nid, pos, raw):
        self.number = nid
        self.pos = pos
        fmt, icount, self.mode = struct.unpack_from("<HHH", raw)
        self.layout = (fmt >> 1) & 7
        if fmt & 1:
            self.inode_size = 64
            self.size, self.blkaddr = struct.unpack_from("<QI", raw, 8)
        else:
            self.inode_size = 32
            self.size, _, self.blkaddr = struct.unpack_from("<III", raw, 8)
        self.xattr_size = 12 + (icount - 1) * 4 if icount else 0

    @property
    def is_dir(self):
        return stat.S_ISDIR(self.mode)


class _Erofs:
    """The metadata of an EROFS image: inodes, directories and xattrs."""

    def __init__(self, path):
        self._file = open(path, "rb")
        try:
            sb = self._read(EROFS_SUPERBLOCK_OFFSET, 128)
            (magic,) = struct.unpack_from("<I", sb)
            if magic != EROFS_MAGIC:
                raise LabelError("not an EROFS image")
            (incompat,) = struct.unpack_from("<I", sb, 80)
            for bit, name in EROFS_UNSUPPORTED.items():
                if incompat & bit:
                    raise LabelError(f"unsupported EROFS feature: {name}")
            if sb[90]:
                raise LabelError("unsupported EROFS directory block size")
            self.block_size = 1 << sb[12]
            (self.root_nid,) = struct.unpack_from("<H", sb, 14)
            meta_blkaddr, xattr_blkaddr = struct.unpack_from("<II", sb, 40)
            self._meta = meta_blkaddr * self.block_size
            self._shared_xattrs = xattr_blkaddr * self.block_size
        except BaseException:
            self._file.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._file.close()

    def _read(self, offset, length):
        self._file.seek(offset)
        data = self._file.read(length)
        if len(data) != length:
            raise LabelError(
                f"image truncated: cannot read {length} bytes at offset {offset}"
            )
        return data

    def read_inode(self, nid):
        pos = self._meta + nid * EROFS_SLOT
        raw = self._read(pos, EROFS_SLOT)
        if raw[0] & 1:
            raw = self._read(pos, 64)
        return _ErofsInode(nid, pos, raw)

    @staticmethod
    def _entry(buf, pos):
        """(name index, name, value, entry length) of the xattr at pos."""
        name_len, index, value_size = struct.unpack_from("<BBH", buf, pos)
        name_end = pos + 4 + name_len
        value = buf[name_end : name_end + value_size]
        if len(value) != value_size:
            raise LabelError("corrupt xattr entry")
        return (
            index,
            buf[pos + 4 : name_end],
            value,
            (4 + name_len + value_size + 3) & ~3,
        )

    def selinux_label(self, inode):
        if not inode.xattr_size:
            return None
        area = self._read(inode.pos + inode.inode_size, inode.xattr_size)
        shared_count = area[4]
        entries = []
        for i in range(shared_count):
            (xattr_id,) = struct.unpack_from("<I", area, 12 + i * 4)
            pos = self._shared_xattrs + xattr_id * 4
            name_len, _, value_size = struct.unpack_from("<BBH", self._read(pos, 4))
            entries.append(self._entry(self._read(pos, 4 + name_len + value_size), 0))
        pos = 12 + shared_count * 4
        while pos + 4 <= len(area):
            entry = self._entry(area, pos)
            entries.append(entry)
            pos += entry[3]
        for index, name, value, _ in entries:
            if index == EROFS_XATTR_INDEX_SECURITY and name == b"selinux":
                return value.rstrip(b"\0")
        return None

    def _dir_data(self, inode):
        bs = self.block_size
        if inode.layout == EROFS_LAYOUT_FLAT_PLAIN:
            return self._read(inode.blkaddr * bs, inode.size)
        if inode.layout != EROFS_LAYOUT_FLAT_INLINE:
            raise LabelError(f"unsupported EROFS directory layout {inode.layout}")
        # All but the last block are at blkaddr; the last one follows the
        # inode and its xattrs.
        head = (-(-inode.size // bs) - 1) * bs
        data = self._read(inode.blkaddr * bs, head) if head else b""
        return data + self._read(
            inode.pos + inode.inode_size + inode.xattr_size, inode.size - head
        )

    def iter_dir(self, inode):
        """Yields (name bytes, nid), without "." and ".."."""
        data = self._dir_data(inode)
        for start in range(0, len(data), self.block_size):
            block = data[start : start + self.block_size]
            count = EROFS_DIRENT.unpack_from(block)[1] // EROFS_DIRENT.size
            dirents = [
                EROFS_DIRENT.unpack_from(block, i * EROFS_DIRENT.size)
                for i in range(count)
            ]
            for i, (nid, name_off, _, _) in enumerate(dirents):
                end = dirents[i + 1][1] if i + 1 < count else len(block)
                name = block[name_off:end].split(b"\0", 1)[0]
                if name not in (b".", b".."):
                    yield name, nid


def _walk(fs, root) -> Dict[str, str]:
    labels = {}
    visited = {root.number}
    pending = [("", root)]
    while pending:
        path, inode = pending.pop()
        label = fs.selinux_label(inode)
        if label:
            labels[path or "/"] = label.decode("ascii", "replace")
        if not inode.is_dir:
            continue
        for name, number in fs.iter_dir(inode):
            if b"/" in name or b"\0" in name or not name:
                raise LabelError("invalid file name")
            child = fs.read_inode(number)
            if child.is_dir:
                if number in visited:
                    raise LabelError("directory loop")
                visited.add(number)
            pending.append((f"{path}/{os.fsdecode(name)}", child))
    return labels


def _walk_f2fs(fs: F2FSFilesystem) -> Dict[str, str]:
    labels = {}
    visited = {fs.root_ino}
    pending = [("", fs.root_ino)]
    while pending:
        path, number = pending.pop()
        inode = fs.inode(number)
        label = fs.selinux_label(inode)
        if label:
            labels[path or "/"] = label.decode("ascii", "replace")
        if not stat.S_ISDIR(struct.unpack_from("<H", inode[0])[0]):
            continue
        for name, child_number in fs.directory(inode):
            if b"/" in name or b"\0" in name or not name:
                raise LabelError("invalid file name")
            child = fs.inode(child_number)
            if stat.S_ISDIR(struct.unpack_from("<H", child[0])[0]):
                if child_number in visited:
                    raise LabelError("directory loop")
                visited.add(child_number)
            pending.append((f"{path}/{os.fsdecode(name)}", child_number))
    return labels


def read_labels(image_path: str, fs_type: str, logger=None) -> Optional[Dict[str, str]]:
    """
    Maps every path in an ext4, EROFS, or F2FS image ("/" for its root) to its
    SELinux label. Returns None if the labels can't be read.
    """
    try:
        if fs_type in ("ext2", "ext3", "ext4"):
            with Ext4Filesystem(image_path) as fs:
                return _walk(fs, fs.read_inode(ROOT_INODE))
        if fs_type == "erofs":
            with _Erofs(image_path) as fs:
                return _walk(fs, fs.read_inode(fs.root_nid))
        if fs_type == "f2fs":
            with F2FSFilesystem(image_path) as fs:
                return _walk_f2fs(fs)
    except (LabelError, Ext4Error, F2FSError, OSError, struct.error) as e:
        if logger:
            logger(f"Could not read SELinux labels from {image_path}: {e}")
    return None
