import glob
import lzma
import os
import re
import tempfile

THRESHOLD = 50 * 1024 * 1024
SPLIT_SIZE = 45 * 1024 * 1024
SUFFIX = ".xz"
PART = re.compile(r"\.xz\.\d{3}$")
PRESET = 6
CHUNK = 1024 * 1024


def is_packed(path):
    return path.endswith(SUFFIX) or bool(PART.search(path))


def raw_path(path):
    path = PART.sub(SUFFIX, path)
    return path[: -len(SUFFIX)] if path.endswith(SUFFIX) else path


def packed_path(path):
    return raw_path(path) + SUFFIX


def parts_for(archive):
    return sorted(glob.glob(glob.escape(archive) + ".[0-9][0-9][0-9]"))


def archive_exists(archive):
    return os.path.exists(archive) or bool(parts_for(archive))


def archive_files(archive):
    return [archive] if os.path.exists(archive) else parts_for(archive)


def archive_size(archive):
    return sum(os.path.getsize(f) for f in archive_files(archive))


def archive_mtime(archive):
    files = archive_files(archive)
    return min(os.path.getmtime(f) for f in files) if files else 0


def _clear_archive(archive):
    for f in [archive] + parts_for(archive):
        if os.path.exists(f):
            os.remove(f)


def _tmp_in(path):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    os.close(fd)
    return tmp


def _split(archive):
    parts = []
    try:
        with open(archive, "rb") as src:
            i = 0
            while True:
                buf = src.read(SPLIT_SIZE)
                if not buf:
                    break
                p = f"{archive}.{i:03d}"
                with open(p, "wb") as out:
                    out.write(buf)
                parts.append(p)
                i += 1
    except BaseException:
        for p in parts:
            if os.path.exists(p):
                os.remove(p)
        raise
    os.remove(archive)
    return parts


def compress(path, force=False):
    raw = raw_path(path)
    archive = packed_path(path)
    if (
        not force
        and archive_exists(archive)
        and archive_mtime(archive) >= os.path.getmtime(raw)
    ):
        return None

    tmp = _tmp_in(archive)
    try:
        with open(raw, "rb") as src, lzma.open(tmp, "wb", preset=PRESET) as out:
            while True:
                buf = src.read(CHUNK)
                if not buf:
                    break
                out.write(buf)
        _clear_archive(archive)
        os.replace(tmp, archive)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    if os.path.getsize(archive) >= THRESHOLD:
        _split(archive)
    return archive


def decompress(path, force=False):
    raw = raw_path(path)
    archive = packed_path(path)
    if not archive_exists(archive):
        return None
    if not force and os.path.exists(raw):
        return None

    tmp = _tmp_in(raw)
    try:
        dec = lzma.LZMADecompressor()
        with open(tmp, "wb") as out:
            for f in archive_files(archive):
                with open(f, "rb") as src:
                    while True:
                        buf = src.read(CHUNK)
                        if not buf:
                            break
                        out.write(dec.decompress(buf))
            if not dec.eof:
                raise EOFError(f"{archive}: truncated or missing a part")
        os.replace(tmp, raw)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return raw


def find_packed(root):
    seen = set()
    for r, d, files in os.walk(root):
        for f in sorted(files):
            if not is_packed(f):
                continue
            archive = packed_path(os.path.join(r, f))
            if archive not in seen:
                seen.add(archive)
                yield archive


def find_oversized(root, threshold=THRESHOLD):
    for r, d, files in os.walk(root):
        for f in files:
            if is_packed(f):
                continue
            p = os.path.join(r, f)
            if os.path.islink(p):
                continue
            try:
                if os.path.getsize(p) >= threshold:
                    yield p
            except OSError:
                pass


def ensure_extracted(root, log=None):
    if not root or not os.path.exists(root):
        return []

    done = []
    for archive in find_packed(root):
        raw = raw_path(archive)
        if os.path.exists(raw):
            continue
        if log:
            log(f"Extracting {os.path.basename(raw)}..")
        decompress(raw)
        done.append(raw)
    return done
