"""Find selected newer CPU instructions in AArch64 executable segments.

This is a presence check, not proof that an instruction executes on every
CPU. Missing matches do not establish compatibility with older devices.
"""

import os
import struct


# Masks follow LLVM's AArch64InstrFormats.td. Keep SIMD and scalar forms
# separate: their identical mnemonics can have different CPU requirements.
INSTRUCTION_PATTERNS = {
    "SVE/SVE2": ((0x1E000000, 0x04000000),),
    "SME": (
        (0xFFFFFFFF, 0xD503437F),  # SMSTART SM
        (0xFFFFFFFF, 0xD503457F),  # SMSTART ZA
        (0xFFFFFFFF, 0xD503477F),  # SMSTART
        (0xFFFFFF00, 0xC0080000),  # ZERO ZA
    ),
    "BF16": (
        (0xBFE0FC00, 0x2E40FC00),  # BFDOT
        (0xFFE0FC00, 0x6E40EC00),  # BFMMLA
        (0xBFE0FC00, 0x2EC0FC00),  # BFMLALB/T
        (0xFFFFFC00, 0x1E634000),  # BFCVT
        (0xBFFFFC00, 0x0EA16800),  # BFCVTN/2
        (0xBFC0F400, 0x0F40F000),  # Indexed BFDOT
        (0xBFC0F400, 0x0FC0F000),  # Indexed BFMLALB/T
    ),
    "I8MM": (
        (0xFFE0FC00, 0x4E80A400),  # SMMLA
        (0xFFE0FC00, 0x6E80A400),  # UMMLA
        (0xFFE0FC00, 0x4E80AC00),  # USMMLA
        (0xBFE0FC00, 0x0E809C00),  # USDOT
        (0xBFC0F400, 0x0F80F000),  # Indexed USDOT
        (0xBFC0F400, 0x0F00F000),  # Indexed SUDOT
    ),
    "MOPS": (
        (0xFB200C00, 0x19000400),  # CPYF/CPY P/M/E, option variants
        (0xFBE00800, 0x19C00000),  # SET/SETG P/M/E, option variants
    ),
    "CSSC": (
        (0x7FFFFC00, 0x5AC01800),  # Scalar CTZ
        (0x7FFFFC00, 0x5AC01C00),  # Scalar CNT
        (0x7FFFFC00, 0x5AC02000),  # Scalar ABS
        (0x7FE0F000, 0x1AC06000),  # Scalar min/max, register
        (0x7FF00000, 0x11C00000),  # Scalar min/max, immediate
    ),
}


def find_cpu_features(path):
    """Return feature names, or None for an unreadable/malformed ARM64 ELF."""
    try:
        with open(path, "rb") as file:
            size = os.fstat(file.fileno()).st_size
            header = file.read(64)
            if (
                header[:6] != b"\x7fELF\x02\x01"
                or len(header) < 20
                or struct.unpack_from("<H", header, 18)[0] != 183
            ):
                return set()
            if len(header) != 64:
                return None

            program_offset = struct.unpack_from("<Q", header, 32)[0]
            program_size = struct.unpack_from("<H", header, 54)[0]
            program_count = struct.unpack_from("<H", header, 56)[0]
            if (
                program_size < 56
                or program_offset > size
                or program_count == 0xFFFF
                or program_count > (size - program_offset) // program_size
            ):
                return None

            segments = []
            for index in range(program_count):
                file.seek(program_offset + index * program_size)
                segment = file.read(56)
                kind, flags = struct.unpack_from("<II", segment)
                if kind != 1 or not flags & 1:
                    continue
                offset = struct.unpack_from("<Q", segment, 8)[0]
                address = struct.unpack_from("<Q", segment, 16)[0]
                length = struct.unpack_from("<Q", segment, 32)[0]
                if offset > size or length > size - offset:
                    return None
                # Instructions are aligned by virtual address, not file offset.
                padding = -address % 4
                if length > padding:
                    segments.append((offset + padding, length - padding))

            features = set()
            for offset, length in segments:
                file.seek(offset)
                remaining = length - length % 4
                while remaining:
                    chunk_size = min(remaining, 1024 * 1024)
                    chunk = file.read(chunk_size)
                    if len(chunk) != chunk_size:
                        return None
                    for (word,) in struct.iter_unpack("<I", chunk):
                        for feature, patterns in INSTRUCTION_PATTERNS.items():
                            if feature not in features and any(
                                word & mask == value for mask, value in patterns
                            ):
                                features.add(feature)
                    remaining -= len(chunk)
            return features
    except (OSError, struct.error):
        return None
