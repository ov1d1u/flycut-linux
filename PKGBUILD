# Build from this checkout: makepkg -si
pkgname=flycut-linux
pkgver=0.1.0
pkgrel=6
pkgdesc='Transparent clipboard history for KDE Plasma Wayland'
arch=('any')
url='https://github.com/ov1d1u/flycut-linux'
license=('MIT')
depends=('python' 'pyside6' 'qt6-wayland' 'python-dbus' 'python-gobject'
         'wl-clipboard>=2.3.0' 'ydotool' 'xdg-desktop-portal' 'xdg-desktop-portal-kde')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
source=()
sha256sums=()

prepare() {
    # Deliberately build the local checkout, including uncommitted Linux changes.
    cp -a "$startdir/flycut_linux" "$startdir/pyproject.toml" "$startdir/README.md" "$startdir/packaging" "$startdir/tests" "$srcdir/"
    cp "$startdir/LICENSE" "$srcdir/LICENSE"
}

build() {
    cd "$srcdir"
    python -m build --wheel --no-isolation
}

check() {
    cd "$srcdir"
    QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
}

package() {
    cd "$srcdir"
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 packaging/org.flycut.FlycutLinux.desktop "$pkgdir/usr/share/applications/org.flycut.FlycutLinux.desktop"
    install -Dm644 flycut_linux/flycut-linux.svg "$pkgdir/usr/share/icons/hicolor/scalable/apps/flycut-linux.svg"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
}
