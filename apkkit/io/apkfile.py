"""I/O classes and helpers for APK files."""

from apkkit.base.package import Package
from apkkit.io.util import recursive_size
import glob
import gzip
import hashlib
import io
import logging
import os
import shutil
from subprocess import Popen, PIPE
import sys
import tarfile
from tempfile import mkstemp


LOGGER = logging.getLogger(__name__)


try:
    import rsa
except ImportError:
    LOGGER.warning("RSA module is not available - signing packages won't work.")


def _ensure_no_debug(filename):
    """tarfile exclusion predicate to ensure /usr/lib/debug isn't included.

    :returns bool: True if the file is a debug file, otherwise False.
    """
    return 'usr/lib/debug' in filename


def _sign_control(control, privkey, pubkey):
    """Sign control.tar.

    :param control:
        A file-like object representing the current control.tar.

    :param privkey:
        The path to the private key.

    :param pubkey:
        The public name of the public key (this will be included in the
        signature, so it must match /etc/apk/keys/<name>).

    :returns:
        A file-like object representing the signed control.tar.
    """
    control.seek(0)
    control_hash = hashlib.sha256(control.read())

    signature = b'signed sha256sum here'
    iosignature = io.BytesIO(signature)

    new_control = io.BytesIO()
    new_control_tar = tarfile.open(mode='w', fileobj=new_control)
    tarinfo = tarfile.TarInfo('.SIGN.RSA.' + pubkey)
    tarinfo.size = len(signature)
    new_control_tar.addfile(tarinfo, fileobj=iosignature)

    control.seek(0)
    new_control.seek(0, 2)

    shutil.copyfileobj(control, new_control)
    new_control.seek(0)
    return new_control


class APKFile:
    """Represents an APK file on disk (or in memory)."""

    def __init__(self, filename=None, mode='r', fileobj=None, package=None):
        if filename is not None:
            self.tar = tarfile.open(filename, mode)
        elif fileobj is not None:
            self.tar = tarfile.open(mode=mode, fileobj=fileobj)
            self.fileobj = fileobj
        else:
            raise ValueError("No filename or file object specified.")

        if package is None:
            self.package = Package.from_pkginfo(
                self.tar.extractfile('.PKGINFO')
            )
        else:
            self.package = package

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
        LOGGER.info('Creating APK from data in: %s', datadir)
        package.size = recursive_size(datadir)

        # XXX TODO BAD RUN AWAY
        # eventually we need to just a write tarfile replacement that can do
        # the sign-mangling required for APK
        if sys.version_info[:2] >= (3, 5):
            mode = 'x'
        else:
            mode = 'w'

        fd, pkg_data_path = mkstemp(prefix='apkkit-', suffix='.tar')
        gzio = io.BytesIO()

        LOGGER.info('Creating data.tar...')
        with os.fdopen(fd, 'xb') as fdfile:
            with tarfile.open(mode=mode, fileobj=fdfile,
                              format=tarfile.PAX_FORMAT) as data:
                for item in glob.glob(datadir + '/*'):
                    data.add(item, arcname=os.path.basename(item),
                             exclude=_ensure_no_debug)

            LOGGER.info('Hashing data.tar [pass 1]...')
            fdfile.seek(0)
            abuild_pipe = Popen(['abuild-tar', '--hash'], stdin=fdfile,
                                stdout=PIPE)

            LOGGER.info('Compressing data...')
            with gzip.GzipFile(mode='wb', fileobj=gzio) as gzobj:
                gzobj.write(abuild_pipe.communicate()[0])

        # make the datahash
        if data_hash:
            LOGGER.info('Hashing data.tar [pass 2]...')
            gzio.seek(0)
            hasher = getattr(hashlib, hash_method)(gzio.read())
            package.data_hash = hasher.hexdigest()

        # if we made the hash, we need to seek back again
        # if we didn't, we haven't seeked back yet
        gzio.seek(0)

        # we are finished with fdfile (data.tar), now let's make control
        LOGGER.info('Creating package header...')

        control = io.BytesIO()
        control_tar = tarfile.open(mode=mode, fileobj=control)
        ioinfo = io.BytesIO(package.to_pkginfo().encode('utf-8'))
        tarinfo = tarfile.TarInfo('.PKGINFO')
        ioinfo.seek(0, 2)
        tarinfo.size = ioinfo.tell()
        ioinfo.seek(0)
        control_tar.addfile(tarinfo, fileobj=ioinfo)

        # we do NOT close control_tar yet, because we don't want the end of
        # archive written out.
        if sign:
            LOGGER.info('Signing package...')
            signfile = os.getenv('PACKAGE_PRIVKEY', signfile)
            pubkey = os.getenv('PACKAGE_PUBKEY',
                               os.path.basename(signfile) + '.pub')
            control = _sign_control(control, signfile, pubkey)

        LOGGER.info('Compressing package header...')
        controlgz = io.BytesIO()
        with gzip.GzipFile(mode='wb', fileobj=controlgz) as gzobj:
            shutil.copyfileobj(control, gzobj)
        control_tar.close()  # we are done with it now
        controlgz.seek(0)

        LOGGER.info('Creating package file (in memory)...')
        combined = io.BytesIO()
        shutil.copyfileobj(controlgz, combined)
        shutil.copyfileobj(gzio, combined)

        controlgz.close()
        gzio.close()

        return cls(fileobj=combined, package=package)

    def write(self, path):
        LOGGER.info('Writing APK to %s', path)
        self.fileobj.seek(0)
        with open(path, 'xb') as new_package:
            shutil.copyfileobj(self.fileobj, new_package)
