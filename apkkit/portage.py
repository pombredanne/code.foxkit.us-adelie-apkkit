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

from apkkit.base.package import Package
from apkkit.io.apkfile import APKFile
import logging
import os
from portage import db
from portage.dep import Atom, use_reduce
import sys


logging.basicConfig(level=logging.DEBUG)


ARCH_MAP = {'amd64': 'x86_64', 'hppa': 'parisc'}
"""Mapping for architectures that have the wrong name in Portage."""


VARDB = db['/']['vartree'].dbapi


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

    return pn


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

    params['name'] = _maybe_xlat(settings['PN'], settings['CATEGORY'])
    if 'SLOT' in settings and not settings['SLOT'].startswith('0/') and\
       settings['SLOT'] != '0':
        slot = settings['SLOT'].split('/')[0]
        params['name'] += slot
    params['version'] = settings['PVR']  # include -rX if necessary
    params['arch'] = ARCH_MAP.get(settings['ARCH'], settings['ARCH'])
    params['provides'] = list()
    params['depends'] = list()

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
    for dep in run_deps:
        category, package = dep.cp.split('/', 1)
        package = _maybe_xlat(package, category)
        if dep.slot:
            if dep.slot != "0":
                package += dep.slot
        elif package != 'ncurses':  # so especially broken it's special cased
            potentials = VARDB.match(dep)
            potential_slots = set([pot.slot for pot in potentials])
            if len(potential_slots) > 1:
                msg = 'Dependency for {name} has multiple candidate slots,'
                msg += ' and no single slot can be determined.'
                _fatal(msg.format(name=dep))
                sys.exit(-1)
            elif len(potential_slots) == 1:
                slot = potential_slots.pop()
                if slot and slot != '0':
                    package += slot
            else:
                pass  # We assume no slot.
        op = dep.operator
        ver = dep.version

        if dep.blocker:
            package = '!' + package

        if op is None and ver is None:
            # "Easy" dep.
            params['depends'].append(package)
            continue

        # apk-tools/src/package.c:195
        # there is literally no other documentation for this format.
        apk_format = '{name}{op}{ver}'.format(name=package, op=op, ver=ver)
        params['depends'].append(apk_format)

    package = Package(**params)
    apk = APKFile.create(package, settings['D'])
    filename = "{name}-{ver}.apk".format(name=package.name, ver=package.version)
    apk.write(os.path.join(settings.get('PKG_DIR', settings['PKGDIR']),
                           filename))

    return 0

if __name__ == '__main__':
    import portage
    print("You are calling from the shell, this is not supported!")
    native(os.environ, portage.db['/']['porttree'].dbapi)
