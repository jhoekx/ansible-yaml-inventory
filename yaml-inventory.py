#!/usr/bin/env python
# Support a YAML file hosts.yml as external inventory in Ansible

# Copyright (C) 2012  Jeroen Hoekx <jeroen@hoekx.be>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
File format:

- <hostname>

or

- host: <hostname>
  vars:
    - myvar: value
    - myvbr: vblue
  groups:
    - membergroup1
    - membergroup2

or

- group: <groupname>
  vars:
    - groupvar: value
  hosts:
    - myhost1
    - host: myhost2
      vars:
        - myvcr: vclue
  children:
    - child1
    - child2
  parents:
    - parent1
    - parent2

Any statement except the first definition is optional.

A group label: sets a variable on the group with the label key. eg:

- group: james
  label: name

is identical to:

- group: james
  vars: 
    - name: james

Nesting is also allowed:

- group: all
  vars:
   - product: myapplication
   - tier: production

- group: postgresql
  children:
    - group: siteA
      vars:
        - virtual_ip: 192.168.2.252
      hosts:
        - 192.168.2.245
        - 192.168.2.246
        - host: 192.168.2.247
          vars:
            - promotable: False
          groups:
            - backup

    - group: siteB
      vars:
        - virtual_ip: 192.168.2.253
      hosts:
        - 192.168.2.248
        - 192.168.2.249
        - host: 192.168.2.250
          vars:
            - promotable: False

- group: app
  children:
    - group: siteA
      hosts:
        - 192.168.2.1
        - 192.168.2.2

    - group: siteB
      hosts:
        - 192.168.2.3
        - 192.168.2.4

"""

import json
import os
import sys
import yaml

from optparse import OptionParser


### import a dummy class to use for globals
class globals: pass

class Host():
    def __init__(self, name):
        self.name = name
        self.groups = []
        self.vars = {}
        globals.all_hosts.add_host(self)
        globals.meta.add_host(self)

    def __repr__(self):
        return "Host('%s')"%(self.name)

    def set_variable(self, key, value):
        self.vars[key] = value

    def get_variables(self):
        result = {}
        for group in self.groups:
            for k,v in group.get_variables().items():
                if type(v) == dict:
                    if k in result:
                        result[k].update(v)
                    else:
                        result[k] = v.copy()
                else:
                    result[k] = v

        for k, v in self.vars.items():
            if type(v) == dict:
                if k in result:
                    result[k].update(v)
                else:
                    result[k] = v.copy()
            else:
                result[k] = v
        return result

    def add_group(self, group):
        if group not in self.groups:
            self.groups.append(group)

class Group():
    def __init__(self, name):
        self.name = name
        self.hosts = []
        self.vars = {}
        self.children = []
        self.parents = []

    def __repr__(self):
        return "Group('%s')"%(self.name)

    def get_hosts(self):
        """ List all hosts in this group, not including children """
        result = [ host for host in self.hosts ]
        return result

    def add_host(self, host):
        if host not in self.hosts:
            self.hosts.append(host)
            host.add_group(self)

    def add_child(self, group):
        if group.name == 'all':
            group = globals.meta
        if group not in self.children:
            self.children.append(group)
            group.add_parent(self)

    def add_parent(self, group):
        if group not in self.parents:
            self.parents.append(group)
            group.add_child(self)

    def set_variable(self, key, value):
        self.vars[key] = value

    def get_variables(self):
        result = {}
        for group in self.parents:
            result.update( group.get_variables() )
        result.update(self.vars)
        return result

def find_group(name, groups):
    for group in groups:
        if name == group.name:
            return group

def find_host(name):
    for host in globals.all_hosts.get_hosts():
        if name == host.name:
            return host

def import_vars(vars, obj):
    for var_file in vars:
        with open(var_file) as f:
            parse_vars(yaml.load(f.read()), obj)

def parse_vars(vars, obj):
    ### vars can be a list of dicts or a dictionary
    if type(vars) == dict:
        for k,v in vars.items():
            obj.set_variable(k, v)
    elif type(vars) == list:
        for var in vars:
            k,v = var.items()[0]
            obj.set_variable(k, v)

def parse_group(entry):
    group = None
    if type(entry) in [str, unicode]:
        group = find_group(entry, globals.groups)
        if not group:
            group = Group(entry)
            globals.groups.append(group)
    if 'group' in entry:
        group = find_group(entry['group'], globals.groups)
        if not group:
            group = Group(entry['group'])
            globals.groups.append(group)

    if 'label' in entry:
        group.set_variable(entry['label'], entry['group'])

    if 'import_vars' in entry:
        import_vars(entry['import_vars'], group)

    if 'vars' in entry:
        parse_vars(entry['vars'], group)

    if 'hosts' in entry:
        for host_entry in entry['hosts']:
            host = parse_host(host_entry)
            if host:
                group.add_host(host)

    if 'children' in entry:
        for child_entry in entry['children']:
            if type(child_entry) in [str, unicode]:
                child_name = child_entry
            elif 'group' in entry:
                child_name = child_entry['group'] 

            child = parse_group(child_entry)
            if child:
                group.add_child(child)

    if 'parents' in entry:
        for parent_entry in entry['parents']:
            if type(parent_entry) in [str, unicode]:
                parent_name = parent_entry
            elif 'group' in entry:
                parent_name = parent_entry['group'] 

            parse_group(parent_entry)
            parent = find_group(parent_name, globals.groups)
            group.add_parent(parent)

    return group

def parse_host(entry):
    host = None
    ### a host is either a dict or a single line definition
    if type(entry) in [str, unicode]:
        host = find_host(entry)
        if not host:
            host = Host(entry)

    elif 'host' in entry:
        host = find_host(entry['host'])
        if not host:
            host = Host(entry['host'])

        if 'vars' in entry:
            parse_vars(entry['vars'], host)

        if 'import_vars' in entry:
            import_vars(entry['import_vars'], host)

        if 'groups' in entry:
            for group_entry in entry['groups']:
                group = parse_group(group_entry)
                group.add_host(host) 

    return host

def parse_yaml(yaml_config):
    globals.groups = []
    globals.all_hosts = Group('all')
    ### this is a special group to allow parents of all, which you can't do normally
    globals.meta = Group('_all')
    globals.groups.append(globals.all_hosts)
    globals.groups.append(globals.meta)

    for entry in yaml_config:
        if 'group' in entry:
            parse_group(entry)

        if 'host' in entry:
            parse_host(entry)

base_dir = os.path.dirname(os.path.realpath(__file__))

parser = OptionParser()
parser.add_option('-f', '--file', default=os.environ.get('YAML_INV', os.path.join(base_dir, "hosts.yml")), dest="yaml_file")
parser.add_option('-p', '--pretty', default=False, dest="pretty_print",  action="store_true")
parser.add_option('-l', '--list', default=False, dest="list_hosts", action="store_true")
parser.add_option('-H', '--host', default=None, dest="host")
parser.add_option('-e', '--extra-vars', default=None, dest="extra")
options, args = parser.parse_args()

hosts_file = options.yaml_file

try:
    with open(hosts_file) as f:
        yaml_config = yaml.load(f.read())

except IOError:
    sys.stderr.write("Can't open file " + hosts_file)
    sys.exit(1)

parse_yaml(yaml_config)

if options.list_hosts == True:
    result = {}
    result['_meta'] = {}
    result['_meta']['hostvars'] = {}
    for group in globals.groups:
        if group.name == 'all':
            continue
        result[group.name]={}
        result[group.name]['hosts'] = [host.name for host in group.get_hosts()]
        result[group.name]['vars'] = group.vars
        result[group.name]['children'] = [child.name for child in group.children]
        result[group.name]['parents'] = [parent.name for parent in group.parents]
    for host in globals.all_hosts.get_hosts():
        result['_meta']['hostvars'][host.name] = host.get_variables()
        if options.extra:
            k,v = options.extra.split("=")
            result[k] = v

    if options.pretty_print:
        print json.dumps(result, sort_keys=True,
            indent=4, separators=(',', ': '))
    else:
        print json.dumps(result)

    sys.exit(0)

if options.host is not None:
    result = {}
    host = None
    for test_host in globals.all_hosts.get_hosts():
        if test_host.name == options.host:
            host = test_host
            break
    result = host.get_variables()
    if options.extra:
        k,v = options.extra.split("=")
        result[k] = v
    if options.pretty_print:
        print json.dumps(result, sort_keys=True,
            indent=4, separators=(',', ': '))
    else:
        print json.dumps(result)
    sys.exit(0)

parser.print_help()
sys.exit(1)
