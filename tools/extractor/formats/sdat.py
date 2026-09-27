"""A-only OTA .new.dat[.br|.xz] + .transfer.list to raw image converter."""

from typing import List, Optional, Tuple
import io
import lzma
import os
import tempfile

try:
    import brotli

    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

from ...config import BLOCK_SIZE

MB = 1024 * 1024
DAT_EXTENSIONS = (".new.dat", ".new.dat.br", ".new.dat.xz")


def rangeset(src: str) -> List[Tuple[int, int]]:
    src_set = [int(item) for item in src.split(",")]
    if len(src_set) != src_set[0] + 1:
        raise ValueError(f"Invalid rangeset syntax: {src}")
    return [(src_set[i], src_set[i + 1]) for i in range(1, len(src_set), 2)]


def _parse_transfer_list(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.strip() for line in f if line.strip()]
    if len(lines) < 2:
        return None

    # v2+ adds stash entry/block counts after the version and block total.
    cmd_start_idx = 4 if int(lines[0]) >= 2 else 2
    commands = []
    for line in lines[cmd_start_idx:]:
        parts = line.split(" ")
        if parts[0] in ("new", "erase", "zero"):
            commands.append((parts[0], rangeset(parts[1])))
    return commands


def find_new_dat(prefix: str) -> Optional[List[str]]:
    """
    Returns the data file for <prefix>.transfer.list, or its split pieces
    (<name>.0, <name>.1, ...) in order.
    """
    for ext in DAT_EXTENSIONS:
        path = prefix + ext
        if os.path.isfile(path):
            return [path]
        pieces = []
        while os.path.isfile(f"{path}.{len(pieces)}"):
            pieces.append(f"{path}.{len(pieces)}")
        if pieces:
            return pieces
    return None


class _ConcatReader(io.RawIOBase):
    def __init__(self, paths: List[str]):
        self._paths = list(paths)
        self._file = None

    def readable(self):
        return True

    def readinto(self, buffer):
        while self._paths or self._file:
            if self._file is None:
                self._file = open(self._paths.pop(0), "rb")
            n = self._file.readinto(buffer)
            if n:
                return n
            self._file.close()
            self._file = None
        return 0

    def close(self):
        if self._file:
            self._file.close()
        super().close()


class _BrotliReader(io.RawIOBase):
    def __init__(self, raw):
        if not HAS_BROTLI:
            raise RuntimeError("brotli package required to decompress .dat.br")
        self._raw = raw
        self._decompressor = brotli.Decompressor()
        self._pending = b""

    def readable(self):
        return True

    def readinto(self, buffer):
        while not self._pending:
            chunk = self._raw.read(MB)
            if not chunk:
                return 0
            self._pending = self._decompressor.process(chunk)
        n = min(len(buffer), len(self._pending))
        buffer[:n] = self._pending[:n]
        self._pending = self._pending[n:]
        return n


def _open_data(paths: List[str]):
    raw = io.BufferedReader(_ConcatReader(paths), MB)
    name = paths[0].split(".new.dat", 1)[1]
    if name.startswith(".br"):
        return io.BufferedReader(_BrotliReader(raw), MB)
    if name.startswith(".xz"):
        return lzma.open(raw)
    return raw


def _apply_commands(commands, data, output_img_path: str):
    block_sets = [pair for _, blocks in commands for pair in blocks]
    max_file_size = max(end for _, end in block_sets) * BLOCK_SIZE if block_sets else 0

    with open(output_img_path, "wb") as out_f:
        # "new" data is stored in command order; zero/erase leave holes.
        for cmd, blocks in commands:
            if cmd != "new":
                continue
            for begin, end in blocks:
                out_f.seek(begin * BLOCK_SIZE)
                to_read = (end - begin) * BLOCK_SIZE
                while to_read > 0:
                    chunk = data.read(min(to_read, MB))
                    if not chunk:
                        raise RuntimeError("Truncated new.dat block data")
                    out_f.write(chunk)
                    to_read -= len(chunk)
        out_f.truncate(max(out_f.tell(), max_file_size))


def sdat_to_img(transfer_list_path: str, output_img_path: str) -> bool:
    prefix = transfer_list_path[: -len(".transfer.list")]
    commands = _parse_transfer_list(transfer_list_path)
    pieces = find_new_dat(prefix)
    if commands is None or pieces is None:
        return False

    out_dir = os.path.dirname(output_img_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sdat-", dir=out_dir or ".") as staging:
        converted = os.path.join(staging, "image.img")
        with _open_data(pieces) as data:
            _apply_commands(commands, data, converted)
        if os.path.getsize(converted) == 0:
            return False
        os.replace(converted, output_img_path)
    return os.path.getsize(output_img_path) > 0
