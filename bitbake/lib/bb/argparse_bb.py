import argparse


# Add parser groups, patch from http://bugs.python.org/issue9341. Note that
# this is likely affected by http://bugs.python.org/issue16807.
class GroupSubParsersAction(argparse._SubParsersAction):
    class _PseudoGroup(argparse.Action):
        def __init__(self, container, title):
            sup = super(GroupSubParsersAction._PseudoGroup, self)
            sup.__init__(option_strings=[], dest=title)
            self.container = container
            self._choices_actions = []

        def add_parser(self, name, **kwargs):
            # add the parser to the main Action, but move the pseudo action
            # in the group's own list
            parser = self.container.add_parser(name, **kwargs)
            choice_action = self.container._choices_actions.pop()
            self._choices_actions.append(choice_action)
            return parser

        def _get_subactions(self):
            return self._choices_actions

        def add_parser_group(self, title):
            # the formatter can handle recursive subgroups
            grp = GroupSubParsersAction._PseudoGroup(self, title)
            self._choices_actions.append(grp)
            return grp

    def add_parser_group(self, title):
        grp = GroupSubParsersAction._PseudoGroup(self, title)
        self._choices_actions.append(grp)
        return grp


class ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super(ArgumentParser, self).__init__(*args, **kwargs)
        self.register('action', 'parsers', GroupSubParsersAction)
