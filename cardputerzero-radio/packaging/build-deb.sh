#!/usr/bin/env bash
# Build the installable CardputerZero .deb.
#
# The layout mirrors the stock CardputerZero apps: payload under
# /usr/share/APPLaunch/apps/<id>, a launcher stub in .../bin, a desktop entry
# in .../applications and the icon in .../share/images.
set -euo pipefail

PKG="cardputerzero-radio"
ARCH="${DEB_ARCH:-arm64}"
REVISION="${DEB_REVISION:-1}"
MAINTAINER="${DEB_MAINTAINER:-churchja <jason.a.church@gmail.com>}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="${DIST_DIR:-$ROOT/dist}"

VERSION="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$ROOT/cpzradio/config.py")"
if [ -z "$VERSION" ]; then
  echo "error: could not read VERSION from cpzradio/config.py" >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"

echo "==> byte-compile check"
"$PYTHON" -m compileall -q "$ROOT/app.py" "$ROOT/cpzradio" >/dev/null

echo "==> generating icon"
"$PYTHON" "$HERE/make_icon.py" "$HERE/icon.png" >/dev/null

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

APPDIR="$STAGE/usr/share/APPLaunch/apps/$PKG"
mkdir -p \
  "$STAGE/DEBIAN" \
  "$APPDIR" \
  "$STAGE/usr/share/APPLaunch/bin" \
  "$STAGE/usr/share/APPLaunch/applications" \
  "$STAGE/usr/share/APPLaunch/share/images" \
  "$STAGE/usr/lib/systemd/user" \
  "$STAGE/usr/share/doc/$PKG"

echo "==> staging payload"
cp "$ROOT/app.py" "$APPDIR/"
cp -r "$ROOT/cpzradio" "$APPDIR/"
cp -r "$ROOT/data" "$APPDIR/"
# Never ship build residue.
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +

cat > "$STAGE/usr/share/APPLaunch/bin/$PKG" <<EOF
#!/bin/sh
exec python3 /usr/share/APPLaunch/apps/$PKG/app.py "\$@"
EOF
chmod 755 "$STAGE/usr/share/APPLaunch/bin/$PKG"

cp "$HERE/$PKG.desktop" "$STAGE/usr/share/APPLaunch/applications/"
cp "$HERE/icon.png" "$STAGE/usr/share/APPLaunch/share/images/$PKG.png"
cp "$HERE/$PKG.service" "$STAGE/usr/lib/systemd/user/"
cp "$ROOT/LICENSE" "$STAGE/usr/share/doc/$PKG/copyright"

INSTALLED_KB="$(du -sk "$STAGE" | cut -f1)"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION-$REVISION
Architecture: $ARCH
Maintainer: $MAINTAINER
Section: APPLaunch
Priority: optional
Installed-Size: $INSTALLED_KB
Depends: python3 (>= 3.11), python3-pil, python3-evdev, python3-numpy, mpv, alsa-utils, fonts-dejavu-core
Homepage: https://github.com/churchja/cardputerzero-radio
Description: Internet radio player for the CardputerZero
 Streams internet radio on the M5Stack CardputerZero's 320x170 panel.
 Includes a curated station list, Radio Browser search, favourites,
 scrolling ICY track metadata, a sleep timer, a wake-to-radio alarm,
 stream recording and an optional boot-to-radio autostart toggle.
EOF

mkdir -p "$DIST"
OUTPUT="$DIST/${PKG}_${VERSION}-${REVISION}_${ARCH}.deb"
echo "==> building $OUTPUT"
dpkg-deb --root-owner-group --build "$STAGE" "$OUTPUT" >/dev/null

echo "==> done"
dpkg-deb --info "$OUTPUT" | sed 's/^/    /'
echo
echo "Contents:"
dpkg-deb --contents "$OUTPUT" | awk '{print "    " $6, $7, $8}'
echo
echo "$OUTPUT"
