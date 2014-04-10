# -*- coding: utf-8 -*-
#
# Copyright (C) 2013-2013 Maarten de Vries <maarten@de-vri.es>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import weechat
import os.path
import re
import json

SCRIPT_NAME     = "autosort"
SCRIPT_AUTHOR   = "Maarten de Vries <maarten@de-vri.es>"
SCRIPT_VERSION  = "1.1"
SCRIPT_LICENSE  = "GPLv3"
SCRIPT_DESC     = "Automatically keeps buffers sorted just the way you want to."


config = None


class Pattern:
	''' A simple glob-like pattern for matching buffer names. '''

	def __init__(self, pattern):
		''' Construct a pattern from a string. '''
		escaped    = False
		char_class = 0
		chars      = ''
		regex      = ''
		for c in pattern:
			if escaped and char_class:
				escaped = False
				chars += re.escape([c])
			elif escaped:
				escaped = False
				regex += re.escape([c])
			elif c == '\\':
				escaped = True
			elif c == '*':
				regex += '[^.]*'
			elif c == '?':
				regex += '[^.]'
			elif c == '[' and not char_class:
				char_class = 1
				chars    = ''
			elif c == '^' and char_class and not chars:
				chars += '^'
			elif c == ']' and char_class and chars not in ('', '^'):
				char_class = False
				regex += '[' + chars + ']'
			elif c == '-' and char_class:
				chars += '-'
			elif char_class:
				chars += re.escape(c)
			else:
				regex += re.escape(c)

		if char_class:
			raise ValueError("unmatched opening '['")
		if escaped:
			raise ValueError("unexpected trailing '\\'")

		self.regex   = re.compile('^' + regex + '$')
		self.pattern = pattern

	def match(self, input):
		''' Match the pattern against a string. '''
		return self.regex.match(input)


class RuleList:
	''' A list of rules to test buffer names against. '''

	def __init__(self, rules):
		''' Construct a RuleList from a list of rules. '''
		self.__rules   = rules
		self.__highest = 0
		for rule in rules:
			self.__highest = max(self.__highest, rule[1] + 1)

	def get_score(self, name, rules):
		''' Get the sort score of a partial name according to a rule list. '''
		for rule in self.__rules:
			if rule[0].match(name): return rule[1]
		return self.__highest

	def encode(self):
		''' Encode the rules for storage. '''
		return json.dumps(map(lambda x: (x[0].pattern, x[1]), self.__rules))

	def append(self, rule):
		''' Add a rule to the list. '''
		self.__rules.append(rule)
		self.__highest = max(self.__highest, rule[1] + 1)

	def insert(self, index, rule):
		''' Add a rule to the list. '''
		if not 0 <= index <= len(self): raise ValueError('Index out of range: expected between 0 and {}, got {}.'.format(len(self), index))
		self.__rules.insert(index, rule)
		self.__highest = max(self.__highest, rule[1] + 1)

	def pop(self, index):
		''' Remove a rule from the list and return it. '''
		if not 0 <= index < len(self): raise ValueError('Index out of range: expected between 0 and {}, got {}.'.format(len(self), index))
		return self.__rules.pop(index)

	def move(self, index_a, index_b):
		''' Move a rule to a new position in the list. '''
		self.insert(index_b, self.pop(index_a))

	def swap(self, index_a, index_b):
		''' Swap two rules in the list. '''
		self[index_a], self[index_b] = self[index_b], self[index_a]

	def __len__(self):
		return len(self.__rules)

	def __getitem__(self, index):
		if not 0 <= index < len(self): raise ValueError('Index out of range: expected between 0 and {}, got {}.'.format(len(self), index))
		return self.__rules[index]

	def __setitem__(self, index, rule):
		if not 0 <= index < len(self): raise ValueError('Index out of range: expected between 0 and {}, got {}.'.format(len(self), index))
		self.__highest      = max(self.__highest, rule[1] + 1)
		self.__rules[index] = value

	def __iter__(self):
		return iter(self.__rules)

	@staticmethod
	def decode(blob):
		''' Parse rules from a string blob. '''
		result = []

		try:
			decoded = json.loads(blob)
		except Exception:
			log("Invalid rules: expected JSON encoded list of pairs, got \"" + blob + "\".")
			return [], 0

		for rule in decoded:
			# Rules must be a pattern,score pair.
			if len(rule) != 2:
				log("Invalid rule: expected [pattern, score], got " + str(rule) + ". Rule ignored.")
				continue

			# Rules must have a valid pattern.
			try:
				pattern = Pattern(rule[0])
			except Exception as e:
				log("Invalid pattern: " + str(e) + " in \"" + rule[0] + "\". Rule ignored.")
				continue

			# Rules must have a valid score.
			try:
				score = int(rule[1])
			except Exception as e:
				log("Invalid score: expected an integer, got " + str(rule[1]) + ". Rule ignored.")
				continue

			result.append((pattern, score))

		return RuleList(result)


class Config:
	''' The autosort configuration. '''

	default_rules = json.dumps([
		('core', 0),
		('irc',  2),
		('*',    1),

		('irc.server',  1),
		('irc.irc_raw', 0),
	])

	def __init__(self, filename):
		''' Initialize the configuration. '''

		self.filename         = filename
		self.config_file      = weechat.config_new(self.filename, '', '')
		self.sorting_section  = None

		self.case_sensitive   = False
		self.group_irc        = True
		self.rules            = []
		self.highest          = 0

		self.__case_sensitive = None
		self.__group_irc      = None
		self.__rules          = None

		if not self.config_file:
			log('Failed to initialize configuration file "{}".'.format(self.filename))
			return

		self.sorting_section = weechat.config_new_section(self.config_file, 'sorting', False, False, '', '', '', '', '', '', '', '', '', '')

		if not self.sorting_section:
			log('Failed to initialize section "sorting" of configuration file.')
			weechat.config_free(config_file)
			return

		self.__case_sensitive = weechat.config_new_option(self.config_file, self.sorting_section,
			'case_sensitive', 'boolean',
			'If this option is on, sorting is case sensitive.',
			'', 0, 0, 'off', 'off', 0,
			'', '', '', '', '', ''
		)

		self.__group_irc = weechat.config_new_option(self.config_file, self.sorting_section,
			'group_irc', 'boolean',
			'If this option is on, the script pretends that IRC channel/private buffers are renamed to "irc.server.{network}.{channel}" rather than "irc.{network}.{channel}".' +
			'This ensures that thsee buffers are grouped with their respective server buffer.',
			'', 0, 0, 'on', 'on', 0,
			'', '', '', '', '', ''
		)

		self.__rules = weechat.config_new_option(self.config_file, self.sorting_section,
			'rules', 'string',
			'An ordered list of sorting rules encoded as JSON. See /help autosort for commands to manipulate these rules.',
			'', 0, 0, Config.default_rules, Config.default_rules, 0,
			'', '', '', '', '', ''
		)

		if weechat.config_read(self.config_file) != weechat.WEECHAT_RC_OK:
			log('Failed to load configuration file.')

		if weechat.config_write(self.config_file) != weechat.WEECHAT_RC_OK:
			log('Failed to write configuration file.')

		self.reload()

	def reload(self):
		''' Load configuration variables. '''

		self.case_sensitive = weechat.config_boolean(self.__case_sensitive)
		self.group_irc      = weechat.config_boolean(self.__group_irc)
		rules_blob          = weechat.config_string(self.__rules)
		self.rules          = RuleList.decode(rules_blob)

	def save_rules(self, run_callback = True):
		''' Save the current rules to the configuration. '''
		weechat.config_option_set(self.__rules, RuleList.encode(self.rules), run_callback)


def pad(sequence, length, padding = None):
	''' Pad a list until is has a certain length. '''
	return sequence + [padding] * max(0, (length - len(sequence)))


def log(message, buffer = 'NULL'):
	weechat.prnt(buffer, '[autosort] ' + str(message))


def get_buffers():
	''' Get a list of all the buffers in weechat. '''
	buffers = []

	buffer_list = weechat.infolist_get("buffer", "", "")

	while weechat.infolist_next(buffer_list):
		name   = weechat.infolist_string (buffer_list, 'full_name')
		number = weechat.infolist_integer(buffer_list, 'number')

		# Buffer is merged with one we already have in the list, skip it.
		if number <= len(buffers):
			continue
		buffers.append(name.split('.'))

	weechat.infolist_free(buffer_list)
	return buffers


def preprocess(buffer, config):
	'''
	Preprocess a buffers name components.
	This function modifies IRC buffers to group them under their server buffer if group_irc is set.
	'''
	result = list(buffer)
	if config.group_irc and len(result) >= 2 and buffer[0] == 'irc' and buffer[1] not in ('server', 'irc_raw'):
		result.insert(1, 'server')

	if not config.case_sensitive:
		result = map(lambda x: x.lower(), result)

	return result;


def buffer_sort_key(rules):
	''' Create a sort key function for a buffer list from a rule list. '''
	def key(buffer):
		result  = []
		name    = ""
		for word in preprocess(buffer, config):
			name += ("." if name else "") + word
			result.append((rules.get_score(name, rules), word))
		return result

	return key


def apply_buffer_order(buffers):
	''' Sort the buffers in weechat according to the order in the input list.  '''
	i = 1
	for buffer in buffers:
		weechat.command('', '/buffer swap {} {}'.format('.'.join(buffer), i))
		i += 1


def split_args(args, expected):
	''' Split an argument string in the desired number of arguments. '''
	split = args.split(' ', expected - 1)
	if (len(split) != expected):
		raise ValueError('Expected exactly ' + expected + ' arguments, got ' + str(len(split)) + '.')
	return split


def parse_rule_arg(arg):
	''' Parse a rule argument. '''
	stripped = arg.strip();
	match = parse_rule_arg.regex.match(stripped)
	if not match:
		raise ValueError('Invalid rule: expected "pattern = score", got "' + stripped + '".')

	pattern = match.group(1).strip()
	score   = match.group(2).strip()

	try:
		score = int(score)
	except Exception:
		raise ValueError('Invalid score: expected integer, got "' + score + '".')

	try:
		pattern = Pattern(pattern)
	except Exception as e:
		raise ValueError('Invalid pattern: ' + str(e) + ' in "' + pattern + '".')

	return (pattern, score)

parse_rule_arg.regex = re.compile(r'^(.*)=\s*([+-]?\d+)$')


def parse_index_arg(arg):
	''' Parse an index argument. '''
	stripped = arg.strip()
	try:
		return int(stripped)
	except ValueError:
		raise ValueError('Invalid index: expected integer, got "' + stripped + '".')


def command_rule_list(buffer, command, args):
	''' Show the list of sorting rules. '''
	output = 'Sorting rules:\n'
	for i, rule in enumerate(config.rules):
		output += '    {}: {} = {}\n'.format(i, rule[0].pattern, rule[1])
	log(output, buffer)

	return weechat.WEECHAT_RC_OK


def command_rule_add(buffer, command, args):
	''' Add a rule to the rule list. '''
	rule = parse_rule_arg(args)

	config.rules.append(rule)
	config.save_rules()
	command_rule_list(buffer, command, '')

	return weechat.WEECHAT_RC_OK


def command_rule_insert(buffer, command, args):
	''' Insert a rule at the desired position in the rule list. '''
	index, rule = split_args(args, 2)
	index = parse_index_arg(index)
	rule  = parse_rule_arg(rule)

	config.rules.insert(index, rule)
	config.save_rules()
	command_rule_list(buffer, command, '')
	return weechat.WEECHAT_RC_OK


def command_rule_update(buffer, command, args):
	''' Update a rule in the rule list. '''
	index, rule = split_args(args, 2)
	index = parse_index_arg(index)
	rule  = parse_rule_arg(rule)

	config.rules[index] = rule
	config.save_rules()
	command_rule_list(buffer, command, '')
	return weechat.WEECHAT_RC_OK



def command_rule_delete(buffer, command, args):
	''' Delete a rule from the rule list. '''
	index = args.strip()
	index = parse_index_arg(index)

	config.rules.pop(index)
	config.save_rules()
	command_rule_list(buffer, command, '')
	return weechat.WEECHAT_RC_OK


def command_rule_move(buffer, command, args):
	''' Move a rule to a new position. '''
	index_a, index_b = split_args(args, 2)
	index_a = parse_index_arg(index_a)
	index_b = parse_index_arg(index_b)

	config.rules.move(index_a, index_b)
	config.save_rules()
	command_rule_list(buffer, command, '')
	return weechat.WEECHAT_RC_OK


def command_rule_swap(buffer, command, args):
	''' Swap two rules. '''
	index_a, index_b = split_args(args, 2)
	index_a = parse_index_arg(index_a)
	index_b = parse_index_arg(index_b)

	config.rules.swap(index_a, index_b)
	config.save_rules()
	command_rule_list(buffer, command, '')
	return weechat.WEECHAT_RC_OK


commands = {
	'rule': {
		'list':   command_rule_list,
		'add':    command_rule_add,
		'insert': command_rule_insert,
		'update': command_rule_update,
		'delete': command_rule_delete,
		'move':   command_rule_move,
		'swap':   command_rule_swap,
	}
}


def call_command(buffer, command, args, subcommands):
	''' Call a subccommand from a dictionary. '''
	subcommand, tail = pad(args.split(' ', 1), 2, '')
	child            = subcommands.get(subcommand)
	command          = command + [subcommand]

	if isinstance(child, dict):
		return call_command(buffer, command, tail, child)
	elif callable(child):
		return child(buffer, command, tail)

	log(' '.join(command) + ": command not found");
	return weechat.WEECHAT_RC_ERROR


def on_buffers_changed(*args, **kwargs):
	''' Called whenever the buffer list changes. '''
	buffers = get_buffers()
	buffers.sort(key=buffer_sort_key(config.rules))
	apply_buffer_order(buffers)
	return weechat.WEECHAT_RC_OK


def on_config_changed(*args, **kwargs):
	''' Called whenever the configuration changes. '''
	config.reload()
	on_buffers_changed()
	return weechat.WEECHAT_RC_OK


def on_autosort_command(data, buffer, args):
	''' Called when the autosort command is invoked. '''
	try:
		return call_command(buffer, ['/autosort'], args, commands)
	except ValueError as e:
		log(e, buffer)
		return weechat.WEECHAT_RC_ERROR


command_description = '''\
/autosort rule list
Print the list of sort rules.

/autosort rule add <pattern> = <score>
Add a new rule at the end of the rule list.

/autosort rule insert <index> <pattern> = <score>
Insert a new rule at the given index in the rule list.

/autosort rule update <index> <pattern> = <score>
Update a rule in the list with a new pattern and score.

/autosort rule delete <index>
Delete a rule from the list.

/autosort rule move <index_from> <index_to>
Move a rule from one position in the list to another.

/autosort rule swap <index_a> <index_b>
Swap two rules in the list


Use the /autosort command to show and manipulate the autosort rules.
Autosort first turns buffer names into a list of their components by splitting on them on the period (.) character.
The buffers are then lexicographically sorted.

To facilitate custom sort orders, it is possible to assign a sort score to each component individually before the sorting is done.
Any name component that did not get a score assigned will be sorted after those that did receive a score.
Components are always sorted on their score first and then on their second.
Lower scores are sorted first.

Scores are assigned by defining sorting rules.
The first rule that matches a component decides the score.
Further rules are not examined.
Sort rules use the following syntax:
<glob-pattern> = <score>

Allowed special characters in the glob patterns are:
*        Match any sequence of characters except for periods.
?        Match a single character, but not a period.
[a-z]    Match a single character in the given regex-like character class.
[^ab]    Match a single character that is not in the given regex-like character class.


As an example, consider the following rule list:
0: core       = 0
1: irc        = 2
2: *          = 1
3: irc.server = 0

4: irc.server.*.#* = 1
5: irc.server.*.*  = 0

Rule 0 ensures the core buffer is always sorted first.
Rule 1 sorts IRC buffers last and rule 2 sorts all remaining buffers between the core and IRC buffers.
Rule 3 sorts IRC server buffers before any other type of IRC buffer.
Note that rule 3 assigns a score of 0 again.
This is perfectly fine and it does not conflict with rule 0,
because rule 0 only matches the first component of a buffer name
while rule 3 only matches the second component of a buffer name.

Rule 4 and 5 would make no sense with the group_irc option off.
Normally IRC channel or private buffers are called "irc.<network>.<#channel>" and their server buffers are called "irc.server.<network>".
With the option on though, autosort pretends that channel buffers are actually called "irc.server.<network>.<#channel>".
This way the channel and private buffers for a particular network are always grouped with their server buffer,
and the server buffer comes first because it has less components in its name.

So, what do rules 4 and 5 do, then?
Very simple, they sort private buffers (and channels not starting with a '#') before channel buffers.
Rule 4 matches channel buffers while rule 5 matches everything that remains and assigns it a lower score.
'''

command_completion = 'rule list|add|insert|update|delete|move|swap'


if weechat.register(SCRIPT_NAME, SCRIPT_AUTHOR, SCRIPT_VERSION, SCRIPT_LICENSE, SCRIPT_DESC, "", ""):
	config = Config('autosort')

	weechat.hook_signal('buffer_opened',   'on_buffers_changed', '')
	weechat.hook_signal('buffer_merged',   'on_buffers_changed', '')
	weechat.hook_signal('buffer_unmerged', 'on_buffers_changed', '')
	weechat.hook_signal('buffer_renamed',  'on_buffers_changed', '')
	weechat.hook_config('autosort.*',      'on_config_changed',  '')
	weechat.hook_command('autosort', command_description, '', '', command_completion, 'on_autosort_command', 'NULL')
	on_config_changed()
