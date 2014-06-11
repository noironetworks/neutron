# Copyright (c) 2014 Cisco Systems
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
# @author: Arvind Somya (asomya@cisco.com), Cisco Systems

import mock

from neutron.services.l3_router import l3_apic
from neutron.tests import base
from neutron.tests.unit.ml2.drivers.cisco.apic import (
    test_cisco_apic_common as mocked)

TENANT = 'tenant1'
TENANT_CONTRACT = 'abcd'
ROUTER = 'router1'
SUBNET = 'subnet1'
NETWORK = 'network1'
PORT = 'port1'
NETWORK_NAME = 'one_network'
NETWORK_EPG = 'one_network-epg'
TEST_SEGMENT1 = 'test-segment1'
SUBNET_GATEWAY = '10.3.2.1'
SUBNET_CIDR = '10.3.1.0/24'
SUBNET_NETMASK = '24'


class FakeContext(object):
    def __init__(self):
        self.tenant_id = None


class FakeContract(object):
    def __init__(self):
        self.contract_id = '123'


class FakeEpg(object):
    def __init__(self):
        self.epg_id = 'abcd_epg'


class FakePort(object):
    def __init__(self):
        self.id = 'Fake_port_id'
        self.network_id = NETWORK
        self.subnet_id = SUBNET


class TestCiscoApicL3Plugin(base.BaseTestCase,
                            mocked.ControllerMixin,
                            mocked.ConfigMixin,
                            mocked.DbModelMixin):
    def setUp(self):
        super(TestCiscoApicL3Plugin, self).setUp()
        mocked.ConfigMixin.set_up_mocks(self)

        mock.patch('neutron.plugins.ml2.drivers.cisco.apic.apic_client.'
                   'RestClient').start()
        mock.patch('neutron.plugins.ml2.drivers.cisco.apic.apic_model.'
                   'ApicDbModel').start()

        self.plugin = l3_apic.ApicL3ServicePlugin()
        self.context = FakeContext()
        self.context.tenant_id = TENANT
        self.interface_info = {'subnet_id': SUBNET, 'port_id': PORT}
        self.subnet = {'network_id': NETWORK, 'tenant_id': TENANT}
        self.port = {'tenant_id': TENANT,
                     'fixed_ips': [{'subnet_id': SUBNET}]}
        self.plugin.name_mapper = mock.Mock()
        self.plugin.name_mapper.tenant.return_value = mocked.APIC_TENANT
        self.plugin.name_mapper.network.return_value = mocked.APIC_NETWORK
        self.plugin.name_mapper.subnet.return_value = mocked.APIC_SUBNET
        self.plugin.name_mapper.port.return_value = mocked.APIC_PORT

        self.contract = FakeContract()
        self.plugin.manager.get_router_contract = mock.Mock()
        ctmk = mock.PropertyMock(return_value=self.contract.contract_id)
        type(self.plugin.manager.get_router_contract).contract_id = ctmk
        self.epg = FakeEpg()
        self.plugin.manager.ensure_epg_created_for_network = mock.Mock()
        epmk = mock.PropertyMock(return_value=self.epg.epg_id)
        type(self.plugin.manager.ensure_epg_created_for_network).epg_id = epmk

        self.plugin.manager.db.get_provider_contract = mock.Mock(
            return_value=None)
        self.plugin.manager.set_contract_for_epg = mock.Mock(
            return_value=True)
        self.plugin.manager.delete_contract_for_epg = mock.Mock()

        self.plugin.get_subnet = mock.Mock(return_value=self.subnet)
        self.plugin.get_network = mock.Mock(return_value=self.interface_info)
        self.plugin.get_port = mock.Mock(return_value=self.port)
        mock.patch('neutron.db.l3_gwmode_db.L3_NAT_db_mixin.'
                   '_core_plugin').start()
        mock.patch('neutron.db.l3_gwmode_db.L3_NAT_db_mixin.'
                   'add_router_interface').start()
        mock.patch('neutron.db.l3_gwmode_db.L3_NAT_db_mixin.'
                   'remove_router_interface').start()
        mock.patch('neutron.openstack.common.excutils.'
                   'save_and_reraise_exception').start()

    def test_add_router_interface(self):
        mgr = self.plugin.manager
        self.plugin.add_router_interface(self.context, ROUTER,
                                         self.interface_info)
        mgr.get_router_contract.assert_called_once_with(mocked.APIC_TENANT)
        mgr.ensure_epg_created_for_network.assertEqual(TENANT_CONTRACT)
        mgr.ensure_epg_created_for_network.assert_called_once_with(
            mocked.APIC_TENANT, mocked.APIC_NETWORK)
        mgr.ensure_epg_created_for_network.assertEqual(NETWORK_EPG)
        mgr.db.get_provider_contract.assert_called_once()
        mgr.db.get_provider_contract.assertEqual(None)
        mgr.set_contract_for_epg.assert_called_once()

    def test_remove_router_interface(self):
        mgr = self.plugin.manager
        self.plugin.remove_router_interface(self.context, ROUTER,
                                            self.interface_info)
        mgr.get_router_contract.assert_called_once_with(mocked.APIC_TENANT)
        mgr.ensure_epg_created_for_network.assert_called_once_with(
            mocked.APIC_TENANT, mocked.APIC_NETWORK)
        mgr.ensure_epg_created_for_network.assertEqual(NETWORK_EPG)
        mgr.delete_contract_for_epg.assert_called_once()

    def test_add_router_interface_fail_contract_delete(self):
        mgr = self.plugin.manager
        with mock.patch('neutron.db.l3_gwmode_db.L3_NAT_db_mixin.'
                        'add_router_interface',
                        side_effect=KeyError()):
            self.plugin.add_router_interface(self.context, ROUTER,
                                             self.interface_info)
            mgr.delete_contract_for_epg.assert_called_once()

    def test_delete_router_interface_fail_contract_create(self):
        mgr = self.plugin.manager
        with mock.patch('neutron.db.l3_gwmode_db.L3_NAT_db_mixin.'
                        'remove_router_interface',
                        side_effect=KeyError()):
            self.plugin.remove_router_interface(self.context, ROUTER,
                                                self.interface_info)
            mgr.set_contract_for_epg.assert_called_once()
