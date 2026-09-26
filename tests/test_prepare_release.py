import json
import os
import subprocess
import sys


def test_prepare_release_basic(tmp_path):
    out_dir = tmp_path / "out" / "raven"
    os.makedirs(out_dir, exist_ok=True)

    img_file = out_dir / "Pixel-raven-14-MysticGSI.img"
    img_file.write_bytes(b"sample raw image data for test")

    zip_file = out_dir / "Pixel-raven-14-MysticGSI.zip"
    zip_file.write_bytes(b"sample zip data for test")

    output_txt = out_dir / "output.txt"
    output_txt.write_text(
        "Device brand: Google\n"
        "Device manufacturer: Google\n"
        "Device model: Pixel 6 Pro\n"
        "Device codename: raven\n"
        "Device board: gs101\n"
        "Android version: 14\n"
        "Android SDK: 34\n"
        "Build ID: UP1A.231105.003\n"
        "Security patch: 2023-11-05\n"
        "Architecture: 64-bit only\n"
        "Raw Image Size: 3.51 GiB\n"
        "Build fingerprint: google/raven/raven:14/UP1A.231105.003:user/release-keys\n"
    )

    gh_out = tmp_path / "gh_output.txt"
    script_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".github", "scripts", "prepare_release.py"
    )

    res = subprocess.run([
        sys.executable, script_path,
        "--build-name", "raven",
        "--output-dir", str(out_dir),
        "--github-output", str(gh_out),
    ], capture_output=True, text=True)

    assert res.returncode == 0, f"Script failed: {res.stderr}"

    # Verify checksum files created
    assert os.path.isfile(out_dir / "checksums.sha256")
    assert os.path.isfile(out_dir / "checksums.md5")

    # Verify release_notes.md created
    notes_path = out_dir / "release_notes.md"
    assert os.path.isfile(notes_path)
    notes_content = notes_path.read_text()
    assert "Pixel 6 Pro" in notes_content
    assert "UP1A.231105.003" in notes_content
    assert "Fastboot" in notes_content or "fastboot" in notes_content

    # Verify GITHUB_OUTPUT was written
    assert os.path.isfile(gh_out)
    output_text = gh_out.read_text()
    assert "output_name=Pixel-raven-14-MysticGSI" in output_text
    assert "release_tag=Pixel-raven-14-MysticGSI" in output_text
    assert "asset_files=" in output_text


def test_prepare_release_reads_gsilist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tmp_dir = tmp_path / "tmp"
    os.makedirs(tmp_dir, exist_ok=True)
    out_dir = tmp_path / "out" / "device"
    os.makedirs(out_dir, exist_ok=True)

    img = out_dir / "Custom-device-14-MysticGSI.img"
    img.write_bytes(b"custom image")

    gsilist = tmp_dir / "gsilist.json"
    gsilist.write_text(json.dumps([{
        "rom_name": "device",
        "output_name": "Custom-device-14-MysticGSI",
        "output_path": str(out_dir / "Custom-device-14-MysticGSI"),
    }]))

    output_txt = out_dir / "output.txt"
    output_txt.write_text("Android version: 14\n")

    gh_out = tmp_path / "gh_output.txt"
    script_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".github", "scripts", "prepare_release.py"
    )

    res = subprocess.run([
        sys.executable, script_path,
        "--build-name", "device",
        "--github-output", str(gh_out),
    ], capture_output=True, text=True)

    assert res.returncode == 0
    assert "output_name=Custom-device-14-MysticGSI" in gh_out.read_text()
