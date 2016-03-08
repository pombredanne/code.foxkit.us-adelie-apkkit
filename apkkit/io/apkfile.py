"""I/O classes and helpers for APK files."""

from apkkit.base.package import Package
from apkkit.io.util import recursive_size
from getpass import getpass
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
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:
    LOGGER.warning("cryptography module is unavailable - can't sign packages.")


FILTERS = None


def _add_filter_func(func):
    """Add a callable to filter files out of the created data.tar.gz.

    :param callable func:
        The callable.  It will be passed a single parameter, filename.
    """
    global FILTERS

    if FILTERS is None:
        FILTERS = set()

    FILTERS.add(func)


def _tar_filter(filename):
    """tarfile exclusion predicate that calls all defined filter functions."""
    global FILTERS

    results = [func(filename) for func in FILTERS]
    return all(results)


def _ensure_no_debug(filename):
    """tarfile exclusion predicate to ensure /usr/lib/debug isn't included.

    :returns bool: True if the file is a debug file, otherwise False.
    """
    return 'usr/lib/debug' in filename


def _sign_control(control, privkey, pubkey):
    """Sign control.tar.

    :param control:
        A file-like object representing the current control.tar.gz.

    :param privkey:
        The path to the private key.

    :param pubkey:
        The public name of the public key (this will be included in the
        signature, so it must match /etc/apk/keys/<name>).

    :returns:
        A file-like object representing the signed control.tar.gz.
    """
    signature = None

    with open(privkey, "rb") as key_file:
        #password = getpass()
        #if password != '':
        #    password.encode('utf-8')
        #else:
        password = None

        private_key = serialization.load_pem_private_key(
            key_file.read(), password=password, backend=default_backend()
        )
        signer = private_key.signer(padding.PKCS1v15(), hashes.SHA256())
        signer.update(control.getvalue())
        signature = signer.finalize()
        del signer
        del private_key

    iosignature = io.BytesIO(signature)

    new_control = io.BytesIO()
    new_control_tar = tarfile.open(mode='w', fileobj=new_control)
    tarinfo = tarfile.TarInfo('.SIGN.RSA.' + pubkey)
    tarinfo.size = len(signature)
    new_control_tar.addfile(tarinfo, fileobj=iosignature)

    new_control.seek(0)
    controlgz = io.BytesIO()
    with gzip.GzipFile(mode='wb', fileobj=controlgz) as gzobj:
        shutil.copyfileobj(new_control, gzobj)

    control.seek(0)
    controlgz.seek(0, 2)
    shutil.copyfileobj(control, controlgz)

    controlgz.seek(0)

    new_control_tar.close()
    new_control.close()
    return controlgz


def _make_data_tgz(datadir, mode):
    """Make the data.tar.gz file.

    :param str datadir:
        The base directory for the package's data.

    :param str mode:
        The mode to open the file ('x' or 'w').

    :returns:
        A file-like object representing the data.tar.gz file.
    """
    fd, pkg_data_path = mkstemp(prefix='apkkit-', suffix='.tar')
    gzio = io.BytesIO()

    with os.fdopen(fd, 'xb') as fdfile:
        with tarfile.open(mode=mode, fileobj=fdfile,
                          format=tarfile.PAX_FORMAT) as data:
            for item in glob.glob(datadir + '/*'):
                data.add(item, arcname=os.path.basename(item),
                         exclude=_tar_filter)

        LOGGER.info('Hashing data.tar [pass 1]...')
        fdfile.seek(0)
        abuild_pipe = Popen(['abuild-tar', '--hash'], stdin=fdfile,
                            stdout=PIPE)

        LOGGER.info('Compressing data...')
        with gzip.GzipFile(mode='wb', fileobj=gzio) as gzobj:
            gzobj.write(abuild_pipe.communicate()[0])

    return gzio


def _make_control_tgz(package, mode):
    """Make the control.tar.gz file.

    :param package:
        The :py:class:`~apkkit.base.package.Package` instance for the package.

    :param str mode:
        The mode to use for control.tar ('x' or 'w').

    :returns:
        A file-like object representing the control.tar.gz file.
    """
    gzio = io.BytesIO()
    control = io.BytesIO()

    control_tar = tarfile.open(mode=mode, fileobj=control)

    ioinfo = io.BytesIO(package.to_pkginfo().encode('utf-8'))
    tarinfo = tarfile.TarInfo('.PKGINFO')
    ioinfo.seek(0, 2)
    tarinfo.size = ioinfo.tell()
    ioinfo.seek(0)

    control_tar.addfile(tarinfo, fileobj=ioinfo)

    control.seek(0)
    with gzip.GzipFile(mode='wb', fileobj=gzio) as control_obj:
        shutil.copyfileobj(control, control_obj)

    control_tar.close()

    return gzio


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
               hash_method='sha256', **kwargs):
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
            The hash method to use for hashing the data - default is sha256.
        """

        # ensure no stale filters are applied.
        global FILTERS
        FILTERS = None

        if 'filters' in kwargs:
            [_add_filter_func(func) for func in kwargs.pop('filters')]

        # XXX what about -debug split packages?  they need this.
        _add_filter_func(_ensure_no_debug)

        LOGGER.info('Creating APK from data in: %s', datadir)
        package.size = recursive_size(datadir)

        # XXX TODO BAD RUN AWAY
        # eventually we need to just a write tarfile replacement that can do
        # the sign-mangling required for APK
        if sys.version_info[:2] >= (3, 5):
            mode = 'x'
        else:
            mode = 'w'

        LOGGER.info('Creating data.tar...')
        data_gzio = _make_data_tgz(datadir, mode)

        # make the datahash
        if data_hash:
            LOGGER.info('Hashing data.tar [pass 2]...')
            data_gzio.seek(0)
            hasher = getattr(hashlib, hash_method)(data_gzio.read())
            package.data_hash = hasher.hexdigest()

        # if we made the hash, we need to seek back again
        # if we didn't, we haven't seeked back yet
        data_gzio.seek(0)

        # we are finished with fdfile (data.tar), now let's make control
        LOGGER.info('Creating package header...')
        controlgz = _make_control_tgz(package, mode)

        # we do NOT close control_tar yet, because we don't want the end of
        # archive written out.
        if sign:
            LOGGER.info('Signing package...')
            signfile = os.getenv('PACKAGE_PRIVKEY', signfile)
            pubkey = os.getenv('PACKAGE_PUBKEY',
                               os.path.basename(signfile) + '.pub')
            controlgz = _sign_control(controlgz, signfile, pubkey)

        LOGGER.info('Creating package file (in memory)...')
        combined = io.BytesIO()
        shutil.copyfileobj(controlgz, combined)
        shutil.copyfileobj(data_gzio, combined)

        controlgz.close()
        data_gzio.close()

        return cls(fileobj=combined, package=package)

    def write(self, path):
        LOGGER.info('Writing APK to %s', path)
        self.fileobj.seek(0)
        with open(path, 'xb') as new_package:
            shutil.copyfileobj(self.fileobj, new_package)
