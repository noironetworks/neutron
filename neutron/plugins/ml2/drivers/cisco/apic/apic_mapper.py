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
# @author: Mandeep Dhami (dhami@noironetworks.com), Cisco Systems Inc.

from keystoneclient.v2_0 import client as keyclient
from oslo.config import cfg

from neutron.openstack.common import excutils
from neutron.openstack.common import log


LOG = log.getLogger(__name__)


NAMING_STRATEGY_UUID = 'use_uuid'
NAMING_STRATEGY_NAMES = 'use_name'


class APICNameMapper(object):
    def __init__(self, apic_manager, strategy=NAMING_STRATEGY_UUID):
        self.apic_manager = apic_manager
        self.strategy = strategy
        self.keystone = None
        self.tenants = {}

    def tenant(self, context, tenant_id):
        tenant_name = None
        try:
            if tenant_id in self.tenants:
                tenant_name = self.tenants.get(tenant_id)
            else:
                if self.keystone is None:
                    keystone_conf = cfg.CONF.keystone_authtoken
                    auth_url = ('%s://%s:%s/v2.0/' % (
                        keystone_conf.auth_protocol,
                        keystone_conf.auth_host,
                        keystone_conf.auth_port))
                    username = keystone_conf.admin_user
                    password = keystone_conf.admin_password
                    project_name = keystone_conf.admin_tenant_name
                    self.keystone = keyclient.Client(
                        auth_url=auth_url,
                        username=username,
                        password=password,
                        tenant_name=project_name)
                for tenant in self.keystone.tenants.list():
                    self.tenants[tenant.id] = tenant.name
                    if tenant.id == tenant_id:
                        tenant_name = tenant.name
        except Exception:
            with excutils.save_and_reraise_exception() as ctxt:
                ctxt.reraise = False
                LOG.exception(_("Exception in looking up tenant name %r"),
                              tenant_id)

        apic_tenant_id = tenant_id
        if tenant_name:
            if self.strategy == NAMING_STRATEGY_NAMES:
                apic_tenant_id = tenant_name
            elif self.strategy == NAMING_STRATEGY_UUID:
                apic_tenant_id = tenant_name + "-" + apic_tenant_id
        return apic_tenant_id

    def network(self, context, network_id):
        network_name = None
        try:
            network = context._plugin.get_network(
                context._plugin_context, network_id)
            network_name = network['name']
        except Exception:
            with excutils.save_and_reraise_exception() as ctxt:
                ctxt.reraise = False
                LOG.exception(_("Exception in looking up network name %r"),
                              network_id)

        apic_network_id = network_id
        if network_name:
            if self.strategy == NAMING_STRATEGY_NAMES:
                apic_network_id = network_name
            elif self.strategy == NAMING_STRATEGY_UUID:
                apic_network_id = \
                    network_name + "-" + apic_network_id
        return apic_network_id

    def subnet(self, context, subnet_id):
        subnet_name = None
        try:
            subnet = context._plugin.get_subnet(
                context._plugin_context, subnet_id)
            subnet_name = subnet['name']
        except Exception:
            with excutils.save_and_reraise_exception() as ctxt:
                ctxt.reraise = False
                LOG.exception(_("Exception in looking up subnet name %r"),
                              subnet_id)

        apic_subnet_id = subnet_id
        if subnet_name:
            if self.strategy == NAMING_STRATEGY_NAMES:
                apic_subnet_id = subnet_name
            elif self.strategy == NAMING_STRATEGY_UUID:
                apic_subnet_id = \
                    subnet_name + "-" + apic_subnet_id
        return apic_subnet_id

    def port(self, context, port_id):
        port_name = None
        try:
            port = context._plugin.get_port(
                context._plugin_context, port_id)
            port_name = port['name']
        except Exception:
            with excutils.save_and_reraise_exception() as ctxt:
                ctxt.reraise = False
                LOG.exception(_("Exception in looking up port name name %r"),
                              port_id)

        apic_port_id = port_id
        if port_name:
            if self.strategy == NAMING_STRATEGY_NAMES:
                apic_port_id = port_name
            elif self.strategy == NAMING_STRATEGY_UUID:
                apic_port_id = \
                    port_name + "-" + apic_port_id
        return apic_port_id

    def router(self, context, router_id):
        router_name = None
        try:
            router = context._plugin.get_router(
                context._plugin_context, router_id)
            router_name = router['name']
        except Exception:
            with excutils.save_and_reraise_exception() as ctxt:
                ctxt.reraise = False
                LOG.exception(
                    _("Exception in looking up router name name %r"),
                    router_id)

        apic_router_id = router_id
        if router_name:
            if self.strategy == NAMING_STRATEGY_NAMES:
                apic_router_id = router_name
            elif self.strategy == NAMING_STRATEGY_UUID:
                apic_router_id = \
                    router_name + "-" + apic_router_id
        return apic_router_id
