import os

import brotli
import pytest

from tools.extractor.formats import sdat

BLK = sdat.BLOCK_SIZE


def test_split_brotli_dat_follows_transfer_list(tmp_path):
    blocks = [os.urandom(BLK) for _ in range(4)]
    data = brotli.compress(b"".join(blocks))
    half = len(data) // 2
    (tmp_path / "system.new.dat.br.0").write_bytes(data[:half])
    (tmp_path / "system.new.dat.br.1").write_bytes(data[half:])
    (tmp_path / "system.transfer.list").write_text(
        "4\n10\n0\n0\nerase 2,0,10\nnew 4,6,8,1,2\nzero 2,2,6\nnew 2,9,10\n"
    )

    out = tmp_path / "system.img"
    assert sdat.sdat_to_img(str(tmp_path / "system.transfer.list"), str(out))

    zero = bytes(BLK)
    assert out.read_bytes() == (
        zero + blocks[2] + zero * 4 + blocks[0] + blocks[1] + zero + blocks[3]
    )


@pytest.mark.parametrize("compressed", [False, True])
def test_truncated_sdat_keeps_previous_output(tmp_path, compressed):
    data = b"x" * (BLK + 16)
    suffix = ".br" if compressed else ""
    (tmp_path / f"system.new.dat{suffix}").write_bytes(
        brotli.compress(data) if compressed else data
    )
    transfer = tmp_path / "system.transfer.list"
    transfer.write_text("4\n3\n0\n0\nnew 2,0,1\nnew 2,2,3\n")
    output = tmp_path / "system.img"
    output.write_bytes(b"previous image")

    with pytest.raises(RuntimeError, match="Truncated"):
        sdat.sdat_to_img(str(transfer), str(output))

    assert output.read_bytes() == b"previous image"
    assert not list(tmp_path.glob("sdat-*"))
