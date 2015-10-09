"""I/O classes and helpers for APK files."""

from apkkit.base.package import Package
# Not used, but we need to raise ImportError if gzip isn't built.
import gzip  # pylint: disable=unused-import
import tarfile


class APKFile:
    """Represents an APK file on disk (or in memory)."""

    def __init__(self, filename, mode='r'):
        self.tar = tarfile.open(filename, mode)
        self.package = Package.from_pkginfo(self.tar.extractfile('.PKGINFO'))

    @classmethod
    def create(cls, package, datadir, sign=True, signfile=None, data_hash=True,
               hash_method='sha1'):
        """Create an APK file in memory from a package and data directory.

        :param package:
            A :py:class:`Package` instance that describes the package.

        :param datadir:
            The path to the directory containing the package's data.

        :param bool sign:
            Whether to sign the package (default True).

        :param signfile:
            The path to the GPG key to sign the package with.

        :param bool data_hash:
            Whether to hash the data (default True).

        :param str hash_method:
            The hash method to use for hashing the data - default is sha1 to
            maintain compatibility with upstream apk-tools.
        """
        raise NotImplementedError
