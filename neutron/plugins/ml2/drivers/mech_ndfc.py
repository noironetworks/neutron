# Copyright 2024 Cisco Systems, Inc.
# All Rights Reserved
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

import abc
import ipaddress
import json
import os
import uuid

from neutron.conf.plugins.ml2.drivers import ndfc_conf
from neutron_lib.api.definitions import portbindings
from neutron_lib.callbacks import resources
from neutron_lib import constants as const
from neutron_lib import context as n_context
from neutron_lib import rpc as n_rpc
from neutron_lib.placement import utils as place_utils
from neutron_lib.plugins import directory
from neutron_lib.plugins.ml2 import api
from neutron.plugins.ml2.drivers.ndfc import Ndfc
from neutron.plugins.ml2.drivers import cache
from oslo_config import cfg
from oslo_log import log
import oslo_messaging

from neutron._i18n import _
from neutron.db import provisioning_blocks
from neutron.plugins.ml2.common import constants as ml2_consts

LOG = log.getLogger(__name__)


class KeystoneNotificationEndpoint(object):
    filter_rule = oslo_messaging.NotificationFilter(
        event_type='^identity.project.[created|deleted]')

    def __init__(self, mechanism_driver):
        self._driver = mechanism_driver
        self._dvs_notifier = None

    def info(self, ctxt, publisher_id, event_type, payload, metadata):
        tenant_id = payload.get('resource_info')
        # malformed notification?
        if not tenant_id:
            return None

        LOG.debug("Keystone notification %(event_type)s received for "
                 "tenant %(tenant_id)s",
                 {'event_type': event_type,
                  'project name': tenant_id})
      
        if event_type == 'identity.project.created':
            self._driver.create_vrf(tenant_id)
            return oslo_messaging.NotificationResult.HANDLED

        if event_type == 'identity.project.deleted':
            #self._driver.purge_resources(tenant_id)
            self._driver.delete_vrf(tenant_id)
            return oslo_messaging.NotificationResult.HANDLED


class NDFCMechanismDriver(api.MechanismDriver):
    def __init__(self):
        super(NDFCMechanismDriver, self).__init__()

    def initialize(self):
        ndfc_conf.register_opts()
        self.keystone_notification_exchange = (cfg.CONF.ndfc.
                keystone_notification_exchange)
        self.keystone_notification_topic = (cfg.CONF.ndfc.
                                            keystone_notification_topic)
        self.keystone_notification_pool = (cfg.CONF.ndfc.
                                           keystone_notification_pool)
        self._setup_keystone_notification_listeners()
        self.ndfc_ip = (cfg.CONF.ndfc.ndfc_ip)
        self.user = (cfg.CONF.ndfc.user)
        self.pwd = (cfg.CONF.ndfc.pwd)
        self.fabric_name = (cfg.CONF.ndfc.fabric_name)
        LOG.debug("NDFC config details: ndfc_ip: %s user: %s "
                  "pwd: %s fabric_name %s",
                  self.ndfc_ip, self.user, self.pwd, self.fabric_name)
        self.ndfc = Ndfc(self.ndfc_ip, self.user, self.pwd, self.fabric_name)
        self._core_plugin = None
        self.project_details_cache = cache.ProjectDetailsCache()
        self.tenants_file = os.path.expanduser('tenants.json')
        self.load_tenants()

    @property
    def plugin(self):
        if not self._core_plugin:
            self._core_plugin = directory.get_plugin()
        return self._core_plugin

    def load_tenants(self):
        if not os.path.exists(self.tenants_file):
            with open(self.tenants_file, 'w') as file:
                json.dump({}, file)
        with open(self.tenants_file, 'r') as file:
            self.tenants = json.load(file)

    def update_tenants(self):
        with open(self.tenants_file, 'w') as file:
            json.dump(self.tenants, file)

    def get_network(self, context, network_id):
        network_db = self.plugin.get_network(context._plugin_context,
                network_id)
        return network_db

    def purge_resources(self, tenant_id):
        ctx = n_context.get_admin_context()
        networks = self.plugin.get_networks(ctx)
        LOG.debug("NDFC Network DBs %s", networks)
        for network in networks:
            if (network['project_id'] == tenant_id):
                LOG.debug("NDFC purge network: %s", network)
                self.plugin.delete_network(ctx,
                        network['id'])

    def _setup_keystone_notification_listeners(self):
        targets = [oslo_messaging.Target(
                    exchange=self.keystone_notification_exchange,
                    topic=self.keystone_notification_topic, fanout=True)]
        endpoints = [KeystoneNotificationEndpoint(self)]
        server = oslo_messaging.get_notification_listener(
            n_rpc.NOTIFICATION_TRANSPORT, targets, endpoints,
            executor='eventlet', pool=self.keystone_notification_pool)
        server.start()

    def create_vrf(self, tenant_id):
        self.project_details_cache.ensure_project(tenant_id)
        prj_details = self.project_details_cache.get_project_details(tenant_id)
        vrf_name = prj_details[0]
        self.tenants[tenant_id] = vrf_name
        self.update_tenants()

        LOG.debug("Create NDFC VRF with vrf name: %s", vrf_name)
        res = self.ndfc.create_vrf(vrf_name)
        if res:
            LOG.debug("NDFC VRF %s created successfully", vrf_name)
        else:
            LOG.debug("NDFC VRF %s failed to create", vrf_name)

    def delete_vrf(self, tenant_id):
        vrf_name = self.tenants.pop(tenant_id, None)
        if vrf_name:
            self.update_tenants()
            LOG.debug("Delete NDFC VRF with vrf name: %s", vrf_name)
            res = self.ndfc.delete_vrf(vrf_name)
            if res:
                LOG.debug("NDFC VRF %s deleted successfully", vrf_name)
            else:
                LOG.debug("NDFC VRF %s failed to delete", vrf_name)
        else:
            LOG.debug("VRF name for tenant %s not found", tenant_id)

    def create_network(self, tenant_id, network_name,
            vlan_id, physical_network):
        self.project_details_cache.ensure_project(tenant_id)
        prj_details = self.project_details_cache.get_project_details(tenant_id)
        vrf_name = prj_details[0]
        if vrf_name:
            LOG.debug("Create NDFC network with network name: %s "
                    "vrf name: %s vlan id: %s physical network: %s",
                    network_name, vrf_name, vlan_id, physical_network)
            res = self.ndfc.create_network(vrf_name, network_name,
                    vlan_id, physical_network)
            if res:
                LOG.debug("NDFC Network %s created successfully", network_name)
            else:
                LOG.debug("NDFC Network %s failed to create", network_name)
        else:
            LOG.debug("VRF name for tenant %s not found", tenant_id)

    def update_network(self, tenant_id, network_name, vlan_id,
            gateway_ip, physical_network):
        self.project_details_cache.ensure_project(tenant_id)
        prj_details = self.project_details_cache.get_project_details(tenant_id)
        vrf_name = prj_details[0]
        if vrf_name:
            LOG.debug("Update NDFC network with network name: %s "
                    "vrf name: %s vlan id: %s physical network %s "
                    "with gateway ip: %s",
                    network_name, vrf_name, vlan_id,
                    physical_network, gateway_ip)
            res = self.ndfc.update_network(vrf_name, network_name,
                    vlan_id, gateway_ip, physical_network)
            if res:
                LOG.debug("NDFC Network %s updated successfully", network_name)
            else:
                LOG.debug("NDFC Network %s failed to update", network_name)
        else:
            LOG.debug("VRF name for tenant %s not found", tenant_id)

    def delete_network(self, network_name, vlan_id, physical_network):
        LOG.debug("Delete NDFC network with network name: %s", network_name)
        res = self.ndfc.delete_network(network_name,
                vlan_id, physical_network)
        if res:
            LOG.debug("NDFC Network %s deleted successfully", network_name)
        else:
            LOG.debug("NDFC Network %s failed to delete", network_name)

    def create_network_postcommit(self, context):
        network = context.current

        network_name = network['name']
        tenant_id = network['tenant_id']
        vlan_id = network['provider:segmentation_id']
        physical_network = network['provider:physical_network']
        LOG.info("create_network_postcommit: %s", network)

        if physical_network:
            self.create_network(tenant_id, network_name,
                    vlan_id, physical_network)

    def delete_network_postcommit(self, context):
        network = context.current

        network_name = network['name']
        vlan_id = network['provider:segmentation_id']
        physical_network = network['provider:physical_network']
        LOG.debug("delete_network_postcommit: %s", network)

        if physical_network:
            self.delete_network(network_name, vlan_id, physical_network)

    def create_subnet_postcommit(self, context):
        subnet = context.current

        LOG.debug("create_subnet_postcommit: %s", subnet)

        network_id = subnet['network_id']
        network_db = self.get_network(context, network_id)
        tenant_id = network_db['project_id']
        network_name = network_db['name']
        vlan_id = network_db['provider:segmentation_id']
        physical_network = network_db['provider:physical_network']
        gateway_ip = subnet['gateway_ip']
        prefix_len = ipaddress.ip_network(subnet['cidr']).prefixlen
        gateway = str(gateway_ip) + "/" + str(prefix_len)

        if physical_network:
            self.update_network(tenant_id, network_name,
                    vlan_id, gateway, physical_network)

    def update_subnet_postcommit(self, context):
        subnet = context.current
        orig_subnet = context.original

        LOG.debug("update_subnet_postcommit: %s", subnet)

        if subnet['gateway_ip'] != orig_subnet['gateway_ip']:
            network_id = subnet['network_id']
            network_db = self.get_network(context, network_id)
            tenant_id = network_db['project_id']
            network_name = network_db['name']
            vlan_id = network_db['provider:segmentation_id']
            physical_network = network_db['provider:physical_network']
            gateway_ip = subnet['gateway_ip']
            prefix_len = ipaddress.ip_network(subnet['cidr']).prefixlen
            gateway = str(gateway_ip) + "/" + str(prefix_len)

            if physical_network:
                self.update_network(tenant_id, network_name,
                        vlan_id, gateway, physical_network)

    def delete_subnet_postcommit(self, context):
        subnet = context.current

        LOG.debug("delete_subnet_postcommit: %s", subnet)

        network_id = subnet['network_id']
        network_db = self.get_network(context, network_id)
        tenant_id = network_db['project_id']
        network_name = network_db['name']
        vlan_id = network_db['provider:segmentation_id']
        physical_network = network_db['provider:physical_network']
        gateway = ''

        if physical_network:
            self.update_network(tenant_id, network_name,
                    vlan_id, gateway, physical_network)
