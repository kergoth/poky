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

        # Remove currently unhandled version numbers from these variables
        ('Requires', r' *\([^)]*\)', ''),
        ('Provides', r' *\([^)]*\)', ''),
        ('Obsoletes', r' *\([^)]*\)', ''),
        ('Install-requires', r'^([^><= ]+).*', r'\1'),
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
        ('Classifier', 'License :: OSI Approved :: GNU General Public License (GPL)', 'License', 'GPL-1.0'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License v2 (GPLv2)', 'License', 'GPL-2.0'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)', 'License', 'GPL-2.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License v3 (GPLv3)', 'License', 'GPL-3.0'),
        ('Classifier', 'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)', 'License', 'GPL-3.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU Lesser General Public License v2 (LGPLv2)', 'License', 'LGPL-2.0'),
        ('Classifier', 'License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)', 'License', 'LGPL-2.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)', 'License', 'LGPL-3.0'),
        ('Classifier', 'License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)', 'License', 'LGPL-3.0+'),
        ('Classifier', 'License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)', 'License', 'LGPL-1.0'),
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
                    for line in f.readlines():
                        line = line.rstrip()
                        if not line:
                            continue

                        # We don't currently support sections for optional deps
                        if line.startswith('['):
                            break
                        inst_req.append(line)
                    info['Install-requires'] = inst_req
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

        for k in sorted(bbinfo):
            v = bbinfo[k]
            if not v:
                continue
            else:
                lines_before.append('{} = "{}"'.format(k, v))
        if bbinfo:
            lines_before.append('')

        inst_reqs = set()
        if 'Install-requires' in info:
            inst_reqs |= set(info['Install-requires'])
            if inst_reqs:
                lines_after.append('# WARNING: the following rdepends are from setuptools install_requires. These')
                lines_after.append('# upstream names may not correspond exactly to bitbake package names.')
                lines_after.append('RDEPENDS_${{PN}} += "{}"'.format(' '.join(r.lower() for r in sorted(inst_reqs))))

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
        for key, value in info.items():
            del info[key]
            key = key.replace('_', '-')
            key = key[0].upper() + key[1:]
            if key in self.setup_parse_map:
                key = self.setup_parse_map[key]
            info[key] = value
        return info, 'setuptools' in visitor.imported_modules, visitor.non_literals

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
            else:
                new_list = []
                for pos, a_value in enumerate(value):
                    new_value = replace_value(search, replace, a_value)
                    if new_value is not None and new_value != value:
                        new_list.append(new_value)

                if value != new_list:
                    info[variable] = new_list

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
