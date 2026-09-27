"""Oppo / Realme / OnePlus OZIP decryptor (AES-128-ECB, known keys)."""

from typing import Optional
import binascii
import os

try:
    from Crypto.Cipher import AES

    HAS_AES = True
except ImportError:
    try:
        from Cryptodome.Cipher import AES

        HAS_AES = True
    except ImportError:
        HAS_AES = False

OZIP_MAGIC = b"OPPOENCRYPT!"

KNOWN_KEYS = [
    "D6EECF0AE5ACD4E0E9FE522DE7CE381E",  # mnkey
    "D6ECCF0AE5ACD4E0E92E522DE7C1381E",  # mkey
    "D6DCCF0AD5ACD4E0292E522DB7C1381E",  # realkey: R9s, R11, RMX1921, RMX1851
    "D7DCCE1AD4AFDCE2393E5161CBDC4321",  # testkey
    "D7DBCE2AD4ADDCE1393E5521CBDC4321",  # utilkey
    "D7DBCE1AD4AFDCE1393E5121CBDC4321",  # R11s
    "D4D2CD61D4AFDCE13B5E01221BD14D20",  # FindX CPH1871 SDM845
    "261CC7131D7C1481294E532DB752381E",  # FindX
    "1CA21E12271335AE33AB81B2A7B14622",  # Realme 2 pro
    "D4D2CE11D4AFDCE13B3E0121CBD14D20",  # K1
    "1C4C1EA3A12531AE491B21BB31613C11",  # Realme 3 Pro, X, 5 Pro, Q, XT
    "1C4C1EA3A12531AE4A1B21BB31C13C21",  # Reno 10x zoom
    "1C4A11A3A12513AE441B23BB31513121",  # Reno 2
    "1C4A11A3A12589AE441A23BB31517733",  # Realme X2
    "1C4A11A3A22513AE541B53BB31513121",  # Realme 5
    "2442CE821A4F352E33AE81B22BC1462E",  # R17 Pro
    "14C2CD6214CFDC2733AE81B22BC1462C",  # CPH1803 OppoA3s
    "1E38C1B72D522E29E0D4ACD50ACFDCD6",
    "12341EAAC4C123CE193556A1BBCC232D",
    "2143DCCB21513E39E1DCAFD41ACEDBD7",
    "2D23CCBBA1563519CE23C1C4AA1E3412",  # A77
    "172B3E14E46F3CE13E2B5121CBDC4321",  # Realme 1
    "ACAA1E12A71431CE4A1B21BBA1C1C6A2",  # Realme U1
    "ACAC1E13A72531AE4A1B22BB31C1CC22",  # Realme 3
    "1C4411A3A12533AE441B21BB31613C11",  # A1k
    "1C4416A8A42717AE441523B336513121",  # Reno 3
    "55EEAA33112133AE441B23BB31513121",  # RenoAce
    "ACAC1E13A12531AE4A1B21BB31C13C21",  # Reno, K3
    "ACAC1E13A72431AE4A1B22BBA1C1C6A2",  # A9
    "12CAC11211AAC3AEA2658690122C1E81",  # A1, A83t
    "1CA21E12271435AE331B81BBA7C14612",  # CPH1909
    "D1DACF24351CE428A9CE32ED87323216",  # Realme1
    "A1CC75115CAECB890E4A563CA1AC67C8",  # A73
    "2132321EA2CA86621A11241ABA512722",  # Realme3
    "22A21E821743E5EE33AE81B227B1462E",  # F3 Plus
]


def is_ozip(file_path: str) -> bool:
    if not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            return f.read(12) == OZIP_MAGIC
    except OSError:
        return False


def _find_key(test_data: bytes) -> Optional[bytes]:
    for key_hex in KNOWN_KEYS:
        key_bytes = binascii.unhexlify(key_hex)
        cipher = AES.new(key_bytes, AES.MODE_ECB)
        decrypted = cipher.decrypt(test_data[:16])
        if decrypted[:4] in (b"PK\x03\x04", b"AVB0", b"ANDR"):
            return key_bytes
    return None


def decrypt_ozip(ozip_path: str, output_zip_path: str, logger=None) -> bool:
    if not HAS_AES:
        raise RuntimeError("pycryptodome is required for OZIP decryption")

    if not is_ozip(ozip_path):
        return False

    out_dir = os.path.dirname(output_zip_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(ozip_path, "rb") as in_f:
        # Mode 1 encrypts from 0x1050; mode 2 has per-block headers at 0x50.
        in_f.seek(0x1050)
        test_block = in_f.read(16)
        key = _find_key(test_block)

        if not key:
            in_f.seek(0x50)
            test_block = in_f.read(16)
            key = _find_key(test_block)
            if key:
                return _decrypt_mode2(in_f, output_zip_path, key, logger)
            if logger:
                logger("No matching AES key found for OZIP")
            return False

        return _decrypt_mode1(in_f, output_zip_path, key, logger)


def _read_size_field(in_f) -> str:
    raw = in_f.read(16).replace(b"\x00", b"")
    return raw.decode("ascii", errors="ignore").strip()


def _decrypt_mode1(in_f, output_path: str, key: bytes, logger=None) -> bool:
    in_f.seek(0x10)
    try:
        dsize = int(_read_size_field(in_f), 10)
    except ValueError:
        in_f.seek(0, os.SEEK_END)
        dsize = in_f.tell() - 0x1050

    in_f.seek(0x1050)
    cipher = AES.new(key, AES.MODE_ECB)

    with open(output_path, "wb") as out_f:
        remaining = dsize
        while remaining > 0:
            chunk_size = min(remaining, 0x4000)
            data = in_f.read(chunk_size)
            if not data:
                break
            pad_len = (16 - (len(data) % 16)) % 16
            if pad_len > 0:
                data += b"\x00" * pad_len
            dec = cipher.decrypt(data)
            out_f.write(dec[:chunk_size])
            remaining -= chunk_size

    return os.path.isfile(output_path) and os.path.getsize(output_path) > 0


def _decrypt_mode2(in_f, output_path: str, key: bytes, logger=None) -> bool:
    in_f.seek(0, os.SEEK_END)
    total_size = in_f.tell()
    in_f.seek(0)

    cipher = AES.new(key, AES.MODE_ECB)
    bstart = 0

    with open(output_path, "wb") as out_f:
        while bstart < total_size:
            in_f.seek(bstart)
            hdr = in_f.read(12)
            if hdr != OZIP_MAGIC:
                break
            in_f.seek(bstart + 0x10)
            try:
                bdsize = int(_read_size_field(in_f), 10)
            except ValueError:
                break

            in_f.seek(bstart + 0x50)
            while bdsize > 0:
                header_data = in_f.read(16)
                if not header_data:
                    break
                dec = cipher.decrypt(header_data)
                out_f.write(dec)
                bdsize -= 16

                copy_size = min(bdsize, 0x3FF0)
                if copy_size > 0:
                    data = in_f.read(copy_size)
                    out_f.write(data)
                    bdsize -= copy_size

            bstart += 0x40000 + 0x50

    return os.path.isfile(output_path) and os.path.getsize(output_path) > 0
