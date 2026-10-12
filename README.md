# MysticGSI

A tool to build a GSI (Generic System Image) from stock Android firmware.

Supported firmware: 
- full OTA zips (`payload.bin`)  
- fastboot packages  
- `super.img`, sparse images  
- `system.new.dat`  
- Samsung tars  
- Huawei `UPDATE.APP`  
- Unisoc `.pac`  
- LG `.kdz`  
- Oppo `.ozip`  
- QFIL packages  
- Sony `.sin`  
- Pixel factory images  

Partitions can be ext4, EROFS or F2FS.

## Setup

This project requires Python 3.10+.

### Automatic setup

#### macOS / Linux

On macOS, install Homebrew and Xcode Command Line Tools first.

```sh
git clone https://github.com/MysticGSI/mysticgsi.git && cd mysticgsi
./setup_host.py
```

#### Windows

Install Python 3 and Git if you haven't already. Make sure both are added to PATH.
We recommend using WinGet:

```cmd
winget install --id Python.Python.3.14 --exact --source winget
winget install --id Git.Git --exact --source winget
```

```cmd
git clone https://github.com/MysticGSI/mysticgsi.git && cd mysticgsi
python setup_host.py
```

On macOS, Debian/Ubuntu, Arch and Windows, the script installs system packages and
creates `.venv`. On NixOS, it checks the development shell. Native image
tools and the Apktool JAR are bundled with the repository.

### Manual setup

Swap `requirements.txt` for `requirements-dev.txt` if you want the dev tools.
The bundled Apktool JAR needs Java; native image tools are under
`tools/bin/<platform>/<architecture>`.

<details>
<summary>macOS</summary>

```sh
xcode-select --install
brew install python@3.13 curl aria2 gpatch openssl@3 openjdk@17
export PATH="$(brew --prefix openjdk@17)/bin:$PATH"
"$(brew --prefix python@3.13)/bin/python3.13" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

</details>

<details>
<summary>Ubuntu / Debian</summary>

```sh
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip curl aria2 \
    openssl patch ca-certificates libarchive-tools default-jre-headless
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

</details>

<details>
<summary>Arch</summary>

```sh
sudo pacman -Syu --needed python python-pip curl aria2 openssl patch \
    libarchive jre17-openjdk-headless
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

</details>

<details>
<summary>NixOS</summary>

```sh
nix --extra-experimental-features 'nix-command flakes' develop
python3 cli.py build <name> <firmware> --type <type>
```

</details>

<details>
<summary>Windows</summary>

```cmd
winget install --id 7zip.7zip --exact --source winget
winget install --id aria2.aria2 --exact --source winget
winget install --id EclipseAdoptium.Temurin.26.JDK --exact --source winget
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

</details>



## Usage

```sh
.venv/bin/python cli.py build <name> <firmware|URL> [--type <type>] [--compress]
.venv/bin/python cli.py rebuild <name> [--compress]
.venv/bin/python cli.py list
.venv/bin/python cli.py clean
```

On Windows, run from the repository root:

```cmd
.venv\Scripts\python.exe cli.py build test D:\path\to\firmware.tar
```

F2FS extraction on Windows needs a local NTFS working directory that allows
case sensitivity. Use an up-to-date Python for Windows directory permissions.

### Commands

```sh
build - Build a new ROM image
rebuild - Rebuild an existing ROM image
list - List all builds
clean - Clean up all builds
```

### Command-line options

```sh
name - Name of the build
firmware or URL - Path to the firmware or URL to download it from
--type <type> - ROM type; default is auto (use an explicit type if detection fails)
--compress - Compress the output image into a ZIP
--add <tag> - Add a tag to the build name
--no-debloat - Keep the apps the patch set would otherwise remove
--no-merge - Build separate images instead of merging partitions into system
--avb-key /path/to/key.pem - Sign with the specified RSA private key
```

By default, an image is signed with AOSP's AVB RSA-2048 test key. You can pass `--avb-key /path/to/key.pem` to sign with your own RSA private key.

To keep separate system and product partitions:

```sh
.venv/bin/python cli.py build lg firmware.kdz --no-merge --compress
```

This writes `*-system.img`, `*-product.img`, and images for any other partitions
normally merged into system. Partitions already inside system stay there.
The ZIP contains each image as `<partition>.img`; `rebuild` keeps this layout.
These images require a device that mounts the separate partitions.

Example:

```sh
.venv/bin/python cli.py build raven \
    https://dl.google.com/dl/android/aosp/raven-up1a.231105.003-factory-76a795d5.zip \
    --type pixel --compress
```

## GitHub Actions

You can build GSIs directly in GitHub Actions without installing anything locally.

### Triggering a Build

1. Go to your repository on GitHub and open the **Actions** tab.
2. Select the **Build GSI** workflow in the left sidebar.
3. Click **Run workflow**, fill in the parameters, and click the green **Run workflow** button.

### Workflow Inputs

| Input | Type | Default | Description |
|---|---|---|---|
| `rom_url` | String | *(Required)* | Direct download link to firmware (`payload.bin`, factory image, `super.img`, `.tar`, `.ozip`, etc.) |
| `rom_name` | String | `gsi` | Build identifier naming `out/<rom_name>/` |
| `rom_type` | String | `auto` | ROM patch type (`auto`, `pixel`, `hyperos`, `oneui`, `coloros`, `generic`, etc.) |
| `variant_tag` | String | *(empty)* | Optional tag appended to build name (e.g. `vanilla`, `gapps`) |
| `compress` | Boolean | `true` | Package output image into a `.zip` archive containing `system.img` |
| `no_debloat` | Boolean | `false` | Keep manufacturer apps that would otherwise be debloated |
| `create_release` | Boolean | `true` | Automatically publish a GitHub Release with built assets |
| `release_tag` | String | *(empty)* | Custom release tag (defaults to build output name if blank) |
| `release_title` | String | *(empty)* | Custom release title (defaults to build output name if blank) |
| `prerelease` | Boolean | `false` | Mark the GitHub Release as a pre-release |
| `upload_artifact` | Boolean | `true` | Upload built package and metadata to GitHub Actions run artifacts |
| `upload_to_gofile` | Boolean | `false` | Upload built package to GoFile (also automatic for >2GB files or release failures) |
| `retention_days` | Number | `7` | Artifact retention period in days (1-90) |

### Releases & Outputs

When a build completes, the workflow produces:

- **GitHub Release** (if `create_release` is enabled):
  - Compressed `.zip` containing `system.img` (raw `.img` files are omitted to save storage and bandwidth)
  - `output.txt`: complete ROM and device hardware details
  - `checksums.sha256` and `checksums.md5`: integrity verification hashes for the ZIP archive
  - Formatted release notes with device specifications, download table, and fastboot flashing instructions
- **GoFile Uploads** (automatic fallback and large-file mirror):
  - When assets exceed GitHub Releases' 2 GiB upload limit, the compressed package is automatically uploaded to [GoFile](https://gofile.io) and linked in the release notes and workflow summary.
  - If GitHub Release creation fails or is disabled, assets automatically fall back to GoFile upload with instant download links.
  - You can also force upload assets to GoFile by enabling `upload_to_gofile`.
- **Workflow Run Artifacts** (if `upload_artifact` is enabled):
  - Downloadable directly from the GitHub Actions run summary page.
- **Custom AVB Signing**:
  - Store an unencrypted RSA private key (2048, 4096, or 8192 bits) in GitHub Repository Secrets as `AVB_KEY` to sign with your own key instead of the AOSP test key.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

```sh
.venv/bin/ruff check .
```

Patch files of 50 MiB or more are stored xz-compressed (`<name>.xz`) and unpacked during builds. After adding one, run `./tools/assets.py pack` and commit the `.xz` files (or `.xz.000`, `.xz.001`, ... for split archives).  

`./tools/assets.py status` shows what's packed.

## License

Apache License 2.0, see [LICENSE](LICENSE). Bundled avbtool is MIT-licensed;
see [tools/avb/LICENSE](tools/avb/LICENSE). Third-party files under `patches/`
have separate terms; see [NOTICE](NOTICE).
