import os

import pytest

from tools.extractor import archive


def _rar(tmp_path):
    path = tmp_path / "fw.rar"
    path.write_bytes(b"Rar!\x1a\x07\x01\x00")
    return str(path)


def _tool(bin_dir, name, body):
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)


def test_failed_tool_leaves_nothing_and_the_next_one_is_tried(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # bsdtar -xf <archive> -C <dir>: writes a truncated file, then fails.
    _tool(bin_dir, "bsdtar", 'printf partial > "$4/update.zip"; exit 1\n')
    # 7zz x -y -o<dir> <archive>
    _tool(
        bin_dir,
        "7zz",
        'd="${3#-o}"; /bin/mkdir -p "$d/dload"; printf good > "$d/dload/update.zip"\n',
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    out = tmp_path / "out"
    out.mkdir()

    extracted = archive.extract_archive(_rar(tmp_path), str(out))

    assert extracted == [str(out / "dload" / "update.zip")]
    assert (out / "dload" / "update.zip").read_text() == "good"
    assert os.listdir(out) == ["dload"]


def test_every_tool_failing_is_an_error(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _tool(bin_dir, "bsdtar", "exit 1\n")
    _tool(bin_dir, "7zz", "exit 2\n")
    monkeypatch.setenv("PATH", str(bin_dir))

    with pytest.raises(RuntimeError, match="bsdtar .exit 1., 7zz .exit 2."):
        archive.extract_archive(_rar(tmp_path), str(tmp_path))
