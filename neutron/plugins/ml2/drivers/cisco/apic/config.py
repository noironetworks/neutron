# Copyright (c) 2014 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Arvind Somya (asomya@cisco.com), Cisco Systems Inc.

from oslo.config import cfg


DEFAULT_ROOT_HELPER = \
    'sudo /usr/local/bin/neutron-rootwrap /etc/neutron/rootwrap.conf'


apic_opts = [
    cfg.StrOpt('apic_host',
               help=_("Host name or IP Address of the APIC controller")),
    cfg.StrOpt('apic_username',
               default='admin',
               help=_("Username for the APIC controller")),
    cfg.StrOpt('apic_password',
               help=_("Password for the APIC controller"),
               secret=True),
    cfg.StrOpt('apic_port',
               default='80',
               help=_("Communication port for the APIC controller")),
    cfg.StrOpt('apic_name_mapping',
               default='use_name',
               help=_("Name mapping strategy to use: use_uuid | use_name")),
    cfg.StrOpt('apic_vmm_provider',
               default='VMware',
               help=_("Name for the VMM domain provider")),
    cfg.StrOpt('apic_vmm_domain',
               default='openstack',
               help=_("Name for the VMM domain to be created for Openstack")),
    cfg.StrOpt('apic_app_profile_name',
               default='openstack_app',
               help=_("Name for the app profile used for openstack")),
    cfg.StrOpt('apic_vlan_ns_name',
               default='openstack_ns',
               help=_("Name for the vlan namespace to be used for openstack")),
    cfg.StrOpt('apic_vlan_range',
               default='2:4093',
               help=_("Range of VLAN's to be used for Openstack")),
    cfg.StrOpt('apic_node_profile',
               default='openstack_profile',
               help=_("Name of the node profile to be created")),
    cfg.StrOpt('apic_entity_profile',
               default='openstack_entity',
               help=_("Name of the entity profile to be created")),
    cfg.StrOpt('apic_function_profile',
               default='openstack_function',
               help=_("Name of the function profile to be created")),
    cfg.FloatOpt('apic_agent_report_interval',
                 default=30,
                 help=_('Interval between agent status updates (in sec)')),
    cfg.FloatOpt('apic_agent_poll_interval',
                 default=2,
                 help=_('Interval between agent poll for topology (in sec)')),
    cfg.ListOpt('apic_host_uplink_ports',
                default=[],
                help=_('The uplink ports to check for ACI connectivity')),
    cfg.BoolOpt('apic_clear_node_profiles',
                default=False,
                help=_("Clear the node profiles on APIC at startup "
                       "(for testing)")),
    cfg.BoolOpt('apic_clear_driver_tables',
                default=False,
                help=_("Clear the apic specific db tables at startup "
                       "(for testing)")),
    cfg.StrOpt('root_helper',
               default=DEFAULT_ROOT_HELPER,
               help=_("Setup root helper as rootwrap or sudo")),
]


cfg.CONF.register_opts(apic_opts, "ml2_cisco_apic")


def switch_dictionary():
    switch_dict = {}
    multi_parser = cfg.MultiConfigParser()
    read_ok = multi_parser.read(cfg.CONF.config_file)

    if len(read_ok) != len(cfg.CONF.config_file):
        raise cfg.Error(_("Some config files were not parsed properly"))

    for parsed_file in multi_parser.parsed:
        for parsed_item in parsed_file.keys():
            if parsed_item.startswith('apic_switch'):
                switch, switch_id = parsed_item.split(':')
                if switch.lower() == 'apic_switch':
                    switch_dict[switch_id] = switch_dict.get(switch_id, {})
                    port_cfg = parsed_file[parsed_item].items()
                    for host_list, port in port_cfg:
                        hosts = host_list.split(',')
                        port = port[0]
                        switch_dict[switch_id][port] = \
                            switch_dict[switch_id].get(port, []) + hosts

    return switch_dict
