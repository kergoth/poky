# Recipe creation tool - create build system handler for python
#
# Copyright (C) 2015 Mentor Graphics Corporation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import ast
import codecs
import collections
import distutils.command.build_py
import email
import imp
import glob
import itertools
import logging
import os
import re
import sys
import subprocess
from recipetool.create import RecipeHandler

logger = logging.getLogger('recipetool')

tinfoil = None


def tinfoil_init(instance):
    global tinfoil
    tinfoil = instance


class PythonRecipeHandler(RecipeHandler):
    base_pkgdeps = ['python-core']
    excluded_pkgdeps = ['python-dbg']
    # os.path is provided by python-core
    assume_provided = ['builtins', 'os.path']
    # Assumes that the host python builtin_module_names is sane for target too
    assume_provided = assume_provided + list(sys.builtin_module_names)

    bbvar_map = {
        'Name': 'PN',
        'Version': 'PV',
        'Home-page': 'HOMEPAGE',
        'Summary': 'SUMMARY',
        'Description': 'DESCRIPTION',
        'License': 'LICENSE',
        'Requires': 'RDEPENDS_${PN}',
        'Provides': 'RPROVIDES_${PN}',
        'Obsoletes': 'RREPLACES_${PN}',
    }
    # PN/PV are already set by recipetool core & desc can be extremely long
    excluded_fields = [
        'Name',
        'Version',
        'Description',
    ]
    setup_parse_map = {
        'Url': 'Home-page',
        'Classifiers': 'Classifier',
        'Description': 'Summary',
    }
    setuparg_map = {
        'Home-page': 'url',
        'Classifier': 'classifiers',
        'Summary': 'description',
        'Description': 'long-description',
    }
    # Values which are lists, used by the setup.py argument based metadata
    # extraction method, to determine how to process the setup.py output.
    setuparg_list_fields = [
        'Classifier',
        'Requires',
        'Provides',
        'Obsoletes',
        'Platform',
        'Supported-Platform',
    ]
    setuparg_multi_line_values = ['Description']
    replacements = [
        ('License', r' ', '-'),
        ('License', r'-License$', ''),
        ('License', r'^UNKNOWN$', ''),

        # Remove currently unhandled version numbers from these variables
        ('Requires', r' *\([^)]*\)', ''),
        ('Provides', r' *\([^)]*\)', ''),
        ('Obsoletes', r' *\([^)]*\)', ''),
        ('Install-requires', r'^([^><= ]+).*', r'\1'),
        ('Extras-require', r'^([^><= ]+).*', r'\1'),
        ('Tests-require', r'^([^><= ]+).*', r'\1'),
    ]

    # Operations to adjust non-list variable values based on the list
    # contents, e.g. set License based on the license classifiers
    list_entry_ops = [
        # Field to search, value to search for, bb var to set, bb value to set
        ('Classifier', 'License :: OSI Approved :: Academic Free License (AFL)', 'License', 'AFL'),
        ('Classifier', 'License :: OSI Approved :: Apache Software License', 'License', 'Apache'),
        ('Classifier', 'License :: OSI Approved :: Apple Public Source License', 'License', 'APSL'),
        ('Classifier', 'License :: OSI Approved :: Artistic License', 'License', 'Artistic'),
        ('Classifier', 'License :: OSI Approved :: Attribution Assurance License', 'License', 'AAL'),
        ('Classifier', 'License :: OSI Approved :: BSD License', 'License', 'BSD'),
        ('Classifier', 'License :: OSI Approved :: Common Public License', 'License', 'CPL'),
        ('Classifier', 'License :: OSI Approved :: Eiffel Forum License', 'License', 'EFL'),
        ('Classifier', 'License :: OSI Approved :: European Union Public Licence 1.0 (EUPL 1.0)', 'License', 'EUPL-1.0'),
        ('Classifier', 'License :: OSI Approved :: European Union Public Licence 1.1 (EUPL 1.1)', 'License', 'EUPL-1.1'),
        ('Classifier', 'License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)', 'License', 'AGPL-3.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU Affero General Public License v3', 'License', 'AGPL-3.0'),
        ('Classifier', 'License :: OSI Approved :: GNU Free Documentation License (FDL)', 'License', 'GFDL'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License (GPL)', 'License', 'GPL'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License v2 (GPLv2)', 'License', 'GPL-2.0'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)', 'License', 'GPL-2.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License v3 (GPLv3)', 'License', 'GPL-3.0'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)', 'License', 'GPL-3.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU Lesser General Public License v2 (LGPLv2)', 'License', 'LGPL-2.0'),
        ('Classifier', 'License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)', 'License', 'LGPL-2.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)', 'License', 'LGPL-3.0'),
        ('Classifier', 'License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)', 'License', 'LGPL-3.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)', 'License', 'LGPL'),
        ('Classifier', 'License :: OSI Approved :: IBM Public License', 'License', 'IPL'),
        ('Classifier', 'License :: OSI Approved :: ISC License (ISCL)', 'License', 'ISC'),
        ('Classifier', 'License :: OSI Approved :: Intel Open Source License', 'License', 'Intel'),
        ('Classifier', 'License :: OSI Approved :: Jabber Open Source License', 'License', 'Jabber'),
        ('Classifier', 'License :: OSI Approved :: MIT License', 'License', 'MIT'),
        ('Classifier', 'License :: OSI Approved :: MITRE Collaborative Virtual Workspace License (CVW)', 'License', 'CVWL'),
        ('Classifier', 'License :: OSI Approved :: Motosoto License', 'License', 'Motosoto'),
        ('Classifier', 'License :: OSI Approved :: Mozilla Public License 1.0 (MPL)', 'License', 'MPL-1.0'),
        ('Classifier', 'License :: OSI Approved :: Mozilla Public License 1.1 (MPL 1.1)', 'License', 'MPL-1.1'),
        ('Classifier', 'License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)', 'License', 'MPL-2.0'),
        ('Classifier', 'License :: OSI Approved :: Nethack General Public License', 'License', 'NGPL'),
        ('Classifier', 'License :: OSI Approved :: Nokia Open Source License', 'License', 'Nokia'),
        ('Classifier', 'License :: OSI Approved :: Open Group Test Suite License', 'License', 'OGTSL'),
        ('Classifier', 'License :: OSI Approved :: Python License (CNRI Python License)', 'License', 'CNRI-Python'),
        ('Classifier', 'License :: OSI Approved :: Python Software Foundation License', 'License', 'PSF'),
        ('Classifier', 'License :: OSI Approved :: Qt Public License (QPL)', 'License', 'QPL'),
        ('Classifier', 'License :: OSI Approved :: Ricoh Source Code Public License', 'License', 'RSCPL'),
        ('Classifier', 'License :: OSI Approved :: Sleepycat License', 'License', 'Sleepycat'),
        ('Classifier', 'License :: OSI Approved :: Sun Industry Standards Source License (SISSL)', 'License', '--  Sun Industry Standards Source License (SISSL)'),
        ('Classifier', 'License :: OSI Approved :: Sun Public License', 'License', 'SPL'),
        ('Classifier', 'License :: OSI Approved :: University of Illinois/NCSA Open Source License', 'License', 'NCSA'),
        ('Classifier', 'License :: OSI Approved :: Vovida Software License 1.0', 'License', 'VSL-1.0'),
        ('Classifier', 'License :: OSI Approved :: W3C License', 'License', 'W3C'),
        ('Classifier', 'License :: OSI Approved :: X.Net License', 'License', 'Xnet'),
        ('Classifier', 'License :: OSI Approved :: Zope Public License', 'License', 'ZPL'),
        ('Classifier', 'License :: OSI Approved :: zlib/libpng License', 'License', 'Zlib'),
    ]

    def __init__(self):
        pass

    def process(self, srctree, classes, lines_before, lines_after, handled):
        if 'buildsystem' in handled:
            return False

        if not RecipeHandler.checkfiles(srctree, ['setup.py']):
            return

        # setup.py is always parsed, at a minimum to get the paths to the
        # python packages/modules/scripts to avoid dep scanning the entire
        # tree.
        #
        # If egg info is available, we use it for both its PKG-INFO metadata
        # and for its requires.txt for install_requires.
        # If PKG-INFO is available but no egg info is, we use that for metadata in preference to
        # the parsed setup.py, but use the install_requires info from the
        # parsed setup.py.

        setupscript = os.path.join(srctree, 'setup.py')
        try:
            setup_info, uses_setuptools, setup_non_literals = self.parse_setup_py(setupscript)
        except Exception:
            logger.exception("Failed to parse setup.py")
            setup_info, uses_setuptools, setup_non_literals = {}, True, []

        egginfo = glob.glob(os.path.join(srctree, '*.egg-info'))
        if egginfo:
            info = self.get_pkginfo(os.path.join(egginfo[0], 'PKG-INFO'))
            requires_txt = os.path.join(egginfo[0], 'requires.txt')
            if os.path.exists(requires_txt):
                with codecs.open(requires_txt) as f:
                    inst_req = []
                    extras_req = collections.defaultdict(list)
                    current_feature = None
                    for line in f.readlines():
                        line = line.rstrip()
                        if not line:
                            continue

                        # We don't currently support sections for optional deps
                        if line.startswith('['):
                            current_feature = line[1:-1]
                        elif current_feature:
                            extras_req[current_feature].append(line)
                        else:
                            inst_req.append(line)
                    info['Install-requires'] = inst_req
                    info['Extras-require'] = extras_req
        elif RecipeHandler.checkfiles(srctree, ['PKG-INFO']):
            info = self.get_pkginfo(os.path.join(srctree, 'PKG-INFO'))

            if setup_info and 'Install-requires' in setup_info:
                    info['Install-requires'] = setup_info['Install-requires']
        else:
            if setup_info:
                info = setup_info
            else:
                info = self.get_setup_args_info(setupscript)

        self.apply_info_replacements(info)

        if uses_setuptools:
            classes.append('setuptools')
        else:
            classes.append('distutils')

        # Map PKG-INFO & setup.py fields to bitbake variables
        bbinfo = {}
        for field, values in info.iteritems():
            if field in self.excluded_fields:
                continue

            if isinstance(values, basestring):
                value = values
            elif field not in self.bbvar_map:
                for checkfield, search, newvar, value in self.list_entry_ops:
                    if checkfield == field and search in values:
                        if newvar in self.bbvar_map:
                            newvar = self.bbvar_map[newvar]
                        bbinfo[newvar] = value
                continue
            else:
                value = ' '.join(v for v in values if v)

            if field in self.bbvar_map:
                bbvar = self.bbvar_map[field]
                if bbvar not in bbinfo:
                    bbinfo[bbvar] = value


        comment_lic_line = None
        for pos, line in enumerate(list(lines_before)):
            if line.startswith('#') and 'LICENSE' in line:
                comment_lic_line = pos
            elif line.startswith('LICENSE =') and 'LICENSE' in bbinfo:
                if line in ('LICENSE = "Unknown"', 'LICENSE = "CLOSED"'):
                    lines_before[pos] = 'LICENSE = "{}"'.format(bbinfo['LICENSE'])
                    if line == 'LICENSE = "CLOSED"' and comment_lic_line:
                        lines_before[comment_lic_line:pos] = [
                            '# WARNING: the following LICENSE value is a best guess - it is your',
                            '# responsibility to verify that the value is complete and correct.'
                        ]
                    del bbinfo['LICENSE']

        src_uri_line = None
        for pos, line in enumerate(lines_before):
            if line.startswith('SRC_URI ='):
                src_uri_line = pos

        mdinfo = ['']
        for k in sorted(bbinfo):
            v = bbinfo[k]
            if not v:
                continue
            else:
                mdinfo.append('{} = "{}"'.format(k, v))
        if bbinfo:
            lines_before[src_uri_line-1:src_uri_line-1] = mdinfo

        mapped_deps, unmapped_deps = self.scan_setup_python_deps(srctree, setup_info, setup_non_literals)

        inst_reqs = set()
        if 'Install-requires' in info:
            inst_reqs = info['Install-requires']
            if inst_reqs:
                unmapped_deps.difference_update(inst_reqs)

                inst_req_deps = ('python-' + r.replace('.', '-').lower() for r in sorted(inst_reqs))
                lines_after.append('# WARNING: the following rdepends are from setuptools install_requires. These')
                lines_after.append('# upstream names may not correspond exactly to bitbake package names.')
                lines_after.append('RDEPENDS_${{PN}} += "{}"'.format(' '.join(inst_req_deps)))

        extras_req = set()
        if 'Extras-require' in info:
            extras_req = info['Extras-require']
            if extras_req:
                if inst_reqs:
                    lines_after.append('')
                lines_after.append('# The following configs & dependencies are from setuptools extras_require.')
                lines_after.append('# These dependencies are optional, hence can be controlled via PACKAGECONFIG.')
                lines_after.append('# The upstream names may not correspond exactly to bitbake package names.')
                lines_after.append('#')
                lines_after.append('# Uncomment this line to enable all the optional features.')
                lines_after.append('#PACKAGECONFIG ?= "{}"'.format(' '.join(k.lower() for k in extras_req.iterkeys())))
                for feature, feature_reqs in extras_req.iteritems():
                    unmapped_deps.difference_update(feature_reqs)

                    feature_req_deps = ('python-' + r.replace('.', '-').lower() for r in sorted(feature_reqs))
                    lines_after.append('PACKAGECONFIG[{}] = ",,,{}"'.format(feature.lower(), ' '.join(feature_req_deps)))

        if mapped_deps:
            name = info.get('Name')
            if name and name[0] in mapped_deps:
                # Attempt to avoid self-reference
                mapped_deps.remove(name[0])
            mapped_deps -= set(self.excluded_pkgdeps)
            if inst_reqs or extras_req:
                lines_after.append('')
            lines_after.append('# WARNING: the following rdepends are determined through basic analysis of the')
            lines_after.append('# python sources, and might not be 100% accurate.')
            lines_after.append('RDEPENDS_${{PN}} += "{}"'.format(' '.join(sorted(mapped_deps))))

        unmapped_deps -= set(self.assume_provided)
        if unmapped_deps:
            if mapped_deps:
                lines_after.append('')
            lines_after.append('# WARNING: We were unable to map the following python package/module')
            lines_after.append('# dependencies to the bitbake packages which include them:')
            lines_after.extend('#    {}'.format(d) for d in sorted(unmapped_deps))

        handled.append('buildsystem')

    def get_pkginfo(self, pkginfo_fn):
        msg = email.message_from_file(open(pkginfo_fn, 'r'))
        msginfo = {}
        for field in msg.keys():
            values = msg.get_all(field)
            if len(values) == 1:
                msginfo[field] = values[0]
            else:
                msginfo[field] = values
        return msginfo

    def parse_setup_py(self, setupscript='./setup.py'):
        setup_ast = self.parse_ast(setupscript)
        visitor = SetupScriptVisitor()
        visitor.visit(setup_ast)
        info = visitor.setup_data
        # Naive mapping of setup() arguments to PKG-INFO field names
        def _map(key):
            key = key.replace('_', '-')
            key = key[0].upper() + key[1:]
            if key in self.setup_parse_map:
                key = self.setup_parse_map[key]
            return key

        for key, value in info.items():
            new_key = _map(key)
            if new_key != key:
                del info[key]
                info[new_key] = value
        non_literals = [_map(k) for k in visitor.non_literals]
        return info, 'setuptools' in visitor.imported_modules, non_literals

    @staticmethod
    def parse_ast(filename):
        with codecs.open(filename, 'r') as f:
            source = f.read()
        return ast.parse(source, filename)

    def get_setup_args_info(self, setupscript='./setup.py'):
        cmd = ['python', setupscript]
        info = {}
        keys = set(self.bbvar_map.keys())
        keys |= set(self.setuparg_list_fields)
        keys |= set(self.setuparg_multi_line_values)
        grouped_keys = itertools.groupby(keys, lambda k: (k in self.setuparg_list_fields, k in self.setuparg_multi_line_values))
        for index, keys in grouped_keys:
            if index == (True, False):
                # Splitlines output for each arg as a list value
                for key in keys:
                    arg = self.setuparg_map.get(key, key.lower())
                    try:
                        arg_info = self.run_command(cmd + ['--' + arg], cwd=os.path.dirname(setupscript))
                    except (OSError, subprocess.CalledProcessError):
                        pass
                    else:
                        info[key] = [l.rstrip() for l in arg_info.splitlines()]
            elif index == (False, True):
                # Entire output for each arg
                for key in keys:
                    arg = self.setuparg_map.get(key, key.lower())
                    try:
                        arg_info = self.run_command(cmd + ['--' + arg], cwd=os.path.dirname(setupscript))
                    except (OSError, subprocess.CalledProcessError):
                        pass
                    else:
                        info[key] = arg_info
            else:
                info.update(self.get_setup_byline(list(keys), setupscript))
        return info

    def get_setup_byline(self, fields, setupscript='./setup.py'):
        info = {}

        cmd = ['python', setupscript]
        cmd.extend('--' + self.setuparg_map.get(f, f.lower()) for f in fields)
        try:
            info_lines = self.run_command(cmd, cwd=os.path.dirname(setupscript)).splitlines()
        except (OSError, subprocess.CalledProcessError):
            pass
        else:
            if len(fields) != len(info_lines):
                logger.error('Mismatch between setup.py output lines and number of fields')
                sys.exit(1)

            for lineno, line in enumerate(info_lines):
                line = line.rstrip()
                info[fields[lineno]] = line
        return info

    def scan_setup_python_deps(self, srctree, setup_info, setup_non_literals):
        if 'Package-dir' in setup_info:
            package_dir = setup_info['Package-dir']
        else:
            package_dir = {}

        class PackageDir(distutils.command.build_py.build_py):
            def __init__(self, package_dir):
                self.package_dir = package_dir

        pd = PackageDir(package_dir)
        to_scan = []
        if not any(v in setup_non_literals for v in ['Py-modules', 'Scripts', 'Packages']):
            if 'Py-modules' in setup_info:
                for module in setup_info['Py-modules']:
                    try:
                        package, module = module.rsplit('.', 1)
                    except ValueError:
                        package, module = '.', module
                    module_path = os.path.join(pd.get_package_dir(package), module + '.py')
                    to_scan.append(module_path)

            if 'Packages' in setup_info:
                for package in setup_info['Packages']:
                    to_scan.append(pd.get_package_dir(package))

            if 'Scripts' in setup_info:
                to_scan.extend(setup_info['Scripts'])

        if not to_scan:
            to_scan = ['.']

        provided_packages = self.parse_pkgdata_for_python_packages()
        scanned_deps = self.scan_python_dependencies([os.path.join(srctree, p) for p in to_scan])
        mapped_deps, unmapped_deps = set(self.base_pkgdeps), set()
        for dep in scanned_deps:
            mapped = provided_packages.get(dep)
            if mapped:
                mapped_deps.add(mapped)
            else:
                unmapped_deps.add(dep)
        return mapped_deps, unmapped_deps

    def scan_python_dependencies(self, paths):
        deps = set()
        try:
            dep_output = self.run_command(['pythondeps', '-d'] + paths)
        except (OSError, subprocess.CalledProcessError):
            pass
        else:
            for line in dep_output.splitlines():
                line = line.rstrip()
                dep, filename = line.split('\t', 1)
                if filename.endswith('/setup.py'):
                    continue
                deps.add(dep)

        try:
            provides_output = self.run_command(['pythondeps', '-p'] + paths)
        except (OSError, subprocess.CalledProcessError):
            pass
        else:
            provides_lines = (l.rstrip() for l in provides_output.splitlines())
            provides = set(l for l in provides_lines if l and l != 'setup')
            deps -= provides

        return deps

    def apply_info_replacements(self, info):
        for variable, search, replace in self.replacements:
            if variable not in info:
                continue

            def replace_value(search, replace, value):
                if replace is None:
                    if re.search(search, value):
                        return None
                else:
                    new_value = re.sub(search, replace, value)
                    if value != new_value:
                        return new_value
                return value

            value = info[variable]
            if isinstance(value, basestring):
                new_value = replace_value(search, replace, value)
                if new_value is None:
                    del info[variable]
                elif new_value != value:
                    info[variable] = new_value
            elif hasattr(value, 'get'):
                for dkey, dvalue in value.iteritems():
                    new_list = []
                    for pos, a_value in enumerate(dvalue):
                        new_value = replace_value(search, replace, a_value)
                        if new_value is not None and new_value != value:
                            new_list.append(new_value)

                    if value != new_list:
                        value[dkey] = new_list
            else:
                new_list = []
                for pos, a_value in enumerate(value):
                    new_value = replace_value(search, replace, a_value)
                    if new_value is not None and new_value != value:
                        new_list.append(new_value)

                if value != new_list:
                    info[variable] = new_list

    def parse_pkgdata_for_python_packages(self):
        suffixes = [t[0] for t in imp.get_suffixes()]
        pkgdata_dir = tinfoil.config_data.getVar('PKGDATA_DIR', True)

        ldata = tinfoil.config_data.createCopy()
        bb.parse.handle('classes/python-dir.bbclass', ldata, True)
        python_sitedir = ldata.getVar('PYTHON_SITEPACKAGES_DIR', True)

        dynload_dir = os.path.join(os.path.dirname(python_sitedir), 'lib-dynload')
        python_dirs = [python_sitedir + os.sep,
                       os.path.join(os.path.dirname(python_sitedir), 'dist-packages') + os.sep,
                       os.path.dirname(python_sitedir) + os.sep]
        packages = {}
        for pkgdatafile in glob.glob('{}/runtime/*'.format(pkgdata_dir)):
            files_info = None
            with open(pkgdatafile, 'r') as f:
                for line in f.readlines():
                    field, value = line.split(': ', 1)
                    if field == 'FILES_INFO':
                        files_info = ast.literal_eval(value)
                        break
                else:
                    continue

            for fn in files_info.iterkeys():
                for suffix in suffixes:
                    if fn.endswith(suffix):
                        break
                else:
                    continue

                if fn.startswith(dynload_dir + os.sep):
                    base = os.path.basename(fn)
                    provided = base.split('.', 1)[0]
                    packages[provided] = os.path.basename(pkgdatafile)
                    continue

                for python_dir in python_dirs:
                    if fn.startswith(python_dir):
                        relpath = fn[len(python_dir):]
                        relstart, _, relremaining = relpath.partition(os.sep)
                        if relstart.endswith('.egg'):
                            relpath = relremaining
                        base, _ = os.path.splitext(relpath)

                        if '/.debug/' in base:
                            continue
                        if os.path.basename(base) == '__init__':
                            base = os.path.dirname(base)
                        base = base.replace(os.sep + os.sep, os.sep)
                        provided = base.replace(os.sep, '.')
                        packages[provided] = os.path.basename(pkgdatafile)
        return packages

    @classmethod
    def run_command(cls, cmd, **popenargs):
        if 'stderr' not in popenargs:
            popenargs['stderr'] = subprocess.STDOUT
        try:
            return subprocess.check_output(cmd, **popenargs)
        except OSError as exc:
            logger.error('Unable to run `{}`: {}', ' '.join(cmd), exc)
            raise
        except subprocess.CalledProcessError as exc:
            logger.error('Unable to run `{}`: {}', ' '.join(cmd), exc.output)
            raise


class SetupScriptVisitor(ast.NodeVisitor):
    def __init__(self):
        ast.NodeVisitor.__init__(self)
        self.non_literals = []
        self.setup_data = {}
        self.imported_modules = set()

    def visit_Import(self, node):
        for alias in node.names:
            self.imported_modules.add(alias.name)

    def visit_ImportFrom(self, node):
        self.imported_modules.add(node.module)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Call) and \
           isinstance(node.value.func, ast.Name) and \
           node.value.func.id == 'setup':
            self.visit_setup(node.value)
            self.generic_visit(node.value)

    def visit_setup(self, node):
        for keyword in node.keywords:
            name = keyword.arg
            try:
                value = ast.literal_eval(keyword.value)
            except ValueError:
                self.non_literals.append(name)
            else:
                self.setup_data[name] = value


def plugin_init(pluginlist):
    pass


def register_recipe_handlers(handlers):
    # We need to make sure this is ahead of the makefile fallback handler
    handlers.insert(0, PythonRecipeHandler())
