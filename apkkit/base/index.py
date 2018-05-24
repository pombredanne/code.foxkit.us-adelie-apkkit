"""Contains the Index class and related helper classes and functions."""

from io import BytesIO
import logging
import os
import tarfile

from apkkit.base.package import Package


INDEX_LOGGER = logging.getLogger(__name__)
"""The logger for messages coming from this module."""


try:
    # logger needed.  pylint: disable=wrong-import-order,wrong-import-position
    import requests
    HAVE_REQUESTS = True
    """Determines if the `requests` module is available."""
except ImportError:
    INDEX_LOGGER.warning("You need install Requests to load indexes over "
            "the network.")
    HAVE_REQUESTS = False
    """Determines if the `requests` module is available."""


class Index:
    """The base index class."""

    def __init__(self, packages=None, description=None, url=None, **kwargs):
        """Initialise an Index object.

        :param list packages:
            The packages available in the repository this index represents.

        :param str description:
            (Recommended) The description of the repository this index
            represents.  Typically, something like "system" or "user".

        :param str url:
            (Optional) The URL to download the index from.  All other
            parameters are ignored if this is specified.

        .. note:: URL downloading requires the Requests module to be installed.
        """

        if HAVE_REQUESTS and url is not None:
            self._url = url
            resp = requests.get(url)
            if resp.status_code != 200:
                INDEX_LOGGER.error("could not download %s: %d (%r)", url,
                                   resp.status_code, resp.text)
            else:
                self._fill_from_index_file(BytesIO(resp.content))
        else:
            if packages is None:
                raise ValueError("Packages are required.")
            self._pkgs = packages
            self._desc = description

        if len(kwargs) > 0:
            INDEX_LOGGER.warning("unknown kwargs in Index: %r", kwargs)

    @property
    def description(self):
        """The description of this repository."""
        return self._desc

    @property
    def packages(self):
        """The packages available in this repository."""
        return list(self._pkgs)

    @property
    def origins(self):
        """The names of all unique origin packages available in this repo."""
        return {pkg.origin for pkg in self._pkgs}

    def __repr__(self):
        if self._url is not None:
            return 'Index(url="{url}")'.format(url=self._url)
        return 'Index(packages=<{num} packages>, description="{desc}")'.format(
                num=len(self._pkgs), desc=self._desc)

    def to_raw(self):
        """Serialises this repository information into the APKINDEX format.

        :returns str: The APKINDEX for this package.  Unicode str, ready to be
                      written to a file.
        """
        raise NotImplemented("Not yet.")

    def _fill_from_index_file(self, buf):
        """Fill this `Index` object from the APKINDEX in `buf`."""
        if len(getattr(self, '_pkgs', list())) > 0:
            raise Exception("Attempt to fill an already filled Index")

        pkgs = list()
        params = {}
        param_map = {'P': 'name', 'V': 'version', 'A': 'arch', 'o': 'origin',
                     'T': 'description', 'p': 'provides', 'i': 'install_if',
                     'D': 'depends', 'U': 'url', 'I': 'size', 'r': 'replaces',
                     'L': 'license', 'q': 'replaces_priority', 'c': 'commit',
                     'm': 'maintainer', 't': 'builddate'}
        list_keys = {'p', 'D', 'i', 'r'}

        tar = None

        # assumption: "P" is the first line of each package.
        try:
            tar = tarfile.open(fileobj=buf)
            real_buf = tar.extractfile('APKINDEX')
        except:
            real_buf = buf

        for line in real_buf.readlines():
            if not isinstance(line, str):
                line = line.decode('utf-8')

            # Skip comments.
            if line[0] == '#':
                continue

            # separated by blank line
            if line == "\n" and params.get('name', None) is not None:
                pkgs.append(Package(**params))
                params.clear()
                continue

            if line.find(':') == -1:
                INDEX_LOGGER.warning('!!! malformed line? "%s" !!!', line)
                continue

            (key, value) = line.split(':', 1)
            key = key.strip()
            value = value.strip()

            if key in param_map:
                if key in list_keys:
                    params[param_map[key]] = value.split(' ')
                else:
                    params[param_map[key]] = value
            else:
                INDEX_LOGGER.info('!!! unrecognised APKINDEX key %s !!!', key)

        self._pkgs = pkgs

    @classmethod
    def from_raw(cls, buf):
        """Create a new :py:class:`Index` object from an existing APKINDEX.

        :param buf:
            The buffer to read from (whether file, BytesIO, etc).

        :returns:
            A :py:class:`Index` object with the details from the APKINDEX.
        """
        idx = cls(packages=list())
        idx._fill_from_index_file(buf)
        return idx

