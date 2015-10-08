from apkkit.base.package import Package
import gzip  # Not used, but we need to raise ImportError if gzip isn't built.
import tarfile

class APKFile:
    """Represents an APK file on disk (or in memory)."""

    def __init__(self, filename, mode='r'):
        self.tar = tarfile.open(filename, mode)
        self.package = Package.from_pkginfo(self.tar.extractfile('.PKGINFO'))
