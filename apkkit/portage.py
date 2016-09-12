"""The entry point to APK Kit from Portage.

This module serves as the "bridge" from Portage land (ebuild) to Adélie land
(APK).  Most of EAPI=5 is supported, with a few extensions.  More information is
below.  The most important bit of information is that this package is still in
ACTIVE DEVELOPMENT and may be rough around the edges!

EAPI=0
======

Supported
---------
* Slots: appended to the package name, i.e. dev-lang/python:3.4 = python3.4.

Unsupported
-----------
* ! = "unspecified" block: always treated as weak per EAPI=2.


EAPI=1
======

Supported
---------
* Slot dependencies: 'mangled' as above for slots.

Unsupported
-----------


EAPI=2
======

Supported
---------
* ! and !! blocks: ! will cause pre-install to warn, !! will emit a conflict in
  PKGINFO.

Unsupported
-----------


EAPI=3
======

Supported
---------

Unsupported
-----------


EAPI=4
======

Supported
---------
* pkg_pretend: this is run on the target (binary) system before pre-install.

Unsupported
-----------
* pkg_info: so far, no way for this to run on the target has been found.


EAPI=5
======

Supported
---------

Unsupported
-----------
* Subslots: not yet supported, will choke.


Extensions
==========
* Triggers: EAPI=5-adelie adds trigger support.  For a really contrived example:

```
EAPI=5-adelie
[...]
TRIGGER_ON="/usr/share/fonts:/usr/X11R7/fonts"

pkg_trigger() {
   fc-cache
}
```
"""

import logging
import os
import sys

from itertools import filterfalse
import portage
from portage.dep import Atom, use_reduce

from apkkit.base.package import Package
from apkkit.io.apkfile import APKFile


logging.basicConfig(level=logging.DEBUG)


ARCH_MAP = {'amd64': 'x86_64', 'hppa': 'parisc'}
"""Mapping for architectures that have the wrong name in Portage."""


# for some reason, it thinks there's no portage.db.  pylint: disable=no-member
VARDB = portage.db['/']['vartree'].dbapi
# pylint: enable=no-member


def _fatal(msg):
    """Print a fatal error to the user.

    :param str msg:
        The message to print.
    """

    print('\033[01;31m *\033[01;39m An APK cannot be created.')
    print('\033[01;31m *\033[00;39m {msg}'.format(msg=msg))


def _maybe_xlat(pn, category):
    """Offers the ability to translate a package name.

    This is mainly useful for package names that exist in multiple categories,
    for instance 'dev-db/redis' and 'dev-ruby/redis' (redis-ruby).

    Requires at least an empty /etc/portage/package.xlat file.

    Thanks to ryao for pointing out this needs to be done, and Elizafox for the
    initial concept/prototype.

    :param str pn:
        The name of the package to possibly translate.

    :param str category:
        The category of the package to possibly translate.

    :returns str:
        The name to use in Adélie for the package.
    """

    root = os.environ.get('PORTAGE_CONFIGROOT', '')
    path = os.path.join(root, '/etc/portage/package.xlat')
    with open(path, 'r') as xlat_file:
        xlats = xlat_file.readlines()

    for xlat in xlats:
        atom, real = xlat.split('\t')
        real = real.rstrip()
        if '{cat}/{pn}'.format(cat=category, pn=pn) == atom:
            return real
    return pn


def _deps_need_an_adult(name, pvr, eapi='6'):
    """Since there are multiple options for a run-time dependency, ask the
    local file system which one to use.

    Looks in [PORTAGE_CONFIGROOT]/etc/portage/deps/name-pvr for the RDEPEND to
    use in Adélie.

    :param str name:
        The name of the package.

    :param str pvr:
        The version + revision of the package.

    :param str eapi:
        The EAPI to use.  Defaults to 6.

    :returns str:
        A list of dependencies.
    """

    root = os.environ.get('PORTAGE_CONFIGROOT', '')
    path = os.path.join(root, '/etc/portage/deps',
                        '{name}-{pvr}'.format(name=name, pvr=pvr))
    try:
        with open(path, 'r') as dep_file:
            deps = dep_file.readlines()
        return [Atom(dep, eapi=eapi) for dep in deps]
    except OSError as exc:
        _fatal('Could not find RDEPEND for {name}'.format(name=name))
        raise exc


def _translate_dep(dep):
    category, package = dep.cp.split('/', 1)

    if category == 'virtual':
        return 'v:' + package

    package = _maybe_xlat(package, category)
    if dep.slot:
        if dep.slot != "0":
            package += dep.slot
    elif package != 'ncurses':  # so especially broken it's special cased
        potentials = VARDB.match(dep)
        potential_slots = set([pot.slot for pot in potentials])
        if len(potential_slots) > 1:
            msg = 'Dependency for {name} has multiple candidate slots,'
            msg += ' and no single slot can be resolved.'
            _fatal(msg.format(name=dep))
            sys.exit(-1)
        elif len(potential_slots) == 1:
            slot = potential_slots.pop()
            if slot and slot != '0':
                package += slot
        else:
            pass  # We assume no slot.
    dep_op = dep.operator
    ver = dep.version

    if dep.blocker:
        if dep.use is None:
            package = '!' + package
        else:
            return None

    if dep_op is None and ver is None:
        # "Easy" dep.
        return package

    #if dep_op == '~':
    #    dep_op = '='  # broken
    # apk-tools/src/package.c:195
    # there is literally no other documentation for this format.
    return '{name}{op}{ver}'.format(name=package, op=dep_op, ver=ver)


def _maybe_package_provides(settings, pkgname):
    """Determine if this package provides SONAMEs."""

    provides = list()

    build_info = os.path.join(settings['PORTAGE_BUILDDIR'], 'build-info')
    provide_path = os.path.join(build_info, 'PROVIDES')
    if os.path.exists(provide_path):
        with open(provide_path, 'r') as provide_file:
            provide_lines = provide_file.readlines()
    else:
        provide_lines = []

    for line in provide_lines:
        if line.startswith(settings['ARCH'] + ': '):
            # We have some SONAMEs!  Chop the arch name off.
            sonames = line.split()[1:]
            provides += ['so:' + soname for soname in sonames]

    root = os.environ.get('PORTAGE_CONFIGROOT', '')
    virtual_path = os.path.join(root, '/etc/apkkit/virtual', pkgname)
    if os.path.exists(virtual_path):
        with open(virtual_path, 'r') as virtual_file:
            provides += virtual_file.readlines()

    return provides


def native(settings, mydbapi=None):
    """Take a Portage settings object and turn it into an APK.

    Surprisingly less difficult than it sounds, but surprisingly more difficult
    than it appears on second glance.

    :param settings:
        A Portage settings object.

    :param mydbapi:
        A Portage DBAPI object for the package.
    """
    params = {}

    if settings['CATEGORY'] == 'virtual':
        _fatal("Won't make an APK for a virtual/ package.")
        sys.exit(-1)

    params['name'] = _maybe_xlat(settings['PN'], settings['CATEGORY'])
    if 'SLOT' in settings and not settings['SLOT'].startswith('0/') and\
       settings['SLOT'] != '0':
        slot = settings['SLOT'].split('/')[0]
        params['name'] += slot
    params['version'] = settings['PVR']  # include -rX if necessary
    params['arch'] = ARCH_MAP.get(settings['ARCH'], settings['ARCH'])
    params['provides'] = list()

    cpv = '%s/%s' % (settings['CATEGORY'], settings['PF'])
    if mydbapi is None or not mydbapi.cpv_exists(cpv):
        _fatal('CPV does not exist or DBAPI is missing')
        sys.exit(-1)

    desc, url = mydbapi.aux_get(cpv, ('DESCRIPTION', 'HOMEPAGE'))
    params['description'] = desc
    params['url'] = url

    run_deps = use_reduce(mydbapi.aux_get(cpv, ('RDEPEND',)),
                          uselist=settings['USE'], opconvert=True,
                          token_class=Atom, eapi=settings['EAPI'])

    if any([isinstance(dep, list) for dep in run_deps]):
        run_deps = _deps_need_an_adult(params['name'], settings['PVR'],
                                       settings['EAPI'])

    params['depends'] = list(filterfalse(lambda x: x is None,
                                         map(_translate_dep, run_deps)))

    params['provides'] = _maybe_package_provides(settings, params['name'])

    package = Package(**params)
    out_path = os.path.join(settings.get('PKG_DIR', settings['PKGDIR']))
    apk = APKFile.create(package, settings['D'], out_path=out_path)

    return 0

if __name__ == '__main__':
    # pylint: disable=no-member
    native(os.environ, portage.db['/']['porttree'].dbapi)
