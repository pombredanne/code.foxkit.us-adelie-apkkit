"""I/O classes and helpers for APK files."""

import glob
import gzip
import hashlib
import io
import logging
import os
import shutil
import sys
import tarfile
import yaml

from copy import deepcopy
from fnmatch import fnmatch
from functools import partial
from itertools import chain
from pathlib import Path
from subprocess import Popen, PIPE
from tempfile import mkstemp

from apkkit.base.package import Package
from apkkit.io.util import recursive_size

LOGGER = logging.getLogger(__name__)


try:
    # we need LOGGER.  pylint: disable=wrong-import-order,wrong-import-position
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:
    LOGGER.warning("cryptography module is unavailable - can't sign packages.")


def load_global_split():
    """Load global split package information from split-global.conf."""

    try:
        with open('/etc/apkkit/split/global.conf') as splitconf:
            splits = list(yaml.safe_load_all(splitconf))
    except OSError:
        LOGGER.error('No global split package information file.')
        splits = []

    return splits


def load_package_split(package):
    """Load specific split package information for the specified package.

    :param package:
        The package for which to load information.
    """

    path = '/etc/apkkit/split/{name}'.format(name=package.name)
    try:
        with open(path + '/' + package.version + '.conf') as splitconf:
            splits = list(yaml.safe_load_all(splitconf))
    except OSError:
        try:
            with open(path + '.conf') as splitconf:
                splits = list(yaml.safe_load_all(splitconf))
        except OSError:
            LOGGER.debug('No split package information for %s', package.name)
            splits = []

    return splits


def skip_split():
    """Return the list of packages that should not be split globally."""

    try:
        with open('/etc/apkkit/split/skip.conf') as skipconf:
            skips = [pkg[:-1] for pkg in skipconf.readlines()]
    except OSError:
        LOGGER.error('No global skip-split package information file.')
        skips = []

    return skips


def path_components(path):
    """Find all directories that make up a full path."""

    components = [path]
    current = path
    while current and current != '/':
        current, _ = os.path.split(current)
        components.append(current)
    return components


def split_filter(split_info, tar_info):
    """Determine if a file should be included in a split package.

    :param dict split_info:
        The split parameters loaded from configuration.

    :param tar_info:
        The TarInfo object to inspect.
    """

    paths = set(split_info['paths'])
    for path in split_info['paths']:
        for component in path_components(path):
            paths.add(component)

    if any([tar_info.name.startswith(path) for path in split_info['paths']]) or\
       any([tar_info.name == component for component in paths]) or\
       any([fnmatch(tar_info.name, path) for path in split_info['paths']]):
        return tar_info

    return None


def base_filter(exclude_from_base, tar_info):
    """Determine if a file should be included in a base package.

    :param list exclude_from_base:
        A list of paths to exclude from the base package.

    :param tar_info:
        The TarInfo object to inspect.
    """

    if any([tar_info.name.startswith(path) for path in exclude_from_base]) or\
       any([fnmatch(tar_info.name, path) for path in exclude_from_base]):
        return None

    return tar_info

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


def _make_data_tgz(datadir, mode, package, my_filter=None):
    """Make the data.tar.gz file.

    :param str datadir:
        The base directory for the package's data.

    :param str mode:
        The mode to open the file ('x' or 'w').

    :param package:
        The Package object for this data.tar.gz file.  The 'size' parameter
        will be set to the size of the data included.

    :param callable my_filter:
        A function passed to tarfile.add to filter contents.  Defaults to None.
        If None, all files in datadir will be added.

    :returns:
        A file-like object representing the data.tar.gz file.
    """
    fd, pkg_data_path = mkstemp(prefix='apkkit-', suffix='.tar')
    gzio = io.BytesIO()

    if my_filter is None:
        my_filter = lambda x: x

    with os.fdopen(fd, 'xb') as fdfile:
        with tarfile.open(mode=mode, fileobj=fdfile,
                          format=tarfile.PAX_FORMAT) as data:
            for item in glob.glob(datadir + '/*'):
                data.add(item, arcname=os.path.basename(item), filter=my_filter)
            members = [member for member in data.getmembers()
                       if member.isfile()]

        if len(members) == 0:
            return None

        fdfile.seek(0, 2)
        package.size = fdfile.tell()
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

    @staticmethod
    def _create_file(package, datadir, sign, signfile, data_hash, hash_method,
                     mode, my_filter):
        LOGGER.info('Creating data.tar...')
        data_gzio = _make_data_tgz(datadir, mode, package, my_filter)
        if data_gzio is None:
            LOGGER.info('Empty package.  Nothing to write.')
            return None

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

        return combined


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

        LOGGER.info('Creating APK from data in: %s', datadir)

        # XXX TODO BAD RUN AWAY
        # eventually we need to just a write tarfile replacement that can do
        # the sign-mangling required for APK
        if sys.version_info[:2] >= (3, 5):
            mode = 'x'
        else:
            mode = 'w'

        files = []

        if package.name in skip_split():
            splits = []
        else:
            splits = load_global_split()
            splits += load_package_split(package)
            splits = [split for split in splits if split is not None]

        exclude_from_base = []

        for split in splits:
            split_package = deepcopy(package)
            split_package._pkgname = split['name'].format(name=package.name)

            if 'desc' in split:
                split_package._pkgdesc += split['desc']

            if 'depends' in split:
                split_package._depends = split['depends']
            else:
                split_package._depends = [package.name]

            if 'provides' in split:
                split_package._provides = split['provides']
            else:
                split_package._provides = []

            file_matches = []
            for path in split['paths']:
                if '*' in path:
                    file_matches += [str(f.relative_to(datadir))
                                     for f in Path(datadir).glob(path)]
                    split['paths'].remove(path)

            split['paths'] += file_matches

            LOGGER.info('Probing for split package: %s', split_package.name)
            combined = APKFile._create_file(split_package, datadir, sign,
                                            signfile, data_hash, hash_method,
                                            mode, partial(split_filter, split))
            if combined:
                files.append(cls(fileobj=combined, package=split_package))

            exclude_from_base += [path for path in split['paths']]

        LOGGER.info('Processing main package: %s', package.name)
        combined = APKFile._create_file(package, datadir, sign, signfile,
                                        data_hash, hash_method, mode,
                                        partial(base_filter, exclude_from_base))
        if combined:
            files.append(cls(fileobj=combined, package=package))

        out_path = kwargs.pop('out_path', None)
        if out_path:
            for apk_file in files:
                name = "{name}-{ver}.apk".format(name=apk_file.package.name,
                                                 ver=apk_file.package.version)
                apk_file.write(os.path.join(out_path, name))

    def write(self, path):
        """Write the APK currently loaded to a file at path."""

        LOGGER.info('Writing APK to %s', path)
        self.fileobj.seek(0)
        with open(path, 'xb') as new_package:
            shutil.copyfileobj(self.fileobj, new_package)
