#!/usr/bin/env python3
"""
Prepares MysticGSI build outputs for GitHub Actions artifacts and releases.
- Scans build outputs (tmp/gsilist.json and out/<name>/)
- Computes SHA-256 and MD5 checksums
- Enforces GitHub Release file size limits (2 GiB) with zip fallback
- Generates structured markdown release notes
- Exports outputs to $GITHUB_OUTPUT for subsequent workflow steps
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile

GITHUB_RELEASE_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB limit


def hash_file(file_path: str, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def human_size(size_bytes: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PiB"


def parse_metadata(output_txt_path: str) -> dict:
    metadata = {}
    if not os.path.isfile(output_txt_path):
        return metadata

    with open(output_txt_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, sep, val = line.partition(":")
            metadata[key.strip()] = val.strip()
    return metadata


def compress_img_to_zip(img_path: str, zip_path: str) -> None:
    print(f"Compressing {img_path} to {zip_path} (DEFLATE)...", flush=True)
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as zf:
        zf.write(img_path, arcname="system.img")
    sz = human_size(os.path.getsize(zip_path))
    print(f"Compressed {img_path} -> {zip_path} ({sz})", flush=True)


def find_build_info(build_name: str, out_dir: str):
    output_name = None
    output_path = None
    gsilist_path = "tmp/gsilist.json"

    if os.path.isfile(gsilist_path):
        try:
            with open(gsilist_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            matching = [e for e in entries if e.get("rom_name") == build_name]
            entry = matching[-1] if matching else (
                entries[-1] if entries else None)
            if entry:
                output_name = entry.get("output_name")
                output_path = entry.get("output_path")
        except Exception as e:
            print(f"warning: error reading {gsilist_path}: {e}",
                  file=sys.stderr)

    if not out_dir:
        out_dir = f"out/{build_name}" if build_name else "out"

    if not output_name and os.path.isdir(out_dir):
        for fname in sorted(os.listdir(out_dir)):
            if fname.endswith(".img") or fname.endswith(".zip"):
                output_name = fname.rsplit(".", 1)[0]
                output_path = os.path.join(out_dir, output_name)
                break

    if not output_name:
        output_name = build_name or "MysticGSI-build"
        output_path = os.path.join(out_dir, output_name)

    return output_name, output_path, out_dir


def generate_release_notes(
    output_name: str,
    metadata: dict,
    file_info: list,
    omitted_large_files: list,
) -> str:
    device_model = metadata.get("Device model", "Unknown Device")
    device_codename = metadata.get("Device codename", "unknown")
    device_brand = metadata.get("Device brand", "")
    device_mfr = metadata.get("Device manufacturer", "")
    android_ver = metadata.get("Android version", "Unknown")
    android_sdk = metadata.get("Android SDK", "Unknown")
    build_id = metadata.get("Build ID", "Unknown")
    security_patch = metadata.get("Security patch", "Unknown")
    arch = metadata.get("Architecture", "Unknown")
    raw_size = metadata.get("Raw Image Size", "Unknown")
    fingerprint = metadata.get("Build fingerprint", "Unknown")

    if device_brand and device_mfr and device_brand != device_mfr:
        mfr = f"{device_brand} / {device_mfr}"
    else:
        mfr = device_brand or device_mfr or "Unknown"

    notes = [
        f"# MysticGSI Release: `{output_name}`",
        "",
        "Generic System Image (GSI) built with "
        "[MysticGSI](https://github.com/MysticGSI/mysticgsi).",
        "",
        "## 📱 Build Specifications",
        "",
        "| Attribute | Value |",
        "|---|---|",
        f"| **Target Model** | {device_model} (`{device_codename}`) |",
        f"| **Manufacturer** | {mfr} |",
        f"| **Android Version** | Android {android_ver} (API {android_sdk}) |",
        f"| **Build ID** | `{build_id}` |",
        f"| **Security Patch** | `{security_patch}` |",
        f"| **Architecture** | {arch} |",
        f"| **Raw Image Size** | {raw_size} |",
        f"| **Build Fingerprint** | `{fingerprint}` |",
        "",
        "## 📦 Assets & Checksums",
        "",
        "| File | Size | SHA-256 Checksum |",
        "|---|---|---|",
    ]

    for item in file_info:
        row = (f"| `{item['name']}` | {item['size_human']} | "
               f"`{item['sha256']}` |")
        notes.append(row)

    if omitted_large_files:
        notes.extend([
            "",
            "> [!NOTE]",
            "> The following raw image(s) exceed GitHub's 2 GiB per-asset "
            "release limit and are excluded from direct release assets:",
        ])
        for f in omitted_large_files:
            notes.append(f"> - `{f['name']}` ({f['size_human']})")
        notes.append("> Please download the corresponding `.zip` package "
                     "and extract `system.img` before flashing.")

    notes.extend([
        "",
        "## ⚡ Flashing Instructions",
        "",
        "1. Extract `system.img` if downloaded as a `.zip` package.",
        "2. Boot your target device into fastbootd mode:",
        "   ```bash",
        "   adb reboot fastboot",
        "   ```",
        "3. Flash the system image to the system partition:",
        "   ```bash",
        "   fastboot flash system system.img",
        "   ```",
        "4. (Recommended on first install) Perform a factory reset:",
        "   ```bash",
        "   fastboot -w",
        "   ```",
        "5. Reboot your device into system:",
        "   ```bash",
        "   fastboot reboot",
        "   ```",
        "",
        "---",
        "*Built with ❤️ using MysticGSI automation.*",
    ])

    return "\n".join(notes) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Prepare MysticGSI release assets.")
    parser.add_argument("--build-name", default="gsi",
                        help="Build name passed to cli.py")
    parser.add_argument("--output-dir", default="",
                        help="Custom output directory")
    parser.add_argument("--release-tag", default="",
                        help="Custom release tag")
    parser.add_argument("--release-title", default="",
                        help="Custom release title")
    parser.add_argument("--github-output",
                        default=os.environ.get("GITHUB_OUTPUT", ""),
                        help="Path to GITHUB_OUTPUT file")
    args = parser.parse_args()

    output_name, output_path, out_dir = find_build_info(
        args.build_name, args.output_dir)
    print(f"Output name: {output_name}")
    print(f"Output path: {output_path}")
    print(f"Output directory: {out_dir}")

    if not os.path.isdir(out_dir):
        print(f"error: output directory '{out_dir}' not found",
              file=sys.stderr)
        return 1

    img_file = f"{output_path}.img"
    zip_file = f"{output_path}.zip"
    output_txt = os.path.join(out_dir, "output.txt")

    if os.path.isfile(img_file):
        img_size = os.path.getsize(img_file)
        if (img_size > GITHUB_RELEASE_MAX_BYTES
                and not os.path.isfile(zip_file)):
            print(f"Raw image {img_file} is {human_size(img_size)} (> 2 GiB). "
                  "Creating zip...", flush=True)
            compress_img_to_zip(img_file, zip_file)

    binary_assets = []
    if os.path.isfile(zip_file):
        binary_assets.append(zip_file)
    if os.path.isfile(img_file):
        binary_assets.append(img_file)

    file_info = []
    sha256_lines = []
    md5_lines = []

    for path in binary_assets:
        fname = os.path.basename(path)
        fsize = os.path.getsize(path)
        sha256 = hash_file(path, "sha256")
        md5 = hash_file(path, "md5")

        file_info.append({
            "path": path,
            "name": fname,
            "size": fsize,
            "size_human": human_size(fsize),
            "sha256": sha256,
            "md5": md5,
        })
        sha256_lines.append(f"{sha256}  {fname}")
        md5_lines.append(f"{md5}  {fname}")

    sha256_path = os.path.join(out_dir, "checksums.sha256")
    md5_path = os.path.join(out_dir, "checksums.md5")

    with open(sha256_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sha256_lines) + "\n")
    with open(md5_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md5_lines) + "\n")

    release_assets = []
    omitted_large = []

    for item in file_info:
        if item["size"] > GITHUB_RELEASE_MAX_BYTES:
            print(f"Skipping direct release upload for {item['name']} "
                  f"({item['size_human']} > 2 GiB limit)")
            omitted_large.append(item)
        else:
            release_assets.append(item["path"])

    if os.path.isfile(sha256_path):
        release_assets.append(sha256_path)
    if os.path.isfile(md5_path):
        release_assets.append(md5_path)
    if os.path.isfile(output_txt):
        release_assets.append(output_txt)

    metadata = parse_metadata(output_txt)

    release_notes_md = generate_release_notes(
        output_name, metadata, file_info, omitted_large)
    release_notes_file = os.path.join(out_dir, "release_notes.md")
    with open(release_notes_file, "w", encoding="utf-8") as f:
        f.write(release_notes_md)

    tag = args.release_tag.strip() or output_name
    title = args.release_title.strip() or f"MysticGSI: {output_name}"

    print("\n=== Release Assets to Upload ===")
    for a in release_assets:
        print(f" - {a} ({human_size(os.path.getsize(a))})")

    if args.github_output:
        print(f"\nWriting step outputs to {args.github_output}...")
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write(f"output_name={output_name}\n")
            f.write(f"output_dir={out_dir}\n")
            f.write(f"release_tag={tag}\n")
            f.write(f"release_title={title}\n")
            f.write(f"release_notes_file={release_notes_file}\n")
            img_val = img_file if os.path.isfile(img_file) else ''
            zip_val = zip_file if os.path.isfile(zip_file) else ''
            f.write(f"img_path={img_val}\n")
            f.write(f"zip_path={zip_val}\n")
            assets_str = ' '.join(f'{path}' for path in release_assets)
            f.write(f"asset_files={assets_str}\n")
            all_list = [item["path"] for item in file_info]
            if os.path.isfile(sha256_path):
                all_list.append(sha256_path)
            if os.path.isfile(md5_path):
                all_list.append(md5_path)
            if os.path.isfile(output_txt):
                all_list.append(output_txt)
            all_str = ' '.join(f'{path}' for path in all_list)
            f.write(f"all_files={all_str}\n")
            over_str = ' '.join(f'{item["path"]}' for item in omitted_large)
            f.write(f"oversized_files={over_str}\n")
            has_over = 'true' if omitted_large else 'false'
            f.write(f"has_oversized={has_over}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
