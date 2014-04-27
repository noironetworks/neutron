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

import sqlalchemy as sa

from neutron.db import api as db_api
from neutron.db import model_base
from neutron.db import models_v2
from neutron.plugins.ml2 import models as models_ml2

from neutron.openstack.common import lockutils


class NetworkEPG(model_base.BASEV2):

    """EPG's created on the apic per network."""

    __tablename__ = 'cisco_ml2_apic_epgs'

    network_id = sa.Column(sa.String(255), nullable=False, primary_key=True)
    epg_id = sa.Column(sa.String(64), nullable=False)
    segmentation_id = sa.Column(sa.String(64), nullable=False)
    provider = sa.Column(sa.Boolean, default=False, nullable=False)


class PortProfile(model_base.BASEV2):

    """Port profiles created on the APIC."""

    __tablename__ = 'cisco_ml2_apic_port_profiles'

    node_id = sa.Column(sa.String(255), nullable=False, primary_key=True)
    profile_id = sa.Column(sa.String(64), nullable=False)
    hpselc_id = sa.Column(sa.String(64), nullable=False)
    module = sa.Column(sa.String(10), nullable=False)
    from_port = sa.Column(sa.Integer(), nullable=False)
    to_port = sa.Column(sa.Integer(), nullable=False)


class TenantContract(model_base.BASEV2, models_v2.HasTenant):

    """Contracts (and Filters) created on the APIC."""

    __tablename__ = 'cisco_ml2_apic_contracts'

    __table_args__ = (sa.PrimaryKeyConstraint('tenant_id'),)
    contract_id = sa.Column(sa.String(64), nullable=False)
    filter_id = sa.Column(sa.String(64), nullable=False)


class HostLink(model_base.BASEV2):

    """Connectivity of host links"""

    __tablename__ = 'cisco_ml2_apic_host_links'

    host = sa.Column(sa.String(255), nullable=False, primary_key=True)
    ifname = sa.Column(sa.String(64), nullable=False, primary_key=True)
    ifmac = sa.Column(sa.String(32), nullable=True)
    swid = sa.Column(sa.String(32), nullable=False)
    module = sa.Column(sa.String(32), nullable=False)
    port = sa.Column(sa.String(32), nullable=False)


class ApicName(model_base.BASEV2):

    """Mapping of names created on the APIC."""

    __tablename__ = 'cisco_ml2_apic_namemap'

    neutron_id = sa.Column(sa.String(36), nullable=False, primary_key=True)
    neutron_type = sa.Column(sa.String(32), nullable=False, primary_key=True)
    apic_name = sa.Column(sa.String(255), nullable=False)


class ApicKey(model_base.BASEV2):

    """Persistent config on APIC that needs to be in sync with driver."""

    __tablename__ = 'cisco_ml2_apic_keymap'

    key = sa.Column(sa.String(255), nullable=False, primary_key=True)
    value = sa.Column(sa.String(255), nullable=False)


class ApicDbModel(object):

    """DB Model to manage all APIC DB interactions."""

    def __init__(self):
        self.session = db_api.get_session()

    def get_port_profile_for_node(self, node_id):
        """Returns a port profile for a switch if found in the DB."""
        return self.session.query(PortProfile).filter_by(
            node_id=node_id).first()

    def get_profile_for_module_and_ports(self, node_id, profile_id,
                                         module, from_port, to_port):
        """Returns profile for module and ports.

        Grabs the profile row from the DB for the specified switch,
        module (linecard) and from/to port combination.
        """
        return self.session.query(PortProfile).filter_by(
            node_id=node_id,
            module=module,
            profile_id=profile_id,
            from_port=from_port,
            to_port=to_port).first()

    def get_profile_for_module(self, node_id, profile_id, module):
        """Returns the first profile for a switch module from the DB."""
        return self.session.query(PortProfile).filter_by(
            node_id=node_id,
            profile_id=profile_id,
            module=module).first()

    def get_nodes_with_port_profile(self):
        return self.session.query(PortProfile.node_id).distinct()

    def add_profile_for_module_and_ports(self, node_id, profile_id,
                                         hpselc_id, module,
                                         from_port, to_port):
        """Adds a profile for switch, module and port range."""
        row = PortProfile(node_id=node_id, profile_id=profile_id,
                          hpselc_id=hpselc_id, module=module,
                          from_port=from_port, to_port=to_port)
        self.session.add(row)
        self._flush()

    def get_provider_contract(self):
        """Returns  provider EPG from the DB if found."""
        return self.session.query(NetworkEPG).filter_by(
            provider=True).first()

    def set_provider_contract(self, epg_id):
        """Sets an EPG to be a contract provider."""
        epg = self.session.query(NetworkEPG).filter_by(
            epg_id=epg_id).first()
        if epg:
            epg.provider = True
            self.session.merge(epg)
            self._flush()

    def unset_provider_contract(self, epg_id):
        """Sets an EPG to be a contract consumer."""
        epg = self.session.query(NetworkEPG).filter_by(
            epg_id=epg_id).first()
        if epg:
            epg.provider = False
            self.session.merge(epg)
            self._flush()

    def get_an_epg(self, exception):
        """Returns an EPG from the DB that does not match the id specified."""
        return self.session.query(NetworkEPG).filter(
            NetworkEPG.epg_id != exception).first()

    def get_epg_for_network(self, network_id):
        """Returns an EPG for a give neutron network."""
        return self.session.query(NetworkEPG).filter_by(
            network_id=network_id).first()

    def write_epg_for_network(self, network_id, epg_uid, segmentation_id='1'):
        """Stores EPG details for a network.

        NOTE: Segmentation_id is just a placeholder currently, it will be
              populated with a proper segment id once segmentation mgmt is
              moved to the APIC.
        """
        epg = NetworkEPG(network_id=network_id, epg_id=epg_uid,
                         segmentation_id=segmentation_id)
        self.session.add(epg)
        self._flush()
        return epg

    def delete_epg(self, epg):
        """Deletes an EPG from the DB."""
        self.session.delete(epg)
        self._flush()

    def get_contract_for_tenant(self, tenant_id):
        """Returns the specified tenant's contract."""
        return self.session.query(TenantContract).filter_by(
            tenant_id=tenant_id).first()

    def write_contract_for_tenant(self, tenant_id, contract_id, filter_id):
        """Stores a new contract for the given tenant."""
        contract = TenantContract(tenant_id=tenant_id,
                                  contract_id=contract_id,
                                  filter_id=filter_id)
        self.session.add(contract)
        self._flush()

        return contract

    def delete_profile_for_node(self, node_id):
        """Deletes the port profile for a node."""
        profile = self.session.query(PortProfile).filter_by(
            node_id=node_id).first()
        if profile:
            self.session.delete(profile)
            self._flush()

    def add_hostlink(self, host, ifname, ifmac, swid, module, port):
        row = HostLink(host=host, ifname=ifname, ifmac=ifmac,
                       swid=swid, module=module, port=port)
        self.session.merge(row)
        self._flush()

    def get_hostlinks(self):
        return self.session.query(HostLink).all()

    def get_hostlink(self, host, ifname):
        return self.session.query(HostLink).filter_by(
            host=host, ifname=ifname).first()

    def get_hostlinks_for_host(self, host):
        return self.session.query(HostLink).filter_by(
            host=host).all()

    def delete_hostlink(self, host, ifname):
        profile = self.session.query(HostLink).filter_by(
            host=host, ifname=ifname).first()
        if profile:
            self.session.delete(profile)
            self._flush()

    def get_switches(self):
        return self.session.query(HostLink.swid).distinct()

    def get_modules_for_switch(self, swid):
        return self.session.query(
            HostLink.module).filter_by(swid=swid).distinct()

    def get_ports_for_switch_module(self, swid, module):
        return self.session.query(
            HostLink.port).filter_by(swid=swid, module=module).distinct()

    def get_switch_and_port_for_host(self, host):
        return self.session.query(
            HostLink.swid, HostLink.module, HostLink.port).filter_by(
                host=host).distinct()

    def get_tenant_network_vlan_for_host(self, host):
        pb = models_ml2.PortBinding
        po = models_v2.Port
        ns = models_ml2.NetworkSegment
        return self.session.query(
            po.tenant_id, ns.network_id, ns.segmentation_id).filter(
                po.id == pb.port_id).filter(pb.host == host).filter(
                    po.network_id == ns.network_id).distinct()

    def add_apic_name(self, neutron_id, neutron_type, apic_name):
        row = ApicName(neutron_id=neutron_id,
                       neutron_type=neutron_type,
                       apic_name=apic_name)
        self.session.add(row)
        self._flush()

    def get_apic_names(self):
        return self.session.query(ApicName).all()

    def get_apic_name(self, neutron_id, neutron_type):
        return self.session.query(ApicName.apic_name).filter_by(
            neutron_id=neutron_id, neutron_type=neutron_type).first()

    def delete_apic_name(self, neutron_id, neutron_type):
        profile = self.session.query(ApicName).filter_by(
            neutron_id=neutron_id, neutron_type=neutron_type).first()
        if profile:
            self.session.delete(profile)
            self._flush()

    def add_apic_config(self, key, value):
        row = ApicKey(key=key, value=value)
        self.session.add(row)
        self._flush()

    def get_apic_configs(self):
        return self.session.query(ApicKey).all()

    def get_apic_config(self, key):
        return self.session.query(ApicKey).filter_by(key=key).first()

    def delete_apic_config(self, key):
        profile = self.session.query(ApicKey).filter_by(key=key).first()
        if profile:
            self.session.delete(profile)
            self._flush()

    @lockutils.synchronized('apic_manager_flush')
    def clean(self):
        self.session.query(NetworkEPG).delete()
        self.session.query(PortProfile).delete()
        self.session.query(TenantContract).delete()
        self.session.query(HostLink).delete()
        self.session.query(ApicName).delete()
        self.session.query(ApicKey).delete()
        self.session.flush()

    @lockutils.synchronized('apic_manager_flush')
    def _flush(self):
        self.session.flush()
