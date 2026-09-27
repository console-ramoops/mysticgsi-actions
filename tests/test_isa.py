import struct

import pytest

from make import RomPorter
from tools.isa import find_cpu_features


# Encodings produced by llvm-mc; tests do not require LLVM on the host.
INSTRUCTIONS = [
    (0x0460E3E9, "SVE/SVE2"),  # cnth x9
    (0x2598E3E0, "SVE/SVE2"),  # ptrue p0.s
    (0x44427020, "SVE/SVE2"),  # sqrdmlah z0.h, z1.h, z2.h
    (0xD503437F, "SME"),  # smstart sm
    (0xD503457F, "SME"),  # smstart za
    (0xD503477F, "SME"),  # smstart
    (0xC00800FF, "SME"),  # zero {za}
    (0x6E42FC20, "BF16"),  # bfdot v0.4s, v1.8h, v2.8h
    (0x6E42EC20, "BF16"),  # bfmmla v0.4s, v1.8h, v2.8h
    (0x2EC2FC20, "BF16"),  # bfmlalb v0.4s, v1.8h, v2.8h
    (0x1E634020, "BF16"),  # bfcvt h0, s1
    (0x0EA16820, "BF16"),  # bfcvtn v0.4h, v1.4s
    (0x4F62F020, "BF16"),  # indexed bfdot
    (0x0FD2F020, "BF16"),  # indexed bfmlalb
    (0x4E82A420, "I8MM"),  # smmla v0.4s, v1.16b, v2.16b
    (0x6E82A420, "I8MM"),  # ummla v0.4s, v1.16b, v2.16b
    (0x4E82AC20, "I8MM"),  # usmmla v0.4s, v1.16b, v2.16b
    (0x4E829C20, "I8MM"),  # usdot v0.4s, v1.16b, v2.16b
    (0x4FA2F020, "I8MM"),  # indexed usdot
    (0x4F22F020, "I8MM"),  # indexed sudot
    (0x19020420, "MOPS"),  # cpyfp [x0]!, [x2]!, x1!
    (0x19420420, "MOPS"),  # cpyfm
    (0x19820420, "MOPS"),  # cpyfe
    (0x1D020420, "MOPS"),  # cpyp
    (0x19C10443, "MOPS"),  # setp [x3]!, x2!, x1
    (0x19C14443, "MOPS"),  # setm
    (0x19C18443, "MOPS"),  # sete
    (0xDAC01C20, "CSSC"),  # cnt x0, x1
    (0x5AC01820, "CSSC"),  # ctz w0, w1
    (0xDAC02020, "CSSC"),  # abs x0, x1
    (0x9AC26020, "CSSC"),  # smax x0, x1, x2
    (0x91C81420, "CSSC"),  # smin x0, x1, #5
]


def _elf(segments, machine=183):
    data = bytearray(0x400)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, machine)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<H", data, 54, 56)
    struct.pack_into("<H", data, 56, len(segments))
    for index, (code, flags, address) in enumerate(segments):
        offset = len(data)
        struct.pack_into(
            "<IIQQQQQQ",
            data,
            64 + index * 56,
            1,
            flags,
            offset,
            address,
            address,
            len(code),
            len(code),
            1,
        )
        data.extend(code)
    return data


@pytest.mark.parametrize("word,feature", INSTRUCTIONS)
def test_instruction_encodings(tmp_path, word, feature):
    path = tmp_path / "init"
    path.write_bytes(_elf([(struct.pack("<I", word), 5, 0)]))
    assert find_cpu_features(path) == {feature}


def test_scan_excludes_data_and_baseline_instructions(tmp_path):
    path = tmp_path / "init"
    data = b"".join(struct.pack("<I", word) for word, _ in INSTRUCTIONS)
    # NOP, BTI, PACIASP, NEON CNT/ABS, CLZ, SDOT, LDR, ADD, RET.
    baseline = [
        0xD503201F,
        0xD503245F,
        0xD503233F,
        0x4E205820,
        0x4E20B820,
        0xDAC01020,
        0x4E829420,
        0xF9400020,
        0x91000420,
        0xD65F03C0,
    ]
    code = b"".join(struct.pack("<I", word) for word in baseline)
    path.write_bytes(_elf([(data, 4, 0), (code, 5, 0)]))
    assert find_cpu_features(path) == set()

    path.write_bytes(_elf([(data, 5, 0)], machine=62))
    assert find_cpu_features(path) == set()


def test_scan_accumulates_features_across_segments_and_chunks(tmp_path):
    path = tmp_path / "init"
    nop = struct.pack("<I", 0xD503201F)
    code = nop * (1024 * 1024 // 4) + struct.pack("<I", 0xDAC01C20)
    path.write_bytes(_elf([(code, 5, 0), (struct.pack("<I", 0x6E42FC20), 5, 0)]))
    assert find_cpu_features(path) == {"CSSC", "BF16"}


def test_scan_aligns_by_virtual_address(tmp_path):
    path = tmp_path / "init"
    cnth = struct.pack("<I", 0x0460E3E9)
    path.write_bytes(_elf([(b"\0" + cnth, 5, 3)]))
    assert find_cpu_features(path) == {"SVE/SVE2"}
    path.write_bytes(_elf([(b"\0" + cnth, 5, 0)]))
    assert find_cpu_features(path) == set()


@pytest.mark.parametrize("damage", ["header", "table", "segment"])
def test_scan_rejects_truncated_elf_even_after_a_match(tmp_path, damage):
    path = tmp_path / "init"
    code = struct.pack("<I", 0x0460E3E9)
    data = _elf([(code, 5, 0), (code, 5, 0)])
    if damage == "header":
        data = data[:32]
    elif damage == "table":
        struct.pack_into("<Q", data, 32, len(data) - 1)
    else:
        struct.pack_into("<Q", data, 64 + 56 + 32, 1000)
    path.write_bytes(data)
    assert find_cpu_features(path) is None


def test_warning_checks_prepared_tree_and_reports_features(tmp_path):
    system = tmp_path / "system"
    (system / "bin/bootstrap").mkdir(parents=True)
    (system / "build.prop").touch()
    code = struct.pack("<II", 0x0460E3E9, 0xDAC01C20)
    (system / "bin/init").write_bytes(_elf([(code, 5, 0)]))
    (system / "bin/bootstrap/linker64").write_bytes(
        _elf([(struct.pack("<I", 0x19C10443), 5, 0)])
    )
    porter = RomPorter("test")
    porter.partition_dirs = {"system": str(system)}
    logs = []
    porter.log = logs.append
    porter._warn_cpu_features()
    assert "SVE/SVE2 (system/bin/init)" in porter.cpu_warning
    assert "CSSC (system/bin/init)" in porter.cpu_warning
    assert "MOPS (system/bin/bootstrap/linker64)" in porter.cpu_warning
    assert "runtime CPU checks may provide fallbacks" in porter.cpu_warning
    assert len(logs) == 1

    (system / "bin/init").unlink()
    (system / "bin/bootstrap/linker64").unlink()
    porter._warn_cpu_features()
    assert porter.cpu_warning == ""
