# Copyright (c) 2014 Cisco Systems Inc.
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

import netaddr

from oslo.config import cfg

from neutron.extensions import portbindings
from neutron.openstack.common import excutils
from neutron.openstack.common import log
from neutron.plugins.common import constants
from neutron.plugins.ml2 import driver_api as api
from neutron.plugins.ml2.drivers.cisco.apic import apic_manager


LOG = log.getLogger(__name__)


class APICMechanismDriver(api.MechanismDriver):

    def initialize(self):
        self.apic_manager = apic_manager.APICManager()
        self.name_mapper = apic_manager.APICNameMapper(
                self.apic_manager,
                apic_manager.NAMING_STRATEGY_NAMES)
        self.keystone = None
        self.tenants = {}

        # Create a VMM domain and VLAN namespace
        # Get vlan ns name
        ns_name = cfg.CONF.ml2_cisco_apic.apic_vlan_ns_name
        # Grab vlan ranges
        vlan_ranges = cfg.CONF.ml2_type_vlan.network_vlan_ranges[0]
        (vlan_min, vlan_max) = vlan_ranges.split(':')[-2:]
        # Create VLAN namespace
        vlan_ns = self.apic_manager.ensure_vlan_ns_created_on_apic(ns_name,
                                                                   vlan_min,
                                                                   vlan_max)
        vmm_name = cfg.CONF.ml2_cisco_apic.apic_vmm_domain
        phys_name = vmm_name
        # Create Physical domain
        self.apic_manager.ensure_phys_domain_created_on_apic(phys_name, vlan_ns)

        # Create entity profile
        ent_name = cfg.CONF.ml2_cisco_apic.apic_entity_profile
        self.apic_manager.ensure_entity_profile_created_on_apic(ent_name)

        # Create function profile
        func_name = cfg.CONF.ml2_cisco_apic.apic_function_profile
        self.apic_manager.ensure_function_profile_created_on_apic(func_name)

        # Create infrastructure on apic
        self.apic_manager.ensure_infra_created_on_apic()

    def _perform_port_operations(self, context):
        # Get tenant details from port context
        tenant_id = context.current['tenant_id']
        tenant_id = self.name_mapper.tenant(context, tenant_id)

        # Get network
        network_id = context.network.current['id']
        network_id = self.name_mapper.network(context, network_id)

        # Get port
        port = context.current

        # Get segmentation id
        if not context.bound_segment:
            LOG.debug(_("Port %s is not bound to a segment"), port)
            return
        seg = None
        if (context.bound_segment.get(api.NETWORK_TYPE) in
            [constants.TYPE_VLAN]):
            seg = context.bound_segment.get(api.SEGMENTATION_ID)

        # Check if a compute port
        if not port['device_owner'].startswith('compute'):
            # Not a compute port, return
            return

        host = port.get(portbindings.HOST_ID)
        # Check host that the dhcp agent is running on
        ports = context._plugin.get_ports(context._plugin_context)
        dhcp_host = None
        for dport in ports:
            if (dport.get('device_owner') == 'network:dhcp' and
                dport.get('network_id') == network_id):
                dhcp_host = dport.get(portbindings.HOST_ID)

        # Create a static path attachment for this host/epg/switchport combo
        self.apic_manager.ensure_tenant_created_on_apic(tenant_id)
        self.apic_manager.ensure_path_created_for_port(tenant_id, network_id,
                                                       host, seg)
        if dhcp_host is not None and host != dhcp_host:
            self.apic_manager.ensure_path_created_for_port(tenant_id, network_id,
                                                           dhcp_host, seg)

    def create_port_postcommit(self, context):
        self._perform_port_operations(context)

    def update_port_postcommit(self, context):
        self._perform_port_operations(context)

    def create_network_postcommit(self, context):
        tenant_id = context.current['tenant_id']
        network_id = context.current['id']

        # Convert to APIC IDs        
        tenant_id = self.name_mapper.tenant(context, tenant_id)
        network_id = self.name_mapper.network(context, network_id)

        self.apic_manager.ensure_bd_created_on_apic(tenant_id, network_id)
        # Create EPG for this network
        self.apic_manager.ensure_epg_created_for_network(tenant_id, network_id)

    def delete_network_postcommit(self, context):
        tenant_id = context.current['tenant_id']
        network_id = context.current['id']

        # Convert to APIC IDs        
        tenant_id = self.name_mapper.tenant(context, tenant_id)
        network_id = self.name_mapper.network(context, network_id)

        self.apic_manager.delete_bd_on_apic(tenant_id, network_id)
        self.apic_manager.delete_epg_for_network(tenant_id, network_id)

    def create_subnet_postcommit(self, context):
        tenant_id = context.current['tenant_id']
        network_id = context.current['network_id']
        gateway_ip = context.current['gateway_ip']
        cidr = netaddr.IPNetwork(context.current['cidr'])
        netmask = str(cidr.prefixlen)
        gateway_ip = gateway_ip + '/' + netmask

        # Convert to APIC IDs        
        tenant_id = self.name_mapper.tenant(context, tenant_id)
        network_id = self.name_mapper.network(context, network_id)

        self.apic_manager.ensure_subnet_created_on_apic(tenant_id, network_id,
                                                        gateway_ip)
