#!/usr/bin/env bash
#
# Upload files to GoFile (https://gofile.io) via CLI.
# Reference: https://github.com/Sushrut1101/GoFile-Upload
# Supports multi-file uploads, server failover, GitHub Step Summary,
# and appending download links to release notes.
#

set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <file1> [file2 ...]" >&2
  exit 1
fi

# Ensure curl and jq are available
if ! command -v curl >/dev/null 2>&1; then
  echo "Error: curl is required but not installed." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is required but not installed." >&2
  exit 1
fi

echo "=== GoFile Upload ===" >&2

# Query GoFile API for available servers
SERVERS_JSON=$(curl -s --connect-timeout 10 https://api.gofile.io/servers || true)
SERVER_LIST=$(echo "$SERVERS_JSON" | jq -r '(.data.servers[]?.name // empty), (.data.serversAllZone[]?.name // empty)' 2>/dev/null | head -n 6)

if [ -z "$SERVER_LIST" ]; then
  SERVER_LIST="store-na-phx-1 store-na-phx-3 store3 store8"
fi

UPLOADED_ITEMS=()

for FILE in "$@"; do
  if [ ! -f "$FILE" ]; then
    echo "Warning: File '$FILE' does not exist or is not a regular file. Skipping." >&2
    continue
  fi

  FNAME=$(basename "$FILE")
  FSIZE=$(ls -lh "$FILE" | awk '{print $5}')
  echo "Uploading: $FNAME ($FSIZE)..." >&2

  LINK=""
  for SERVER in $SERVER_LIST; do
    echo "  Attempting upload via server: $SERVER" >&2

    # Primary endpoint: /contents/uploadfile
    RESP=$(curl -# --connect-timeout 30 -m 3600 -F "file=@$FILE" "https://${SERVER}.gofile.io/contents/uploadfile" 2>/dev/null || true)
    LINK=$(echo "$RESP" | jq -r '.data.downloadPage // empty' 2>/dev/null)

    # Fallback endpoint: /uploadFile
    if [ -z "$LINK" ] || [ "$LINK" = "null" ]; then
      RESP=$(curl -# --connect-timeout 30 -m 3600 -F "file=@$FILE" "https://${SERVER}.gofile.io/uploadFile" 2>/dev/null || true)
      LINK=$(echo "$RESP" | jq -r '.data.downloadPage // empty' 2>/dev/null)
    fi

    if [ -n "$LINK" ] && [ "$LINK" != "null" ]; then
      echo "  Upload successful: $LINK" >&2
      UPLOADED_ITEMS+=("$FNAME|$FSIZE|$LINK")
      break
    else
      echo "  Server $SERVER failed or timed out. Trying next server..." >&2
    fi
  done

  if [ -z "$LINK" ] || [ "$LINK" = "null" ]; then
    echo "Error: Failed to upload $FNAME to GoFile on all servers." >&2
  fi
done

if [ "${#UPLOADED_ITEMS[@]}" -eq 0 ]; then
  echo "Warning: No files were uploaded to GoFile." >&2
  exit 0
fi

# Print summary to stdout
echo ""
echo "=== GoFile Upload Summary ==="
for item in "${UPLOADED_ITEMS[@]}"; do
  IFS='|' read -r name size link <<< "$item"
  echo " - $name ($size): $link"
done
echo ""

# Output to GITHUB_STEP_SUMMARY if running in GitHub Actions
if [ -n "${GITHUB_STEP_SUMMARY:-}" ] && [ -w "$GITHUB_STEP_SUMMARY" ]; then
  {
    echo ""
    echo "### 🌐 GoFile Uploads"
    echo ""
    echo "| File | Size | Download Link |"
    echo "|---|---|---|"
    for item in "${UPLOADED_ITEMS[@]}"; do
      IFS='|' read -r name size link <<< "$item"
      echo "| \`$name\` | $size | [Download from GoFile]($link) |"
    done
    echo ""
  } >> "$GITHUB_STEP_SUMMARY"
fi

# Append to RELEASE_NOTES_FILE if available
NOTES_FILE="${RELEASE_NOTES_FILE:-}"
if [ -z "$NOTES_FILE" ]; then
  # Look for release_notes.md in the directory of the first file
  FIRST_DIR=$(dirname "$1")
  if [ -f "$FIRST_DIR/release_notes.md" ]; then
    NOTES_FILE="$FIRST_DIR/release_notes.md"
  fi
fi

if [ -n "$NOTES_FILE" ] && [ -f "$NOTES_FILE" ]; then
  echo "Appending GoFile links to $NOTES_FILE..." >&2
  {
    echo ""
    echo "### 🌐 GoFile Download Mirrors"
    echo ""
    echo "| File | Size | Download Link |"
    echo "|---|---|---|"
    for item in "${UPLOADED_ITEMS[@]}"; do
      IFS='|' read -r name size link <<< "$item"
      echo "| \`$name\` | $size | [Download from GoFile]($link) |"
    done
    echo ""
  } >> "$NOTES_FILE"
fi

# Set output variable in GITHUB_OUTPUT if present
if [ -n "${GITHUB_OUTPUT:-}" ] && [ -w "$GITHUB_OUTPUT" ]; then
  echo "gofile_uploaded=true" >> "$GITHUB_OUTPUT"
  LINKS_STR=$(for item in "${UPLOADED_ITEMS[@]}"; do IFS='|' read -r _ _ link <<< "$item"; echo "$link"; done | tr '\n' ' ' | xargs)
  echo "gofile_links=$LINKS_STR" >> "$GITHUB_OUTPUT"
fi
