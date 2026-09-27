"""
Read-only access to ext2/3/4 filesystem images.
"""

import os
import stat
import struct

SUPERBLOCK_OFFSET = 1024
SUPERBLOCK_MAGIC = 0xEF53
ROOT_INODE = 2
COPY_CHUNK = 1 << 20

COMPAT_SPARSE_SUPER2 = 0x200

INCOMPAT_FILETYPE = 0x2
INCOMPAT_META_BG = 0x10
INCOMPAT_64BIT = 0x80
_INCOMPAT_SUPPORTED = (
    INCOMPAT_FILETYPE
    | 0x4  # recover: a pending journal replay only affects recent writes
    | INCOMPAT_META_BG
    | 0x40  # extents
    | INCOMPAT_64BIT
    | 0x100  # mmp
    | 0x200  # flex_bg
    | 0x400  # ea_inode
    | 0x2000  # csum_seed
    | 0x4000  # largedir
    | 0x8000  # inline_data
    | 0x20000  # casefold
)
_INCOMPAT_NAMES = {
    0x1: "compression",
    0x8: "journal_dev",
    0x1000: "dirdata",
    0x10000: "encrypt",
}

RO_COMPAT_SPARSE_SUPER = 0x1
_RO_COMPAT_UNSUPPORTED = {
    0x200: "bigalloc",
}

EXTENTS_FL = 0x80000
INLINE_DATA_FL = 0x10000000

EXTENT_MAGIC = 0xF30A
EXTENT_MAX_DEPTH = 5
EXTENT_UNINIT_BASE = 32768

XATTR_MAGIC = 0xEA020000
XATTR_BLOCK_HEADER = 32
XATTR_INDEX_SECURITY = 6
XATTR_INDEX_SYSTEM = 7


class Ext4Error(Exception):
    pass


def _decode_time(seconds, extra):
    # The low two bits of *_extra extend the signed 32-bit seconds to 34
    # bits; the other 30 bits are nanoseconds.
    return (seconds + ((extra & 3) << 32)) * 1_000_000_000 + (extra >> 2)


class Inode:
    """A parsed on-disk inode. stat() returns one of these."""

    def __init__(self, number, raw):
        self.number = number
        self.raw = raw
        (self.mode, uid_lo, size_lo, atime, _, mtime, _, gid_lo, _, _, self.flags) = (
            struct.unpack_from("<HHIiiiIHHII", raw)
        )
        self.i_block = raw[0x28:0x64]
        (file_acl_lo,) = struct.unpack_from("<I", raw, 0x68)
        (size_hi,) = struct.unpack_from("<I", raw, 0x6C)
        file_acl_hi, uid_hi, gid_hi = struct.unpack_from("<HHH", raw, 0x76)
        self.file_acl = file_acl_lo | file_acl_hi << 32
        self.size = size_lo | size_hi << 32
        self.uid = uid_lo | uid_hi << 16
        self.gid = gid_lo | gid_hi << 16

        extra_isize = 0
        if len(raw) > 0x82:
            (extra_isize,) = struct.unpack_from("<H", raw, 0x80)
            if 128 + extra_isize > len(raw):
                extra_isize = 0
        self.extra_isize = extra_isize
        mtime_extra = atime_extra = 0
        if extra_isize >= 0x8C - 128:
            (mtime_extra,) = struct.unpack_from("<I", raw, 0x88)
        if extra_isize >= 0x90 - 128:
            (atime_extra,) = struct.unpack_from("<I", raw, 0x8C)
        self.atime_ns = _decode_time(atime, atime_extra)
        self.mtime_ns = _decode_time(mtime, mtime_extra)

    @property
    def atime(self):
        return self.atime_ns / 1e9

    @property
    def mtime(self):
        return self.mtime_ns / 1e9

    @property
    def is_dir(self):
        return stat.S_ISDIR(self.mode)

    @property
    def is_file(self):
        return stat.S_ISREG(self.mode)

    @property
    def is_symlink(self):
        return stat.S_ISLNK(self.mode)


class Ext4Filesystem:
    """A raw (non-sparse) ext2/3/4 image opened read-only."""

    def __init__(self, path):
        self._file = open(path, "rb")
        self._inode_tables = {}
        try:
            self._load_superblock()
        except BaseException:
            self._file.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._file.close()

    def _read(self, offset, length):
        self._file.seek(offset)
        data = self._file.read(length)
        if len(data) != length:
            raise Ext4Error(
                f"image truncated: cannot read {length} bytes at offset {offset}"
            )
        return data

    def _read_block(self, block):
        return self._read(block * self.block_size, self.block_size)

    def _load_superblock(self):
        sb = self._read(SUPERBLOCK_OFFSET, 1024)
        (magic,) = struct.unpack_from("<H", sb, 0x38)
        if magic != SUPERBLOCK_MAGIC:
            raise Ext4Error("not an ext2/3/4 filesystem (bad magic)")

        (
            self.inodes_count,
            blocks_lo,
            _,
            _,
            _,
            self.first_data_block,
            log_block_size,
            _,
            self.blocks_per_group,
            _,
            self.inodes_per_group,
        ) = struct.unpack_from("<11I", sb)
        if log_block_size > 6:
            raise Ext4Error(f"invalid block size 2^{10 + log_block_size}")
        self.block_size = 1024 << log_block_size
        if not self.blocks_per_group or not self.inodes_per_group:
            raise Ext4Error("invalid superblock geometry")

        (rev_level,) = struct.unpack_from("<I", sb, 0x4C)
        if rev_level >= 1:
            (self.inode_size,) = struct.unpack_from("<H", sb, 0x58)
            self.compat, self.incompat, self.ro_compat = struct.unpack_from(
                "<3I", sb, 0x5C
            )
        else:
            self.inode_size = 128
            self.compat = self.incompat = self.ro_compat = 0
        if (
            self.inode_size < 128
            or self.inode_size > self.block_size
            or self.inode_size & (self.inode_size - 1)
        ):
            raise Ext4Error(f"invalid inode size {self.inode_size}")

        self._check_features()

        self.desc_size = 32
        if self.incompat & INCOMPAT_64BIT:
            (self.desc_size,) = struct.unpack_from("<H", sb, 0xFE)
            if (
                self.desc_size < 32
                or self.desc_size > self.block_size
                or self.desc_size & (self.desc_size - 1)
            ):
                raise Ext4Error(f"invalid group descriptor size {self.desc_size}")
        (self.first_meta_bg,) = struct.unpack_from("<I", sb, 0x104)
        blocks_hi = 0
        if self.incompat & INCOMPAT_64BIT:
            (blocks_hi,) = struct.unpack_from("<I", sb, 0x150)
        self.blocks_count = blocks_lo | blocks_hi << 32
        self.backup_bgs = struct.unpack_from("<2I", sb, 0x24C)
        self.group_count = -(
            -(self.blocks_count - self.first_data_block) // self.blocks_per_group
        )

    def _check_features(self):
        unknown = self.incompat & ~_INCOMPAT_SUPPORTED
        if unknown:
            names = [name for bit, name in _INCOMPAT_NAMES.items() if unknown & bit]
            leftover = unknown & ~sum(_INCOMPAT_NAMES)
            if leftover:
                names.append(f"incompat 0x{leftover:x}")
            raise Ext4Error(f"unsupported ext4 features: {', '.join(names)}")
        for bit, name in _RO_COMPAT_UNSUPPORTED.items():
            if self.ro_compat & bit:
                raise Ext4Error(f"unsupported ext4 feature: {name}")

    def _has_super(self, group):
        if group == 0:
            return True
        if self.compat & COMPAT_SPARSE_SUPER2:
            return group in self.backup_bgs
        if not self.ro_compat & RO_COMPAT_SPARSE_SUPER or group == 1:
            return True
        for base in (3, 5, 7):
            n = base
            while n < group:
                n *= base
            if n == group:
                return True
        return False

    def _inode_table(self, group):
        table = self._inode_tables.get(group)
        if table is not None:
            return table
        per_block = self.block_size // self.desc_size
        meta_group, index = divmod(group, per_block)
        if self.incompat & INCOMPAT_META_BG and meta_group >= self.first_meta_bg:
            # meta_bg keeps each descriptor block in the first group of
            # the meta group it describes, right after any superblock.
            first = meta_group * per_block
            block = (
                first * self.blocks_per_group
                + self.first_data_block
                + self._has_super(first)
            )
        else:
            block = self.first_data_block + 1 + meta_group
        desc = self._read(
            block * self.block_size + index * self.desc_size, self.desc_size
        )
        (table,) = struct.unpack_from("<I", desc, 0x8)
        if self.desc_size >= 64:
            table |= struct.unpack_from("<I", desc, 0x28)[0] << 32
        self._inode_tables[group] = table
        return table

    def read_inode(self, number):
        if not 1 <= number <= self.inodes_count:
            raise Ext4Error(f"inode number {number} out of range")
        group, index = divmod(number - 1, self.inodes_per_group)
        if group >= self.group_count:
            raise Ext4Error(f"inode number {number} out of range")
        offset = self._inode_table(group) * self.block_size + index * self.inode_size
        return Inode(number, self._read(offset, self.inode_size))

    def lookup(self, path):
        inode = self.read_inode(ROOT_INODE)
        for part in path.split("/"):
            if not part:
                continue
            if not inode.is_dir:
                raise NotADirectoryError(path)
            name = os.fsencode(part)
            for entry_name, number in self.iter_dir(inode):
                if entry_name == name:
                    inode = self.read_inode(number)
                    break
            else:
                raise FileNotFoundError(path)
        return inode

    def stat(self, path):
        """Like lstat(): a symlink itself is described, not its target."""
        return self.lookup(path)

    def _extent_runs(self, node, expected_depth=None):
        magic, entries, _, depth = struct.unpack_from("<4H", node)
        if magic != EXTENT_MAGIC:
            raise Ext4Error("bad extent header magic")
        if (
            depth > EXTENT_MAX_DEPTH
            or expected_depth is not None
            and depth != expected_depth
        ):
            raise Ext4Error(f"bad extent tree depth {depth}")
        if 12 + entries * 12 > len(node):
            raise Ext4Error("extent node entry count exceeds node size")
        for offset in range(12, 12 + entries * 12, 12):
            if depth == 0:
                logical, length, start_hi, start_lo = struct.unpack_from(
                    "<IHHI", node, offset
                )
                initialized = length <= EXTENT_UNINIT_BASE
                if not initialized:
                    length -= EXTENT_UNINIT_BASE
                yield logical, start_hi << 32 | start_lo, length, initialized
            else:
                _, leaf_lo, leaf_hi = struct.unpack_from("<IIH", node, offset)
                yield from self._extent_runs(
                    self._read_block(leaf_hi << 32 | leaf_lo), depth - 1
                )

    def _blockmap_runs(self, i_block, nblocks):
        pointers = struct.unpack("<15I", i_block)
        yield from _pointer_runs(pointers[:12], 0, nblocks)
        logical = 12
        per_block = self.block_size // 4
        for level, pointer in enumerate(pointers[12:], 1):
            if logical >= nblocks:
                return
            if pointer:
                yield from self._indirect_runs(pointer, level, logical, nblocks)
            logical += per_block**level

    def _indirect_runs(self, block, level, logical, nblocks):
        per_block = self.block_size // 4
        pointers = struct.unpack(f"<{per_block}I", self._read_block(block))
        if level == 1:
            yield from _pointer_runs(pointers, logical, nblocks)
            return
        span = per_block ** (level - 1)
        for pointer in pointers:
            if logical >= nblocks:
                return
            if pointer:
                yield from self._indirect_runs(pointer, level - 1, logical, nblocks)
            logical += span

    def _data_ranges(self, inode):
        """Yields (file_offset, image_offset, length) for stored data."""
        size = inode.size
        bs = self.block_size
        nblocks = -(-size // bs)
        if inode.flags & EXTENTS_FL:
            runs = self._extent_runs(inode.i_block)
        else:
            runs = self._blockmap_runs(inode.i_block, nblocks)
        for logical, physical, count, initialized in runs:
            if not initialized or logical >= nblocks:
                continue
            count = min(count, nblocks - logical)
            offset = logical * bs
            yield offset, physical * bs, min(count * bs, size - offset)

    @staticmethod
    def _find_xattr(buf, pos, base, index, name, what):
        """Value of xattr index/name in the entry list at buf[pos:], whose
        value offsets are relative to base, or None."""
        while pos + 16 <= len(buf):
            name_len, e_index, value_offs, value_inum, value_size = struct.unpack_from(
                "<BBHII", buf, pos
            )
            if not (name_len or e_index or value_offs or value_inum):
                break
            if e_index == index and buf[pos + 16 : pos + 16 + name_len] == name:
                if value_inum:
                    raise Ext4Error(f"{what} in an EA inode is not supported")
                end = base + value_offs + value_size
                if end > len(buf):
                    raise Ext4Error(f"corrupt {what} xattr")
                return buf[base + value_offs : end]
            pos += (16 + name_len + 3) & ~3
        return None

    def _ibody_xattr(self, inode, index, name, what):
        raw = inode.raw
        start = 128 + inode.extra_isize
        if (
            start + 4 > len(raw)
            or struct.unpack_from("<I", raw, start)[0] != XATTR_MAGIC
        ):
            return None
        # In-inode value offsets are relative to the first entry.
        return self._find_xattr(raw, start + 4, start + 4, index, name, what)

    def _system_data_xattr(self, inode):
        return (
            self._ibody_xattr(inode, XATTR_INDEX_SYSTEM, b"data", "inline data") or b""
        )

    def selinux_label(self, inode):
        """The inode's security.selinux value (without the trailing NUL),
        or None if it has none."""
        what = "SELinux label"
        value = self._ibody_xattr(inode, XATTR_INDEX_SECURITY, b"selinux", what)
        if value is None and inode.file_acl:
            block = self._read_block(inode.file_acl)
            if struct.unpack_from("<I", block)[0] == XATTR_MAGIC:
                # Block value offsets are relative to the block start.
                value = self._find_xattr(
                    block, XATTR_BLOCK_HEADER, 0, XATTR_INDEX_SECURITY, b"selinux", what
                )
        return None if value is None else value.rstrip(b"\0")

    def _inline_data(self, inode):
        data = inode.i_block + self._system_data_xattr(inode)
        return data[: inode.size]

    def _rec_len(self, value):
        # 64 KiB blocks cannot express a 65536-byte rec_len in 16 bits, so
        # the low two bits (always zero otherwise) carry bits 16-17.
        if self.block_size < 65536:
            return value
        if value in (0, 65535):
            return 65536
        return (value & 65532) | (value & 3) << 16

    def _parse_dirents(self, buf):
        filetype = self.incompat & INCOMPAT_FILETYPE
        pos = 0
        while pos + 8 <= len(buf):
            number, rec_len, name_len = struct.unpack_from("<IHH", buf, pos)
            rec_len = self._rec_len(rec_len)
            if filetype:
                name_len &= 0xFF
            if rec_len < 8 or rec_len % 4 or pos + rec_len > len(buf):
                raise Ext4Error(
                    f"corrupt directory entry (rec_len {rec_len} at offset {pos})"
                )
            # inode 0 marks unused space, htree nodes and checksum tails.
            if number:
                if name_len + 8 > rec_len:
                    raise Ext4Error("corrupt directory entry name length")
                name = buf[pos + 8 : pos + 8 + name_len]
                if name not in (b".", b".."):
                    yield name, number
            pos += rec_len

    def iter_dir(self, inode):
        """Yields (name bytes, inode number), without "." and ".."."""
        if not inode.is_dir:
            raise NotADirectoryError(f"inode {inode.number}")
        if inode.flags & INLINE_DATA_FL:
            # i_block starts with the parent inode number; the rest and
            # the system.data value are two separate dirent areas.
            yield from self._parse_dirents(inode.i_block[4:])
            yield from self._parse_dirents(self._system_data_xattr(inode))
            return
        bs = self.block_size
        for _, image_offset, length in self._data_ranges(inode):
            for block in range(0, length, bs):
                yield from self._parse_dirents(self._read(image_offset + block, bs))

    def read_link(self, inode):
        if inode.flags & INLINE_DATA_FL:
            return self._inline_data(inode)
        uses_extents = (
            inode.flags & EXTENTS_FL
            and struct.unpack_from("<H", inode.i_block)[0] == EXTENT_MAGIC
        )
        if inode.size < 60 and not uses_extents:
            return inode.i_block[: inode.size]
        if inode.size > self.block_size:
            raise Ext4Error(f"symlink target too long ({inode.size} bytes)")
        target = bytearray(inode.size)
        for file_offset, image_offset, length in self._data_ranges(inode):
            target[file_offset : file_offset + length] = self._read(
                image_offset, length
            )
        return bytes(target)

    def write_file(self, inode, out):
        """Streams a regular file's contents into a seekable file object,
        leaving holes and uninitialized extents unwritten."""
        if inode.flags & INLINE_DATA_FL:
            out.write(self._inline_data(inode))
        else:
            for file_offset, image_offset, length in self._data_ranges(inode):
                out.seek(file_offset)
                self._file.seek(image_offset)
                while length:
                    chunk = self._file.read(min(COPY_CHUNK, length))
                    if not chunk:
                        raise Ext4Error("image truncated inside file data")
                    out.write(chunk)
                    length -= len(chunk)
        out.truncate(inode.size)


def _pointer_runs(pointers, logical, nblocks):
    """Coalesces a block-map pointer array into contiguous runs."""
    run_start = run_physical = run_length = 0
    for pointer in pointers:
        if logical >= nblocks:
            break
        if run_length and pointer == run_physical + run_length:
            run_length += 1
        else:
            if run_length:
                yield run_start, run_physical, run_length, True
            run_start, run_physical, run_length = logical, pointer, 1
            if not pointer:
                run_length = 0
        logical += 1
    if run_length:
        yield run_start, run_physical, run_length, True
