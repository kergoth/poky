import argparse
import collections
import fnmatch
import logging
import os
import sys

import bb.utils

from bblayers.common import LayerPlugin

logger = logging.getLogger('bitbake-layers')


def plugin_init(plugins):
    return ActionPlugin()


class LayerNotFound(Exception):
    def __init__(self, name, parseerrors=None):
        self.name = name
        self.parseerrors = parseerrors

    def __repr__(self):
        return '{0.__class__.__name__}({0.name!r}, {0.parseerrors!r})'.format(self)

    def __str__(self):
        msg = 'Layer not found with name "{0.name}"'.format(self)
        if self.parseerrors:
            msg += '. Parse errors occurred:\n{0}'.format(''.join('  {0}: {1}\n'.format(lconf, err) for lconf, err in self.parseerrors))
        return msg


class DuplicateLayers(Exception):
    def __init__(self, name, paths):
        self.name = name
        self.paths = paths

    def __repr__(self):
        return '{0.__class__.__name__}({0.name!r}, {0.paths!r})'.format(self)

    def __str__(self):
        path_lines = ''.join('  %s\n' % p for p in self.paths)
        return 'Duplicate layers with name "{0.name}":\n{1}'.format(self, path_lines)


class ActionPlugin(LayerPlugin):
    def do_add_layer(self, args):
        """Add a layer to bblayers.conf."""
        layerdir = os.path.abspath(args.layerdir)
        if not os.path.exists(layerdir):
            sys.stderr.write("Specified layer directory doesn't exist\n")
            return 1

        layer_conf = os.path.join(layerdir, 'conf', 'layer.conf')
        if not os.path.exists(layer_conf):
            sys.stderr.write("Specified layer directory doesn't contain a conf/layer.conf file\n")
            return 1

        bblayers_conf = os.path.join('conf', 'bblayers.conf')
        if not os.path.exists(bblayers_conf):
            sys.stderr.write("Unable to find bblayers.conf\n")
            return 1

        notadded, _ = bb.utils.edit_bblayers_conf(bblayers_conf, layerdir, None)
        if notadded:
            for item in notadded:
                sys.stderr.write("Specified layer %s is already in BBLAYERS\n" % item)

    def do_remove_layer(self, args):
        """Remove a layer from bblayers.conf."""
        bblayers_conf = os.path.join('conf', 'bblayers.conf')
        if not os.path.exists(bblayers_conf):
            sys.stderr.write("Unable to find bblayers.conf\n")
            return 1

        if args.layerdir.startswith('*'):
            layerdir = args.layerdir
        elif not '/' in args.layerdir:
            layerdir = '*/%s' % args.layerdir
        else:
            layerdir = os.path.abspath(args.layerdir)
        (_, notremoved) = bb.utils.edit_bblayers_conf(bblayers_conf, None, layerdir)
        if notremoved:
            for item in notremoved:
                sys.stderr.write("No layers matching %s found in BBLAYERS\n" % item)
            return 1

    def do_flatten(self, args):
        """flatten layer configuration into a separate output directory.

Takes the specified layers (or all layers in the current layer
configuration if none are specified) and builds a "flattened" directory
containing the contents of all layers, with any overlayed recipes removed
and bbappends appended to the corresponding recipes. Note that some manual
cleanup may still be necessary afterwards, in particular:

* where non-recipe files (such as patches) are overwritten (the flatten
  command will show a warning for these)
* where anything beyond the normal layer setup has been added to
  layer.conf (only the lowest priority number layer's layer.conf is used)
* overridden/appended items from bbappends will need to be tidied up
* when the flattened layers do not have the same directory structure (the
  flatten command should show a warning when this will cause a problem)

Warning: if you flatten several layers where another layer is intended to
be used "inbetween" them (in layer priority order) such that recipes /
bbappends in the layers interact, and then attempt to use the new output
layer together with that other layer, you may no longer get the same
build results (as the layer priority order has effectively changed).
"""
        if len(args.layer) == 1:
            logger.error('If you specify layers to flatten you must specify at least two')
            return 1

        outputdir = args.outputdir
        if os.path.exists(outputdir) and os.listdir(outputdir):
            logger.error('Directory %s exists and is non-empty, please clear it out first' % outputdir)
            return 1

        layers = self.bblayers
        if len(args.layer) > 2:
            layernames = args.layer
            found_layernames = []
            found_layerdirs = []
            for layerdir in layers:
                layername = self.get_layer_name(layerdir)
                if layername in layernames:
                    found_layerdirs.append(layerdir)
                    found_layernames.append(layername)

            for layername in layernames:
                if not layername in found_layernames:
                    logger.error('Unable to find layer %s in current configuration, please run "%s show-layers" to list configured layers' % (layername, os.path.basename(sys.argv[0])))
                    return
            layers = found_layerdirs
        else:
            layernames = []

        # Ensure a specified path matches our list of layers
        def layer_path_match(path):
            for layerdir in layers:
                if path.startswith(os.path.join(layerdir, '')):
                    return layerdir
            return None

        applied_appends = []
        for layer in layers:
            overlayed = []
            for f in self.tinfoil.cooker.collection.overlayed.keys():
                for of in self.tinfoil.cooker.collection.overlayed[f]:
                    if of.startswith(layer):
                        overlayed.append(of)

            logger.plain('Copying files from %s...' % layer )
            for root, dirs, files in os.walk(layer):
                if '.git' in dirs:
                    dirs.remove('.git')
                if '.hg' in dirs:
                    dirs.remove('.hg')

                for f1 in files:
                    f1full = os.sep.join([root, f1])
                    if f1full in overlayed:
                        logger.plain('  Skipping overlayed file %s' % f1full )
                    else:
                        ext = os.path.splitext(f1)[1]
                        if ext != '.bbappend':
                            fdest = f1full[len(layer):]
                            fdest = os.path.normpath(os.sep.join([outputdir,fdest]))
                            bb.utils.mkdirhier(os.path.dirname(fdest))
                            if os.path.exists(fdest):
                                if f1 == 'layer.conf' and root.endswith('/conf'):
                                    logger.plain('  Skipping layer config file %s' % f1full )
                                    continue
                                else:
                                    logger.warning('Overwriting file %s', fdest)
                            bb.utils.copyfile(f1full, fdest)
                            if ext == '.bb':
                                for append in self.tinfoil.cooker.collection.get_file_appends(f1full):
                                    if layer_path_match(append):
                                        logger.plain('  Applying append %s to %s' % (append, fdest))
                                        self.apply_append(append, fdest)
                                        applied_appends.append(append)

        # Take care of when some layers are excluded and yet we have included bbappends for those recipes
        for b in self.tinfoil.cooker.collection.bbappends:
            (recipename, appendname) = b
            if appendname not in applied_appends:
                first_append = None
                layer = layer_path_match(appendname)
                if layer:
                    if first_append:
                        self.apply_append(appendname, first_append)
                    else:
                        fdest = appendname[len(layer):]
                        fdest = os.path.normpath(os.sep.join([outputdir,fdest]))
                        bb.utils.mkdirhier(os.path.dirname(fdest))
                        bb.utils.copyfile(appendname, fdest)
                        first_append = fdest

        # Get the regex for the first layer in our list (which is where the conf/layer.conf file will
        # have come from)
        first_regex = None
        layerdir = layers[0]
        for layername, pattern, regex, _ in self.tinfoil.cooker.bbfile_config_priorities:
            if regex.match(os.path.join(layerdir, 'test')):
                first_regex = regex
                break

        if first_regex:
            # Find the BBFILES entries that match (which will have come from this conf/layer.conf file)
            bbfiles = str(self.tinfoil.config_data.getVar('BBFILES', True)).split()
            bbfiles_layer = []
            for item in bbfiles:
                if first_regex.match(item):
                    newpath = os.path.join(outputdir, item[len(layerdir)+1:])
                    bbfiles_layer.append(newpath)

            if bbfiles_layer:
                # Check that all important layer files match BBFILES
                for root, dirs, files in os.walk(outputdir):
                    for f1 in files:
                        ext = os.path.splitext(f1)[1]
                        if ext in ['.bb', '.bbappend']:
                            f1full = os.sep.join([root, f1])
                            entry_found = False
                            for item in bbfiles_layer:
                                if fnmatch.fnmatch(f1full, item):
                                    entry_found = True
                                    break
                            if not entry_found:
                                logger.warning("File %s does not match the flattened layer's BBFILES setting, you may need to edit conf/layer.conf or move the file elsewhere" % f1full)

    def get_file_layer(self, filename):
        layerdir = self.get_file_layerdir(filename)
        if layerdir:
            return self.get_layer_name(layerdir)
        else:
            return '?'

    def get_file_layerdir(self, filename):
        layer = bb.utils.get_file_layer(filename, self.tinfoil.config_data)
        return self.bbfile_collections.get(layer, None)

    def apply_append(self, appendname, recipename):
        with open(appendname, 'r') as appendfile:
            with open(recipename, 'a') as recipefile:
                recipefile.write('\n')
                recipefile.write('##### bbappended from %s #####\n' % self.get_file_layer(appendname))
                recipefile.writelines(appendfile.readlines())

    def do_sort_layers(self, args):
        """Sort configured layers by layer priority."""
        layer_priorities = {}

        for layer, _, _, pri in self.tinfoil.cooker.recipecache.bbfile_config_priorities:
            layerdir = self.bbfile_collections.get(layer, None)
            layer_priorities[layerdir] = pri

        bblayers = list(sorted(layer_priorities, key=lambda l: layer_priorities[l], reverse=True))

        def set_first_bblayers_only(varname, origvalue, op, newlines):
            if bblayers:
                newvalue = list(bblayers)
                del bblayers[:]
                return newvalue, '=', 2, False
            else:
                return None, None, 2, False

        bblayers_conf = os.path.join('conf', 'bblayers.conf')
        if not os.path.exists(bblayers_conf):
            sys.stderr.write("Unable to find bblayers.conf\n")
            return 1
        bb.utils.edit_metadata_file(bblayers_conf, ['BBLAYERS'], set_first_bblayers_only)

    def find_layers(self, patternstring):
        """Return the layers for a given patternstring."""
        import glob

        patternstring = self.tinfoil.config_data.expand(patternstring)
        for pattern in patternstring.split():
            for lconf in glob.glob(os.path.join(pattern, 'conf', 'layer.conf')):
                lconf = os.path.realpath(lconf)
                layerdir = os.path.dirname(os.path.dirname(lconf))
                yield lconf, layerdir

    def get_layers_by_name(self, patternstring):
        """Return a mapping of layer name to layer path.

        Prefer those already in BBLAYERS to those which are found on disk.
        The layer name is the name defined in BBFILE_COLLECTIONS.
        """
        by_name, configured = collections.defaultdict(set), []
        varhistory = self.tinfoil.config_data.varhistory
        layer_filemap = varhistory.get_variable_items_files('BBFILE_COLLECTIONS', self.tinfoil.config_data)
        for item, filename in layer_filemap.iteritems():
            by_name[item].add(os.path.dirname(os.path.dirname(filename)))
            configured.append(item)

        data, errors = bb.data.init(), []
        bb.parse.init_parser(data)
        for lconf, layerdir in sorted(self.find_layers(patternstring)):
            ldata = data.createCopy()
            ldata.setVar('LAYERDIR', layerdir)
            try:
                ldata = bb.parse.handle(lconf, ldata, include=True)
            except BaseException as exc:
                errors.append([lconf, exc])
                continue

            names = (ldata.getVar('BBFILE_COLLECTIONS', True) or '').split()
            if not names:
                names = [os.path.basename(layerdir)]

            if any(name in configured for name in names):
                # Prioritize layers already in BBLAYERS
                continue

            for name in names:
                by_name[name].add(layerdir)

        return by_name, errors

    def find_layers_by_name(self, names, patternstring):
        by_name, errors = self.get_layers_by_name(patternstring)
        for name in names:
            layerdirs = by_name.get(name)
            if not layerdirs:
                raise LayerNotFound(name, errors)
            elif len(layerdirs) > 1:
                raise DuplicateLayers(name, layerdirs)
            else:
                layerdir = next(iter(layerdirs))
                yield name, layerdir

    def do_find_layer_by_name(self, args):
        """find the layer(s) for a given layer name, using a specific list of layers/wildcards to search"""
        try:
            for name, layerdir in self.find_layers_by_name(args.names, args.search_globs):
                logger.plain(layerdir)
        except LayerNotFound as exc:
            logger.error(str(exc))
            if exc.parseerrors:
                return 2
            else:
                return 1
        except DuplicateLayers as exc:
            logger.error(str(exc))
            return 3

    def do_find_layer_with_path(self, args):
        """find the layers which contain a specified path, using a specific list of layers/wildcards to search"""
        layers = collections.defaultdict(list)
        for lconf, layerdir in self.find_layers(args.search_globs):
            for path in args.paths:
                if os.path.exists(os.path.join(layerdir, path)):
                    layers[path].append(layerdir)

        for path in args.paths:
            if path in layers:
                for layer in layers[path]:
                    logger.plain(layer)
            else:
                logger.error('Failed to find layers including "%s"', path)
                return 1

    def register_commands(self, sp):
        parser_add_layer = self.add_command(sp, 'add-layer', self.do_add_layer, parserecipes=False)
        parser_add_layer.add_argument('layerdir', help='Layer directory to add')

        parser_remove_layer = self.add_command(sp, 'remove-layer', self.do_remove_layer, parserecipes=False)
        parser_remove_layer.add_argument('layerdir', help='Layer directory to remove (wildcards allowed, enclose in quotes to avoid shell expansion)')
        parser_remove_layer.set_defaults(func=self.do_remove_layer)

        parser_flatten = self.add_command(sp, 'flatten', self.do_flatten)
        parser_flatten.add_argument('layer', nargs='*', help='Optional layer(s) to flatten (otherwise all are flattened)')
        parser_flatten.add_argument('outputdir', help='Output directory')

        self.add_command(sp, 'sort-layers', self.do_sort_layers, parserecipes=False)

        find_common = argparse.ArgumentParser(add_help=False)
        find_common.add_argument('-g', '--search-globs', default='* */* ${COREBASE}/../* ${COREBASE}/../*/*', help='Space-separated list of layers to search (default: %(default)s)')

        find_layer_by_name = self.add_command(sp, 'find-layer-by-name', self.do_find_layer_by_name, parents=[find_common], parserecipes=False)
        find_layer_by_name.add_argument('names', nargs='+', metavar='NAME', help='Layer names (as specified in layer.conf, in BBFILE_COLLECTIONS)')

        find_layer_by_path = self.add_command(sp, 'find-layer-with-path', self.do_find_layer_with_path, parents=[find_common], parserecipes=False)
        find_layer_by_path.add_argument('paths', nargs='+', metavar='PATH', help='Path to find')
