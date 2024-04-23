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
import collections
import copy
import datetime
import json
import os
import shlex
from unittest import mock
import uuid

from keystoneauth1.identity import v3
from keystoneclient.v3 import client as ksc_client
import netaddr
from neutron_lib.api.definitions import availability_zone as az_def
from neutron_lib.api.definitions import external_net
from neutron_lib.api.definitions import extra_dhcp_opt as edo_ext
from neutron_lib.api.definitions import portbindings
from neutron_lib.api.definitions import provider_net as pnet
from neutron_lib.callbacks import events
from neutron_lib.callbacks import registry
from neutron_lib.callbacks import resources
from neutron_lib import constants as const
from neutron_lib import context
from neutron_lib import exceptions as n_exc
from neutron_lib.placement import utils as place_utils
from neutron_lib.plugins import directory
from neutron_lib.plugins import utils as p_utils
from neutron_lib.tests import tools
from neutron_lib.utils import net as n_net
from neutron.tests.unit.db import test_db_base_plugin_v2 as test_pluginV2
from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_db import exception as os_db_exc
from oslo_serialization import jsonutils
from oslo_utils import timeutils
from oslo_utils import uuidutils
from ovsdbapp.backend.ovs_idl import idlutils
from ovsdbapp.backend.ovs_idl import rowview
from webob import exc

from neutron.common import _constants as n_const
from neutron.common import config
from neutron.common.ovn import acl as ovn_acl
from neutron.common.ovn import constants as ovn_const
from neutron.common.ovn import exceptions as ovn_exceptions
from neutron.common.ovn import hash_ring_manager
from neutron.common.ovn import utils as ovn_utils
from neutron.conf.plugins.ml2.drivers import ndfc_conf
from neutron.db import db_base_plugin_v2
from neutron.db import ovn_revision_numbers_db
from neutron.db import provisioning_blocks
from neutron.db import securitygroups_db
from neutron.db import segments_db
from neutron.plugins.ml2.drivers.ovn.agent import neutron_agent
from neutron.plugins.ml2.drivers import mech_ndfc
from neutron.plugins.ml2.drivers.cache import ProjectDetailsCache
from neutron.plugins.ml2.drivers import type_geneve  # noqa
from neutron.plugins.ml2 import plugin as ml2_plugin
from neutron.services.revisions import revision_plugin
from neutron.tests.unit.extensions import test_segment
from neutron.tests.unit import fake_resources as fakes
from neutron.tests.unit.plugins.ml2 import _test_mech_agent as test_mech_agent
from neutron.tests.unit.plugins.ml2 import test_ext_portsecurity
from neutron.tests.unit.plugins.ml2 import test_plugin
from neutron.tests.unit.plugins.ml2 import test_security_group


class MechDriverSetupBase(abc.ABC):
    def setUp(self):
        config.register_common_config_options()
        super().setUp()
        mm = directory.get_plugin().mechanism_manager
        self.ndfc_mech = mm.mech_drivers['ndfc'].obj
        FakeProjectManager.reset()
        self.saved_keystone_client = ksc_client.Client
        ksc_client.Client = FakeKeystoneClient


TEST_TENANT_NAMES = {
    'another_tenant': 'AnotherTenantName',
    'bad_tenant_id': 'BadTenantIdName',
    'not_admin': 'NotAdminName',
    'some_tenant': 'SomeTenantName',
    'somebody_else': 'SomebodyElseName',
    't1': 'T1Name',
    'tenant1': 'Tenant1Name',
    'tenant_1': 'Tenant1Name',
    'tenant_2': 'Tenant2Name',
    'test-tenant': 'TestTenantName',
    'ten_1': 'prj_ten_1',
    'ten_2': 'prj_ten_2',
    test_pluginV2.TEST_TENANT_ID: test_pluginV2.TEST_TENANT_ID,
}


FABRIC_TOPOLOGY = {
  "Fabric": "SgmScale",
  "NetworkIdStart": 40000,
  "Physnets": [
          {
                  "Name": "physnet1",
                  "Switches": [
                          {
                                  "Ip": "172.28.9.245",
                                  "Serial": "FLM273901SA",
                                  "Interfaces": "Ethernet1/24,Ethernet1/25",
                                  "TorInterfaces": "test-9-26(Ethernet1/4,Ethernet1/5)"
                          },
                          {
                                  "Ip": "172.28.9.246",
                                  "Serial": "FLM273901TQ",
                                  "Interfaces": "Ethernet1/24,Ethernet1/25",
                                  "TorInterfaces": "test-9-26(Ethernet1/4,Ethernet1/5)"
                          },
                          {
                                  "Ip": "172.28.9.249",
                                  "Serial": "FLM27400AHJ",
                                  "Interfaces": "Ethernet1/50"
                          },
                          {
                                  "Ip": "172.28.9.250",
                                  "Serial": "FLM27400AJD",
                                  "Interfaces": "Ethernet1/50"
                          }
                  ]
          }
  ]
}


current_directory = os.getcwd()
tenants_file = os.path.join(current_directory, 'tenants.json')


class FakeProject(object):
    def __init__(self, id, name, description='bad\"\'descr'):
        self.id = id
        self.name = name
        self.description = description


class FakeProjectManager(object):
    _instance = None

    def __init__(self):
        self._projects = {k: FakeProject(k, v)
                          for k, v in list(TEST_TENANT_NAMES.items())}

    def list(self):
        return list(self._projects.values())

    def get(self, project_id):
        return self._projects.get(project_id)

    @classmethod
    def reset(cls):
        cls._instance = None

    @classmethod
    def get_instance(cls):
        if not cls._instance:
            cls._instance = FakeProjectManager()
        return cls._instance

    @classmethod
    def set(cls, project_id, name, description=''):
        cls.get_instance()._projects[project_id] = FakeProject(
            project_id, name, description)


class FakeKeystoneClient(object):
    def __init__(self, **kwargs):
        self.projects = FakeProjectManager.get_instance()


class TestNDFCMechanismDriverBase(MechDriverSetupBase,
                                 test_plugin.Ml2PluginV2TestCase):

    _mechanism_drivers = ['ndfc']


class TestNDFCMechanismDriver(TestNDFCMechanismDriverBase):
    def setUp(self):
        ndfc_conf.register_opts()
        ndfc_conf.cfg.CONF.set_override('fabric_name',
                'SgmScale', group='ndfc')
        ndfc_conf.cfg.CONF.set_override('ndfc_ip', '172.28.9.19',
                group='ndfc')
        self.mock_keystone_auth = mock.patch.object(
            ProjectDetailsCache, 'get_auth', return_value=None).start()
        self.mock_get_network = mock.patch.object(mech_ndfc.NDFCMechanismDriver,
                'get_network', return_value=None).start()
        self.mock_network = {'name': 'fake-network-name',
                             'project_id': 'fake-project-id',
                             'provider:segmentation_id': 'fake-segmentation-id',
                             'provider:physical_network': 'fake-physical_-network'}
        super().setUp()

    def _create_fake_network_context(self,
                                     network_type,
                                     physical_network=None,
                                     segmentation_id=None):
        network_attrs = {'provider:network_type': network_type,
                         'provider:physical_network': physical_network,
                         'provider:segmentation_id': segmentation_id}
        segment_attrs = {'network_type': network_type,
                         'physical_network': physical_network,
                         'segmentation_id': segmentation_id}
        fake_network = \
            fakes.FakeNetwork.create_one_network(attrs=network_attrs).info()
        fake_segments = \
            [fakes.FakeSegment.create_one_segment(attrs=segment_attrs).info()]
        return fakes.FakeNetworkContext(fake_network, fake_segments)

    def _create_fake_subnet_context(self,
                                    network_id,
                                    cidr):
        subnet_attrs = {'network_id': network_id,
                        'gateway_ip': cidr}
        fake_subnet = \
            fakes.FakeSubnet.create_one_subnet(attrs=subnet_attrs).info()
        return fakes.FakeSubnetContext(fake_subnet)

    @mock.patch.object(mech_ndfc.NDFCMechanismDriver, 'get_network')
    def test_network_postcommit(self, *args):
        # Test create and delete network postcommit methods
        self.mock_get_network = self.mock_network
        fake_network_context = self._create_fake_network_context('local')
        self.ndfc_mech.create_network_postcommit(fake_network_context)
        self.ndfc_mech.delete_network_postcommit(fake_network_context)

        # Test create and delete network with physical network
        fake_network_context = self._create_fake_network_context('local',
                'physnet1')
        self.ndfc_mech.create_network_postcommit(fake_network_context)
        self.ndfc_mech.delete_network_postcommit(fake_network_context)

        # Test create and delete subnet postcommit methods
        fake_subnet_context = self._create_fake_subnet_context(
                'fake-network-id', '10.10.10.0/824')
        self.ndfc_mech.create_subnet_postcommit(fake_subnet_context)
        self.ndfc_mech.delete_subnet_postcommit(fake_subnet_context)

    def test_keystone_notification_endpoint(self):
        payload = {}

        payload['resource_info'] = 'test-tenant'
        keystone_ep = mech_ndfc.KeystoneNotificationEndpoint(self.ndfc_mech)

        # Test with project.created event.
        FakeProjectManager.set('test-tenant',
            'tenant1', 'bad\"\'descr')
        keystone_ep.info(None, None, 'identity.project.created', payload, None)

        # Test with project.deleted event.
        payload['resource_info'] = 'test-tenant'
        keystone_ep.info(None, None, 'identity.project.deleted', payload, None)
