#!/usr/bin/python
# debsecan - Debian Security Analyzer
# Copyright (C) 2005, 2006, 2007 Florian Weimer
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA

from _version import __version__

import copy
from cStringIO import StringIO
from optparse import OptionParser
import os
import os.path
import re
import socket
import sys
import time
import types
import urllib2
import zlib
import apt_pkg

apt_pkg.init()
try:
    version_compare = apt_pkg.version_compare
except AttributeError:
    version_compare = apt_pkg.VersionCompare

#
# From debian_support in the secure-testing repository.  Needs to be
# kept in sync manually.  (We duplicate here to avoid a library
# dependency, and make it easy to run the script even when it is not
# installed on the system.)


class ParseError(Exception):

    """An exception which is used to signal a parse failure.

    Attributes:

    filename - name of the file
    lineno - line number in the file
    msg - error message

    """

    def __init__(self, filename, lineno, msg):
        assert type(lineno) == types.IntType
        self.filename = filename
        self.lineno = lineno
        self.msg = msg

    def __str__(self):
        return self.msg

    def __repr__(self):
        return "ParseError(%s, %d, %s)" % (`self.filename`,
                                           self.lineno,
                                           `self.msg`)

    def printOut(self, file):
        """Writes a machine-parsable error message to file."""
        file.write("%s:%d: %s\n" % (self.filename, self.lineno, self.msg))
        file.flush()


class Version:

    """Version class which uses the original APT comparison algorithm."""

    def __init__(self, version):
        """Creates a new Version object."""
        assert type(version) == types.StringType, `version`
        assert version != ""
        self.__asString = version

    def __str__(self):
        return self.__asString

    def __repr__(self):
        return 'Version(%s)' % `self.__asString`

    def __cmp__(self, other):
        return version_compare(self.__asString, other.__asString)


class PackageFile:

    """A Debian package file.

    Objects of this class can be used to read Debian's Source and
    Packages files."""

    re_field = re.compile(r'^([A-Za-z][A-Za-z0-9-]+):(?:\s+(.*?))?\s*$')

    def __init__(self, name, fileObj=None):
        """Creates a new package file object.

        name - the name of the file the data comes from
        fileObj - an alternate data source; the default is to open the
                  file with the indicated name.
        """
        if fileObj is None:
            fileObj = safe_open(name)
        self.name = name
        self.file = fileObj
        self.lineno = 0

    def __iter__(self):
        line = self.file.readline()
        self.lineno += 1
        pkg = []
        while line:
            if line == '\n':
                if len(pkg) == 0:
                    self.raiseSyntaxError('expected package record')
                yield pkg
                pkg = []
                line = self.file.readline()
                self.lineno += 1
                continue

            match = self.re_field.match(line)
            if not match:
                self.raiseSyntaxError("expected package field, got " + `line`)
            (name, contents) = match.groups()
            contents = contents or ''

            while True:
                line = self.file.readline()
                self.lineno += 1
                if line and line[0] in " \t":
                    ncontents = line[1:]
                    if ncontents:
                        if ncontents[-1] == '\n':
                            ncontents = ncontents[:-1]
                    else:
                        break
                    contents = "%s\n%s" % (contents, ncontents)
                else:
                    break
            pkg.append((name, contents))
        if pkg:
            yield pkg

    def raiseSyntaxError(self, msg, lineno=None):
        if lineno is None:
            lineno = self.lineno
        raise ParseError(self.name, lineno, msg)

# End of code from debian_support
#

# General support routines


def safe_open(name, mode="r"):
    try:
        return file(name, mode)
    except IOError, e:
        sys.stdout.write(
            "error: could not open %s: %s\n" % (`name`, e.strerror))
        sys.exit(2)

# Configuration file parser


class ConfigParser:

    def __init__(self, name, file=None):
        self.name = name
        if file is None:
            if os.path.exists(name):
                self.file = safe_open(name)
            else:
                self.file = None
        else:
            self.file = file

    def onComment(self, line, number):
        pass

    def onKey(self, line, number, key, value, trailer):
        pass

    def onError(self, line, number):
        sys.stderr.write("%s:%d: invalid configuration file syntax\n"
                         % (self.name, number))
        sys.exit(2)

    def parse(self, re_comment=re.compile(r'^\s*(?:#.*)?$'),
              re_key=re.compile(r'^\s*([A-Z_]+)=(.*?)\s*$'),
              re_quote=re.compile(r'^"(.*)"\s*$')):
        if self.file is None:
            return
        lineno = 0
        for line in self.file:
            lineno += 1
            match = re_comment.match(line)
            if match is not None:
                self.onComment(line, lineno)
                continue

            match = re_key.match(line)
            if match is not None:
                (k, v) = match.groups()
                match = re_quote.match(v)
                if match is not None:
                    # This is not perfect, but proper parsing is
                    # probably not worth the effort.
                    (v,) = match.groups()
                self.onKey(line, lineno, k, v, '\n')
                continue

            self.onError(line, lineno)


def read_config(name, file=None):
    """Read the configuration file NAME into a dictionary and return it."""
    config = {}

    class Parser(ConfigParser):

        def onKey(self, line, number, key, value, trailer):
            config[key] = value
    Parser(name, file).parse()
    return config


def update_config(name):
    """Update the configuration file NAME with data from standard input."""
    new_config = read_config('<stdin>', sys.stdin)

    new_file = []

    class Parser(ConfigParser):

        def onComment(self, line, lineno):
            new_file.append(line)

        def onKey(self, line, lineno, key, value, trailer):
            if new_config.has_key(key):
                if new_config[key] != value:
                    new_file.append("%s=%s%s"
                                    % (key, new_config[key], trailer))
                else:
                    new_file.append(line)
                del new_config[key]
            else:
                new_file.append(line)
    Parser(name).parse()

    remaining = new_config.keys()
    remaining.sort()
    if remaining:
        if remaining[-1] != "\n":
            new_file.append("\n")
        for k in remaining:
            new_file.append("%s=%s\n" % (k, new_config[k]))

    conf = file(name, "w+")
    try:
        for line in new_file:
            conf.write(line)
    finally:
        conf.close()


def patch_https_implementation():
    "Add certificate and host name checking to the standard library."

    import ssl
    from inspect import getargspec
    from inspect import stack
    from httplib import HTTPConnection

    wrap_socket_orig = ssl.wrap_socket
    set_ciphers = "ciphers" in getargspec(wrap_socket_orig)[0]

    def wrap_socket(sock, *args, **kwargs):
        kwargs["ca_certs"] = "/etc/ssl/certs/ca-certificates.crt"
        kwargs["cert_reqs"] = ssl.CERT_REQUIRED
        if set_ciphers:
            kwargs["ciphers"] = "HIGH:!aNULL:!SRP:!PSK"
        kwargs["do_handshake_on_connect"] = True
        kwargs["suppress_ragged_eofs"] = False
        secsock = wrap_socket_orig(sock, *args, **kwargs)

        # Implement host name check for httplib
        cert = secsock.getpeercert()
        caller = stack()[1]
        caller_locals = caller[0].f_locals
        try:
            caller_self = caller_locals["self"]
        except KeyError:
            caller_self = None
        if caller_self is not None and isinstance(caller_self, HTTPConnection):
            expected_host = caller_self.host
            try:
                subject_dn = cert["subject"]
            except KeyError:
                raise IOError("invalid X.509 certificate for " + expected_host)
            found = False
            expected = (("commonName", expected_host),)
            for entry in subject_dn:
                if entry == expected:
                    found = True
            if not found:
                raise IOError("X.509 certificate does not match host name " +
                              expected_host)
        else:
            raise IOError("ssl.wrap_socket called from unexpected place")

        return secsock
    ssl.wrap_socket = wrap_socket

# Command line parser


def parse_cli():
    """Reads sys.argv and returns an options object."""
    parser = OptionParser(usage="%prog OPTIONS...")
    parser.add_option("--config", metavar="FILE",
                      help="sets the name of the configuration file",
                      default='/etc/default/debsecan')
    parser.add_option("--suite", type="choice",
                      choices=[
                          'woody', 'sarge', 'etch', 'lenny', 'squeeze', 'wheezy',
                               'jessie', 'sid'],
                      help="set the Debian suite of this installation")
    parser.add_option("--source", metavar="URL",
                      help="sets the URL for the vulnerability information")
    parser.add_option("--status", metavar="NAME",
                      default="/var/lib/dpkg/status",
                      help="name of the dpkg status file")
    parser.add_option("--format", type="choice",
                      choices=['bugs', 'packages', 'summary', 'detail',
                               'report', 'simple'],
                      default="summary",
                      help="change output format")
    parser.add_option("--only-fixed", action="store_true", dest="only_fixed",
                      help="list only vulnerabilities for which a fix is available")
    parser.add_option("--no-obsolete", action="store_true", dest="no_obsolete",
                      help="do not list obsolete packages (not recommend)")
    parser.add_option("--history", default="/var/lib/debsecan/history",
                      metavar="NAME",
                      help="sets the file name of debsecan's internal status "
                      + "file")
    parser.add_option("--line-length", default=72, type="int",
                      dest="line_length",
                      help="maximum line length in report mode")
    parser.add_option("--update-history", action="store_true",
                      dest="update_history",
                      help="update the history file after reporting")
    parser.add_option("--mailto", help="send report to an email address")
    parser.add_option("--cron", action="store_true",
                      help="debsecan is invoked from cron")
    parser.add_option("--whitelist", metavar="NAME",
                      default="/var/lib/debsecan/whitelist",
                      help="sets the name of the whitelist file")
    parser.add_option("--add-whitelist", action="store_true",
                      dest="whitelist_add",
                      help="add entries to the whitelist")
    parser.add_option("--remove-whitelist", action="store_true",
                      dest="whitelist_remove",
                      help="remove entries from the whitelist")
    parser.add_option("--show-whitelist", action="store_true",
                      dest="whitelist_show",
                      help="display entries on the whitelist")
    parser.add_option("--disable-https-check", action="store_true",
                      dest="disable_https_check",
                      help="disable certificate checks")
    parser.add_option("--update-config", action="store_true",
                      dest="update_config", help=None)
    (options, args) = parser.parse_args()

    def process_whitelist_options():
        """Check the whitelist options.  They conflict with everything
        else."""
        count = 0
        for x in (options.whitelist_add, options.whitelist_remove,
                  options.whitelist_show):
            if x:
                count += 1
        if count == 0:
            return
        if count > 1:
            sys.stderr.write(
                "error: at most one whitelist option may be specified\n")
            sys.exit(1)

        for (k, v) in options.__dict__.items():
            if type(v) == types.MethodType or v is None:
                continue
            if k not in ("whitelist", "whitelist_add", "whitelist_remove",
                         # The following options have defaults and are
                         # always present.
                         "history", "status", "format", "line_length"):
                sys.stderr.write(
                    "error: when editing the whitelist, no other options are allowed\n")
                sys.exit(1)

    if options.whitelist_add:
        whitelist_add(options, args)
        sys.exit(0)
    if options.whitelist_remove:
        whitelist_remove(options, args)
        sys.exit(0)
    if options.whitelist_show:
        whitelist_show(options, args)
        sys.exit(0)

    process_whitelist_options()

    if options.cron:
        options.format = 'report'
        options.update_history = True
    if options.only_fixed and not options.suite:
        sys.stderr.write("error: --only-fixed requires --suite\n")
        sys.exit(1)
    if options.no_obsolete and not options.suite:
        sys.stderr.write("error: --no-obsolete requires --suite\n")
        sys.exit(1)
    if options.update_history and options.format != 'report':
        sys.stderr.write("error: --update-history requires report format\n")
        sys.exit(1)
    if options.cron and options.format != 'report':
        sys.stderr.write("error: --cron requires report format\n")
        sys.exit(1)
    if options.mailto and options.format != 'report':
        sys.stderr.write("error: --mailto requires report format\n")
        sys.exit(1)
    options.need_history = options.format == 'report'

    config = read_config(options.config)
    if options.cron and not options.mailto:
        options.mailto = config.get('MAILTO', '')
        if options.mailto == '':
            options.mailto = 'root'
    options.disable_https_check = options.disable_https_check or \
        (config.get("DISABLE_HTTPS_CHECK", False) in
         ['yes', 'true', 'True', '1', 'on'])
    options.suite = options.suite or config.get('SUITE', None)
    if options.suite == 'GENERIC':
        options.suite = None
    options.subject = config.get(
        'SUBJECT', 'Debian security status of %(hostname)s')

    if not options.disable_https_check:
        patch_https_implementation()

    return (options, config, args)

# Vulnerabilities


class Vulnerability:

    """Stores a vulnerability name/package name combination."""

    urgency_conversion = {' ': '',
                          'L': 'low',
                          'M': 'medium',
                          'H': 'high'}

    def __init__(self, vuln_names, str):
        """Creates a new vulnerability object from a string."""
        (package, vnum, flags, unstable_version, other_versions) \
            = str.split(',', 4)
        vnum = int(vnum)
        self.bug = vuln_names[vnum][0]
        self.package = package
        self.binary_packages = None
        self.unstable_version = unstable_version
        self.other_versions = other_versions.split(' ')
        if self.other_versions == ['']:
            self.other_versions = []
        self.description = vuln_names[vnum][1]
        self.binary_package = flags[0] == 'B'
        self.urgency = self.urgency_conversion[flags[1]]
        self.remote = {'?': None,
                       'R': True,
                       ' ': False}[flags[2]]
        self.fix_available = flags[3] == 'F'

    def is_vulnerable(self, (bin_pkg, bin_ver), (src_pkg, src_ver)):
        """Returns true if the specified binary package is subject to
        this vulnerability."""
        self._parse()
        if self.binary_package and bin_pkg == self.package:
            if self.unstable_version:
                return bin_ver < self.unstable_version
            else:
                return True
        elif src_pkg == self.package:
            if self.unstable_version:
                return src_ver < self.unstable_version \
                    and src_ver not in self.other_versions
            else:
                return src_ver not in self.other_versions
        else:
            return False

    def obsolete(self, bin_name=None):
        if self.binary_packages is None:
            return
        if bin_name is None:
            bin_name = self.installed_package
        return bin_name not in self.binary_packages

    def installed(self, src_name, bin_name):
        """Returns a new vulnerability object for the installed package."""
        v = copy.copy(self)
        v.installed_package = bin_name
        return v

    def _parse(self):
        """Further parses the object."""
        if type(self.unstable_version) == types.StringType:
            if self.unstable_version:
                self.unstable_version = Version(self.unstable_version)
            else:
                self.unstable_version = None
            self.other_versions = map(Version, self.other_versions)


def fetch_data(options, config):
    """Returns a dictionary PACKAGE -> LIST-OF-VULNERABILITIES."""
    url = options.source or config.get("SOURCE", None) \
        or "https://security-tracker.debian.org/tracker/" \
           "debsecan/release/1/"
    if url[-1] != "/":
        url += "/"
    if options.suite:
        url += options.suite
    else:
        url += 'GENERIC'
    r = urllib2.Request(url)
    r.add_header('User-Agent', 'debsecan/' + __version__)
    try:
        u = urllib2.urlopen(r)
        # In cron mode, we suppress almost all errors because we
        # assume that they are due to lack of Internet connectivity.
    except urllib2.HTTPError, e:
        if (not options.cron) or e.code == 404:
            sys.stderr.write("error: while downloading %s:\n%s\n" % (url, e))
            sys.exit(1)
        else:
            sys.exit(0)
    except urllib2.URLError, e:
        if not options.cron:            # no e.code check here
            # Be conservative about the attributes offered by
            # URLError.  They are undocumented, and strerror is not
            # available even though it is documented for
            # EnvironmentError.
            msg = e.__dict__.get('reason', '')
            if msg:
                msg = "error: while downloading %s:\nerror: %s\n" % (url, msg)
            else:
                msg = "error: while downloading %s:\n" % url
            sys.stderr.write(msg)
            sys.exit(1)
        else:
            sys.exit(0)

    data = []
    while 1:
        d = u.read(4096)
        if d:
            data.append(d)
        else:
            break
    data = StringIO(zlib.decompress(''.join(data)))
    if data.readline() != "VERSION 1\n":
        sys.stderr.write("error: server sends data in unknown format\n")
        sys.exit(1)

    vuln_names = []
    for line in data:
        if line[-1:] == '\n':
            line = line[:-1]
        if line == '':
            break
        (name, flags, desc) = line.split(',', 2)
        vuln_names.append((name, desc))

    packages = {}
    for line in data:
        if line[-1:] == '\n':
            line = line[:-1]
        if line == '':
            break
        v = Vulnerability(vuln_names, line)
        try:
            packages[v.package].append(v)
        except KeyError:
            packages[v.package] = [v]

    source_to_binary = {}
    for line in data:
        if line[-1:] == '\n':
            line = line[:-1]
        if line == '':
            break
        (sp, bps) = line.split(',')
        if bps:
            source_to_binary[sp] = bps.split(' ')
        else:
            source_to_binary[sp] = []

    for vs in packages.values():
        for v in vs:
            if not v.binary_package:
                v.binary_packages = source_to_binary.get(v.package, None)

    return packages

# Previous state (for incremental reporting)


class History:

    def __init__(self, options):
        self.options = options
        self.last_updated = 86400
        self._read_history(self.options.history)

    def data(self):
        """Returns a dictionary (BUG, PACKAGE) -> UPDATE-AVAILABLE.
        The result is not shared with the internal dictionary."""
        return self.history.copy()

    def expired(self):
        """Returns true if the stored history file is out of date."""
        if self.options.cron:
            old = time.localtime(self.last_updated)
            now = time.localtime()

            def ymd(t):
                return (t.tm_year, t.tm_mon, t.tm_mday)
            if ymd(old) == ymd(now):
                return False
            return now.tm_hour >= 2
        else:
            # If we aren't run from cron, we always download new data.
            return True

    def known(self, v):
        """Returns true if the vulnerability is known."""
        return self.history.has_key(v)

    def fixed(self, v):
        """Returns true if the vulnerability is known and has been
        fixed."""
        return self.history.get(v, False)

    def _read_history(self, name):
        """Reads the named history file.  Returns a dictionary
        (BUG, PACKAGE) -> UPDATE-AVAILABLE."""

        self.history = {}

        try:
            f = file(name)
        except IOError:
            return

        line = f.readline()
        if line == 'VERSION 0\n':
            pass
        elif line == 'VERSION 1\n':
            line = f.readline()
            self.last_updated = int(line)
        else:
            return

        for line in f:
            if line[-1:] == '\n':
                line = line[:-1]
            (bug, package, fixed) = line.split(',')
            self.history[(bug, package)] = fixed == 'F'
        f.close()

# Whitelisting vulnerabilities


class Whitelist:

    def __init__(self, name):
        """Read a whitelist from disk.

        name - file name of the white list.  If None, no file is read.
        """
        self.name = name
        self.bug_dict = {}
        self.bug_package_dict = {}
        if name and os.path.exists(name):
            src = safe_open(name)
            line = src.readline()
            if line != 'VERSION 0\n':
                raise SyntaxError, "invalid whitelist file, got: " + `line`
            for line in src:
                if line[-1] == '\n':
                    line = line[:-1]
                (bug, pkg) = line.split(',')
                self.add(bug, pkg)
        self._dirty = False

    def add(self, bug, pkg=None):
        """Adds a bug/package pair to the whitelist.
        If the package is not specified (or empty), the bug is whitelisted
        completely."""
        if pkg:
            self.bug_package_dict[(bug, pkg)] = True
        else:
            self.bug_dict[bug] = True
        self._dirty = True

    def remove(self, bug, pkg=None):
        """Removes a bug/package pair from the whitelist.
        If the package is not specified, *all* whitelisted packages for
        that bug are removed."""
        removed = False
        if pkg:
            try:
                del self.bug_package_dict[(bug, pkg)]
                removed = True
            except KeyError:
                pass
        else:
            try:
                del self.bug_dict[bug]
                removed = True
            except KeyError:
                pass
            for bug_pkg in self.bug_package_dict.keys():
                if bug_pkg[0] == bug:
                    del self.bug_package_dict[bug_pkg]
                    removed = True

        if removed:
            self._dirty = True
        else:
            if pkg:
                sys.stderr.write(
                    "error: no matching whitelist entry for %s %s\n"
                    % (bug, pkg))
            else:
                sys.stderr.write("error: no matching whitelist entry for %s\n"
                                 % bug)
            sys.exit(1)

    def check(self, bug, package):
        """Returns true if the bug/package pair is whitelisted."""
        return self.bug_dict.has_key(bug) \
            or self.bug_package_dict.has_key((bug, package))

    def update(self):
        """Write the whitelist file back to disk, if the data has changed."""
        if not (self._dirty and self.name):
            return
        new_name = self.name + '.new'
        f = safe_open(new_name, "w+")
        f.write("VERSION 0\n")
        l = self.bug_dict.keys()
        l.sort()
        for bug in l:
            f.write(bug + ",\n")
        l = self.bug_package_dict.keys()
        l.sort()
        for bug_pkg in l:
            f.write("%s,%s\n" % bug_pkg)
        f.close()
        os.rename(new_name, self.name)

    def show(self, file):
        l = []
        for bug in self.bug_dict.keys():
            file.write("%s (all packages)\n" % bug)
        for (bug, pkg) in self.bug_package_dict.keys():
            l.append("%s %s\n" % (bug, pkg))
        l.sort()
        for line in l:
            file.write(line)


def __whitelist_edit(options, args, method):
    w = Whitelist(options.whitelist)
    while args:
        bug = args[0]
        if bug == '' or (not ('A' <= bug[0] <= 'Z')) or ',' in bug:
            sys.stderr.write("error: %s is not a bug name\n" % `bug`)
            sys.exit(1)
        del args[0]
        pkg_found = False
        while args:
            pkg = args[0]
            if (not pkg) or ',' in pkg:
                sys.stderr.write("error: %s is not a package name\n" % `bug`)
                sys.exit(1)
            if 'A' <= pkg[0] <= 'Z':
                break
            method(w, bug, pkg)
            del args[0]
            pkg_found = True
        if not pkg_found:
            method(w, bug, None)
    w.update()


def whitelist_add(options, args):
    __whitelist_edit(options, args, lambda w, bug, pkg: w.add(bug, pkg))


def whitelist_remove(options, args):
    __whitelist_edit(options, args, lambda w, bug, pkg: w.remove(bug, pkg))


def whitelist_show(options, args):
    Whitelist(options.whitelist).show(sys.stdout)

# Classes for output formatting


class Formatter:

    def __init__(self, target, options, history):
        self.target = target
        self.options = options
        self.history = history
        self.whitelist = Whitelist(self.options.whitelist)
        self._invalid_versions = False

    def invalid_version(self, package, version):
        sys.stdout.flush()
        sys.stderr.write("error: invalid version %s of package %s\n"
                         % (version, package))
        if not self._invalid_versions:
            sys.stderr.write(
                "error: install the python-apt package for invalid versions support\n")
            self._invalid_versions = True
        sys.stderr.flush()

    def invalid_source_version(self, package, version):
        sys.stdout.flush()
        sys.stderr.write("error: invalid source version %s of package %s\n"
                         % (version, package))
        if not self._invalid_versions:
            sys.stderr.write(
                "error: install the python-apt package for invalid versions support\n")
            self._invalid_versions = True
        sys.stderr.flush()

    def maybe_record(self, v, bp, sp):
        """Invoke self.record, honouring --only-fixed.  Can be
        overridden to implement a different form of --only-fixed
        processing."""
        if self.whitelist.check(v.bug, bp[0]):
            return
        if not (self.options.only_fixed and not v.fix_available):
            if self.options.no_obsolete and v.obsolete(bp[0]):
                return
            self.record(v, bp, sp)

    def finish(self):
        pass


class BugFormatter(Formatter):

    def __init__(self, target, options, history):
        Formatter.__init__(self, target, options, history)
        self.bugs = {}

    def record(self, v, bp, sp):
        self.bugs[v.bug] = 1

    def finish(self):
        bugs = self.bugs.keys()
        bugs.sort()
        for b in bugs:
            self.target.write(b)


class PackageFormatter(Formatter):

    def __init__(self, target, options, history):
        Formatter.__init__(self, target, options, history)
        self.packages = {}

    def record(self, v, (bin_name, bin_version), sp):
        self.packages[bin_name] = 1

    def finish(self):
        packages = self.packages.keys()
        packages.sort()
        for p in packages:
            self.target.write(p)


class SummaryFormatter(Formatter):

    def record(self, v,
               (bin_name, bin_version), (src_name, src_version)):
        notes = []
        if v.fix_available:
            notes.append("fixed")
        if v.remote:
            notes.append("remotely exploitable")
        if v.urgency:
            notes.append(v.urgency + " urgency")
        if v.obsolete(bin_name):
            notes.append('obsolete')
        notes = ', '.join(notes)
        if notes:
            self.target.write("%s %s (%s)" % (v.bug, bin_name, notes))
        else:
            self.target.write("%s %s" % (v.bug, bin_name))


class SimpleFormatter(Formatter):

    def record(self, v,
               (bin_name, bin_version), (src_name, src_version)):
        self.target.write("%s %s" % (v.bug, bin_name))


class DetailFormatter(Formatter):

    def record(self, v,
               (bin_name, bin_version), (src_name, src_version)):
        notes = []
        if v.fix_available:
            notes.append("fixed")
        if v.remote:
            notes.append("remotely exploitable")
        if v.urgency:
            notes.append(v.urgency + " urgency")
        notes = ', '.join(notes)
        if notes:
            self.target.write("%s (%s)" % (v.bug, notes))
        else:
            self.target.write(v.bug)
        self.target.write("  " + v.description)
        self.target.write("  installed: %s %s"
                          % (bin_name, bin_version))
        self.target.write("             (built from %s %s)"
                          % (src_name, src_version))
        if v.obsolete(bin_name):
            self.target.write("             package is obsolete")

        if v.binary_package:
            k = 'binary'
        else:
            k = 'source'
        if v.unstable_version:
            self.target.write("  fixed in unstable: %s %s (%s package)"
                              % (v.package, v.unstable_version, k))
        for vb in v.other_versions:
            self.target.write("  fixed on branch:   %s %s (%s package)"
                              % (v.package, vb, k))
        if v.fix_available:
            self.target.write("  fix is available for the selected suite (%s)"
                              % self.options.suite)
        self.target.write("")


class ReportFormatter(Formatter):

    def __init__(self, target, options, history):
        Formatter.__init__(self, target, options, history)
        self.bugs = {}
        self.invalid = []

        # self.record will put new package status information here.
        self.new_history = {}

        # Fixed bugs are deleted from self.fixed_bugs by self.record.
        self.fixed_bugs = self.history.data()

        # True if some bugs have been whitelisted.
        self._whitelisted = False

    def _write_history(self, name):
        """Writes self.new_history to the named history file.
        The file is replaced atomically."""
        new_name = name + '.new'
        f = safe_open(new_name, "w+")
        f.write("VERSION 1\n%d\n" % int(time.time()))
        for ((bug, package), fixed) in self.new_history.items():
            if fixed:
                fixed = 'F'
            else:
                fixed = ' '
            f.write("%s,%s,%s\n" % (bug, package, fixed))
        f.close()
        os.rename(new_name, name)

    def maybe_record(self, v, bp, sp):
        # --only-fixed processing happens in self.finish, and we need
        # all records to detect changes properly.  Whitelisted bugs
        # need special treatment, too.
        self.record(v, bp, sp)

    def record(self, v,
               (bin_name, bin_version), (src_name, src_version)):

        v = v.installed(src_name, bin_name)
        bn = (v.bug, bin_name)
        if not self.whitelist.check(v.bug, bin_name):
            if self.bugs.has_key(v.bug):
                self.bugs[v.bug].append(v)
            else:
                self.bugs[v.bug] = [v]
            self.new_history[bn] = v.fix_available
        else:
            self._whitelisted = True
        # If we whitelist a bug, do not list it as fixed, so we always
        # remove it from the fixed_bugs dict.
        try:
            del self.fixed_bugs[bn]
        except KeyError:
            pass

    def invalid_version(self, package, version):
        self.invalid.append(package)

    def invalid_source_version(self, package, version):
        self.invalid.append(package)

    def _status_changed(self):
        """Returns true if the system's vulnerability status changed
        since the last run."""

        for (k, v) in self.new_history.items():
            if (not self.history.known(k)) or self.history.fixed(k) != v:
                return True
        return len(self.fixed_bugs.keys()) > 0

    def finish(self):
        if self.options.mailto and not self._status_changed():
            if options.update_history:
                self._write_history(self.options.history)
            return

        w = self.target.write
        if self.options.suite:
            w("Security report based on the %s release" % self.options.suite)
        else:
            w("Security report based on general data")
            w("")
            w(
                """If you specify a proper suite, this report will include information
regarding available security updates and obsolete packages.  To set
the correct suite, run "dpkg-reconfigure debsecan" as root.""")
        w("")

        for vlist in self.bugs.values():
            vlist.sort(lambda a, b: cmp(a.package, b.package))

        blist = self.bugs.items()
        blist.sort()

        self._bug_found = False

        def print_headline(fix_status, new_status):
            if fix_status:
                if new_status:
                    w("*** New security updates")
                else:
                    w("*** Available security updates")
            else:
                if new_status:
                    w("*** New vulnerabilities")
                else:
                    if self.options.suite:
                        w("*** Vulnerabilities without updates")
                    else:
                        # If no suite has been specified, all
                        # vulnerabilities lack updates, technically
                        # speaking.
                        w("*** Vulnerabilities")
            w("")

        def score_urgency(urgency):
            return {'high': 100,
                    'medium': 50,
                    }.get(urgency, 0)

        def vuln_to_notes(v):
            notes = []
            notes_score = 0
            if v.remote:
                notes.append("remotely exploitable")
                notes_score += 25
            if v.urgency:
                notes.append(v.urgency + " urgency")
                notes_score += score_urgency(v.urgency)
            if v.obsolete():
                notes.append('obsolete')
            return (-notes_score, ', '.join(notes))

        def truncate(line):
            if len(line) <= self.options.line_length:
                return line
            result = []
            length = 0
            max_length = self.options.line_length - 3
            for c in line.split(' '):
                l = len(c)
                new_length = length + l + 1
                if new_length < max_length:
                    result.append(c)
                    length = new_length
                else:
                    return ' '.join(result) + '...'
            return ' '.join(result)     # should not be reachedg

        def write_url(bug):
            w("  <https://security-tracker.debian.org/tracker/%s>" % bug)

        def scan(fix_status, new_status):
            have_obsolete = False
            first_bug = True
            for (bug, vlist) in blist:
                pkg_vulns = {}
                for v in vlist:
                    bug_package = (v.bug, v.installed_package)
                    if v.fix_available:
                        is_new = not self.history.fixed(bug_package)
                    else:
                        is_new = (not self.history.known(bug_package)) \
                            or self.history.fixed(bug_package)
                    if v.fix_available != fix_status or is_new != new_status:
                        continue

                    if first_bug:
                        print_headline(fix_status, new_status)
                        first_bug = False

                    if v.obsolete():
                        if self.options.no_obsolete:
                            continue
                        have_obsolete = True

                    notes = vuln_to_notes(v)
                    if pkg_vulns.has_key(notes):
                        pkg_vulns[notes].append(v)
                    else:
                        pkg_vulns[notes] = [v]

                indent = "    "
                if len(pkg_vulns) > 0:
                    self._bug_found = True
                    notes = pkg_vulns.keys()
                    notes.sort()
                    # any v will do, because we've aggregated by v.bug
                    v = pkg_vulns[notes[0]][0]
                    w(truncate("%s %s" % (v.bug, v.description)))
                    write_url(v.bug)

                    for note in notes:
                        note_text = note[1]
                        line = "  - "
                        comma_needed = False
                        for v in pkg_vulns[note]:
                            pkg = v.installed_package
                            # Wrap the package list if the line length
                            # is exceeded.
                            if len(line) + len(pkg) + 3 \
                                    > self.options.line_length:
                                w(line + ',')
                                line = indent + pkg
                                comma_needed = True
                            else:
                                if comma_needed:
                                    line += ", "
                                else:
                                    comma_needed = True
                                line += pkg
                        if note_text:
                            if len(line) + len(note_text) + 3 \
                                    > self.options.line_length:
                                w(line)
                                w("%s(%s)" % (indent, note_text))
                            else:
                                w("%s (%s)" % (line, note_text))
                        else:
                            w(line)
                    w("")

            if have_obsolete:
                w(
                    """Note that some packages were marked as obsolete.  To deal with the
vulnerabilities in them, you need to remove them.  Before you can do
this, you may have to upgrade other packages depending on them.
""")

        def scan_fixed():
            bugs = {}
            for (bug, package) in self.fixed_bugs.keys():
                if bugs.has_key(bug):
                    bugs[bug].append(package)
                else:
                    bugs[bug] = [package]
            bug_names = bugs.keys()
            bug_names.sort()

            first_bug = True
            for bug in bug_names:
                if first_bug:
                    w("*** Fixed vulnerabilities")
                    w("")
                    first_bug = False
                    self._bug_found = True
                w(bug)
                write_url(bug)
                bugs[bug].sort()
                for p in bugs[bug]:
                    w("  - %s" % p)
                w("")

        def scan_invalid():
            if self.invalid:
                self._bug_found = True
                self.invalid.sort()
                w("*** Packages with invalid versions")
                w("")
                w("The following non-official packages have invalid versions and cannot")
                w("be classified correctly:")
                w("")
                for p in self.invalid:
                    w("  - " + p)

        scan(fix_status=True, new_status=True)
        scan_fixed()
        scan(fix_status=True, new_status=False)
        if not self.options.only_fixed:
            scan(fix_status=False, new_status=True)
            scan(fix_status=False, new_status=False)
        scan_invalid()

        if not self._bug_found:
            if self.options.only_fixed:
                w(
                    """No known vulnerabilities for which updates are available were found
on the system.""")
            else:
                w("No known vulnerabilities were found on the system.")
            if self._whitelisted:
                w("")
                w("However, some bugs have been whitelisted.")
        else:
            if self._whitelisted:
                w(
                    """Note that some vulnerablities have been whitelisted and are not included
in this report.""")

        if options.update_history:
            self._write_history(self.options.history)

formatters = {'bugs': BugFormatter,
              'packages': PackageFormatter,
              'summary': SummaryFormatter,
              'simple': SimpleFormatter,
              'detail': DetailFormatter,
              'report': ReportFormatter}

# Mini-template processing

format_values = {
    'hostname': socket.gethostname(),
    'fqdn': socket.getfqdn()
}
try:
    format_values['ip'] = socket.gethostbyname(format_values['hostname'])
except socket.gaierror:
    format_values['ip'] = "unknown"


def format_string(msg):
    try:
        return msg % format_values
    except ValueError:
        sys.stderr.write("error: invalid format string: %s\n" % `msg`)
        sys.exit(2)
    except KeyError, e:
        sys.stderr.write("error: invalid key %s in format string %s\n"
                         % (`e.args[0]`, `msg`))
        sys.exit(2)

# Targets


class Target:

    def __init__(self, options):
        pass

    def finish(self):
        pass


class TargetMail(Target):

    def __init__(self, options):
        assert options.mailto
        self.options = options
        self.sendmail = None
        self.opt_subject = format_string(self.options.subject)

        # Legacy addresses may contain "%" characters, without
        # proper template syntax.
        self.opt_mailto = format_string(
            re.sub(r'%([a-z0-9])', r'%%\1', self.options.mailto))

    def _open(self):
        self.sendmail = os.popen("/usr/sbin/sendmail -t", "w")
        self.sendmail.write("""Subject: %s
To: %s

""" % (self.opt_subject, self.opt_mailto))

    def write(self, line):
        if self.sendmail is None:
            self._open()
        self.sendmail.write(line + '\n')

    def finish(self):
        if self.sendmail is not None:
            self.sendmail.close()


class TargetPrint(Target):

    def write(self, line):
        print line


def rate_system(target, options, vulns, history):
    """Read /var/lib/dpkg/status and discover vulnerable packages.
    The results are printed using one of the formatter classes.

    options: command line options
    vulns: list of vulnerabiltiies"""
    packages = PackageFile(options.status)
    re_source = re.compile\
        (r'^([a-zA-Z0-9.+-]+)(?:\s+\((\S+)\))?$')
    formatter = formatters[options.format](target, options, history)
    for pkg in packages:
        pkg_name = None
        pkg_status = None
        pkg_version = None
        pkg_arch = None
        pkg_source = None
        pkg_source_version = None

        for (name, contents) in pkg:
            if name == "Package":
                pkg_name = contents
            if name == "Status":
                pkg_status = contents
            elif name == "Version":
                pkg_version = contents
            elif name == "Source":
                match = re_source.match(contents)
                if match is None:
                    raise SyntaxError(('package %s references '
                                       + 'invalid source package %s') %
                                      (pkg_name, `contents`))
                (pkg_source, pkg_source_version) = match.groups()
        if pkg_name is None:
            raise SyntaxError\
                ("package record does not contain package name")
        if pkg_status is None:
            raise SyntaxError\
                ("package record does not contain status")
        if 'installed' not in pkg_status.split(' '):
            # Package is not installed.
            continue
        if pkg_version is None:
            raise SyntaxError\
                ("package record does not contain version information")
        if pkg_source_version is None:
            pkg_source_version = pkg_version
        if not pkg_source:
            pkg_source = pkg_name

        try:
            pkg_version = Version(pkg_version)
        except ValueError:
            formatter.invalid_version(pkg_name, pkg_version)
            continue
        try:
            pkg_source_version = Version(pkg_source_version)
        except ValueError:
            formatter.invalid_source_version(pkg_name, pkg_source_version)
            continue

        try:
            vlist = vulns[pkg_source]
        except KeyError:
            try:
                vlist = vulns[pkg_name]
            except:
                continue
        for v in vlist:
            bp = (pkg_name, pkg_version)
            sp = (pkg_source, pkg_source_version)
            if v.is_vulnerable(bp, sp):
                formatter.maybe_record(v, bp, sp)
    formatter.finish()
    target.finish()


def main():
    (options, config, args) = parse_cli()
    if (options.update_config):
        update_config(options.config)
        sys.exit(0)
    if options.cron and config.get("REPORT", "true") != "true":
        # Do nothing in cron mode if reporting is disabled.
        sys.exit(0)
    if options.need_history:
        history = History(options)
        if not history.expired():
            sys.exit(0)
    else:
        history = None
    if options.mailto:
        target = TargetMail(options)
    else:
        target = TargetPrint(options)
    rate_system(target, options, fetch_data(options, config), history)


if __name__ == "__main__":
    main()
