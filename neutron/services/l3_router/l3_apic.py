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

from oslo.config import cfg

from neutron.db import api as qdbapi
from neutron.db import db_base_plugin_v2
from neutron.db import extraroute_db
from neutron.db import l3_gwmode_db
from neutron.db import model_base
from neutron.plugins.common import constants
from neutron.plugins.ml2.drivers.cisco.apic import apic_manager


class ApicL3ServicePlugin(db_base_plugin_v2.NeutronDbPluginV2,
                          db_base_plugin_v2.CommonDbMixin,
                          extraroute_db.ExtraRoute_db_mixin,
                          l3_gwmode_db.L3_NAT_db_mixin):
    supported_extension_aliases = ["router", "ext-gw-mode", "extraroute"]

    def __init__(self):
        qdbapi.register_models(base=model_base.BASEV2)
        self.manager = apic_manager.APICManager()
        self.name_mapper = apic_manager.APICNameMapper(
                self.manager,
                cfg.CONF.ml2_cisco_apic.apic_name_mapping)
 
    def _map_names(self, context, tenant_id, network_id, subnet_id):
        context._plugin = self
        context._plugin_context = context   # temporary circular reference
        tenant_id = self.name_mapper.tenant(context, tenant_id)
        network_id = self.name_mapper.network(context, network_id)
        subnet_id = self.name_mapper.subnet(context, subnet_id)
        context._plugin_context = None      # break circular reference
        return  tenant_id, network_id, subnet_id

    @staticmethod
    def get_plugin_type():
        return constants.L3_ROUTER_NAT

    @staticmethod
    def get_plugin_description():
        """returns string description of the plugin."""
        return _("L3 Router Service Plugin for basic L3 using the APIC")

    def add_router_interface(self, context, router_id, interface_info):
        tenant_id = context.tenant_id
        subnet_id = interface_info['subnet_id']
        subnet = self.get_subnet(context, subnet_id)
        network_id = subnet['network_id']

        # Map openstack IDs to APIC IDs
        tenant_id, network_id, subnet_id = \
                self._map_names(context, tenant_id, network_id, subnet_id)

        # Program APIC
        self.manager.add_router_interface(tenant_id, router_id,
                                          network_id, subnet_id)

        # Create DB port
        port = super(ApicL3ServicePlugin, self).add_router_interface(
            context, router_id, interface_info)

        return port

    def remove_router_interface(self, context, router_id, interface_info):
        port = self.get_port(context, interface_info['port_id'])
        tenant_id = port['tenant_id']
        subnet_id = port['fixed_ips'][0]['subnet_id']
        subnet = self.get_subnet(context, subnet_id)
        network_id = subnet['network_id']

        # Map openstack IDs to APIC IDs
        tenant_id, network_id, subnet_id = \
                self._map_names(context, tenant_id, network_id, subnet_id)

        # Program APIC
        self.manager.remove_router_interface(tenant_id, router_id,
                                             network_id, subnet_id)

        # Delete DB port
        super(ApicL3ServicePlugin, self).remove_router_interface(
            context, router_id, interface_info)
