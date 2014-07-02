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

import itertools
import uuid

from neutron.openstack.common import excutils
from neutron.openstack.common import log
from neutron.plugins.ml2.drivers.cisco.apic import apic_client
from neutron.plugins.ml2.drivers.cisco.apic import apic_mapper
from neutron.plugins.ml2.drivers.cisco.apic import apic_model
from neutron.plugins.ml2.drivers.cisco.apic import exceptions as cexc


LOG = log.getLogger(__name__)


CONTEXT_ENFORCED = '1'
CONTEXT_UNENFORCED = '2'
CONTEXT_DEFAULT = 'default'
CONTEXT_SHARED = 'shared'
DN_KEY = 'dn'
PORT_DN_PATH = 'topology/pod-1/paths-%s/pathep-[eth%s/%s]'
NODE_DN_PATH = 'topology/pod-1/node-%s'
POD_POLICY_GROUP_DN_PATH = 'uni/fabric/funcprof/podpgrp-%s'
CP_PATH_DN = 'uni/tn-%s/brc-%s'
SCOPE_GLOBAL = 'global'
SCOPE_TENANT = 'tenant'
TENANT_COMMON = 'common'
NAMING_STRATEGY_UUID = 'use_uuid'
NAMING_STRATEGY_NAMES = 'use_name'

# L3 External constants
EXT_NODE = 'os-lnode'
EXT_INTERFACE = 'os-linterface'
EXT_EPG = 'os-external_epg'

# Contract constants
CP_SUBJ = 'os-subject'
CP_FILTER = 'os-filter'
CP_ENTRY = 'os-entry'
CP_INTERFACE = 'os-interface'


class APICManager(object):
    """Class to manage APIC translations and workflow.

    This class manages translation from Neutron objects to APIC
    managed objects and contains workflows to implement these
    translations.
    """
    def __init__(self, apic_config, network_config):
        self.db = apic_model.ApicDbModel()
        self.apic_config = apic_config
        self.vlan_ranges = network_config['vlan_ranges']
        self.switch_dict = network_config['switch_dict']
        self.ext_net_dict = network_config['external_network_dict']

        # Connect to the the APIC
        self.apic = apic_client.RestClient(
            apic_config.apic_hosts,
            apic_config.apic_username,
            apic_config.apic_password
        )

        self.port_profiles = {}
        self.phys_domain = None
        self.vlan_ns = None
        self.node_profiles = {}
        self.app_profile_name = apic_config.apic_app_profile_name
        self.entity_profile = None
        self.function_profile = None
        self.clear_node_profiles = apic_config.apic_clear_node_profiles

    def _create_if_not_exist(self, mo, *params, **attributes):
        if not mo.get(*params):
            mo.create(*params, **attributes)

    def ensure_infra_created_on_apic(self):
        """Ensure the infrastructure is setup.

        First create all common entities, and then
        Loop over the switch dictionary from the config and
        setup profiles for switches, modules and ports
        """

        # Create VLAN namespace
        vlan_ns_name = self.apic_config.apic_vlan_ns_name
        vlan_range = self.vlan_ranges[0]
        (vlan_min, vlan_max) = vlan_range.split(':')[-2:]
        vlan_ns = self.ensure_vlan_ns_created_on_apic(
            vlan_ns_name, vlan_min, vlan_max)

        # Create domain
        phys_name = self.apic_config.apic_domain_name
        self.ensure_phys_domain_created_on_apic(phys_name, vlan_ns)

        # Create entity profile
        ent_name = self.apic_config.apic_entity_profile
        self.ensure_entity_profile_created_on_apic(ent_name)

        # Create function profile
        func_name = self.apic_config.apic_function_profile
        self.ensure_function_profile_created_on_apic(func_name)

        # first make sure that all existing switches in DB are in apic
        for switch in self.db.get_switches():
            self.ensure_infra_created_for_switch(switch[0])

        # now create add any new switches in config to apic and DB
        for switch in self.switch_dict:
            for module_port in self.switch_dict[switch]:
                module, port = module_port.split('/')
                hosts = self.switch_dict[switch][module_port]
                for host in hosts:
                    self.add_hostlink(
                        host, 'static', None, switch, module, port)

    def ensure_vlan_ns_created_on_apic(self, name, vlan_min, vlan_max):
        """Creates a static VLAN namespace with the given vlan range."""
        ns_args = name, 'static'
        if self.clear_node_profiles:
            self.apic.fvnsVlanInstP.delete(name, 'dynamic')
            self.apic.fvnsVlanInstP.delete(*ns_args)
        self.vlan_ns = self.apic.fvnsVlanInstP.get(*ns_args)
        if not self.vlan_ns:
            try:
                self.apic.fvnsVlanInstP.create(*ns_args)
                vlan_min = 'vlan-' + vlan_min
                vlan_max = 'vlan-' + vlan_max
                ns_blk_args = name, 'static', vlan_min, vlan_max
                vlan_encap = self.apic.fvnsEncapBlk__vlan.get(*ns_blk_args)
                if not vlan_encap:
                    ns_kw_args = {
                        'name': 'encap',
                        'from': vlan_min,
                        'to': vlan_max
                    }
                    self.apic.fvnsEncapBlk__vlan.create(*ns_blk_args,
                                                        **ns_kw_args)
                self.vlan_ns = self.apic.fvnsVlanInstP.get(*ns_args)
                return self.vlan_ns
            except (cexc.ApicResponseNotOk, KeyError):
                with excutils.save_and_reraise_exception():
                    # Delete the vlan namespace
                    self.apic.fvnsVlanInstP.delete(*ns_args)

    def ensure_phys_domain_created_on_apic(self, phys_name,
                                           vlan_ns=None, vxlan_ns=None):
        """Create physical domain.

        Creates the physical domain on the APIC and adds a VLAN or VXLAN
        namespace to that physical domain.
        """
        if self.clear_node_profiles:
            self.apic.physDomP.delete(phys_name)
        self.phys_domain = self.apic.physDomP.get(phys_name)
        if not self.phys_domain:
            try:
                self.apic.physDomP.create(phys_name)
                if vlan_ns:
                    vlan_ns_dn = vlan_ns[DN_KEY]
                    self.apic.infraRsVlanNs.create(phys_name,
                                                   tDn=vlan_ns_dn)
                self.phys_domain = self.apic.physDomP.get(phys_name)
            except (cexc.ApicResponseNotOk, KeyError):
                with excutils.save_and_reraise_exception():
                    # Delete the physical domain
                    self.apic.physDomP.delete(phys_name)

    def ensure_entity_profile_created_on_apic(self, name):
        """Create the infrastructure entity profile."""
        if self.clear_node_profiles:
            self.apic.infraAttEntityP.delete(name)
        self.entity_profile = self.apic.infraAttEntityP.get(name)
        if not self.entity_profile:
            try:
                phys_dn = self.phys_domain[DN_KEY]
                self.apic.infraAttEntityP.create(name)
                # Attach phys domain to entity profile
                self.apic.infraRsDomP.create(name, phys_dn)
                self.entity_profile = self.apic.infraAttEntityP.get(name)
            except (cexc.ApicResponseNotOk, KeyError):
                with excutils.save_and_reraise_exception():
                    # Delete the created entity profile
                    self.apic.infraAttEntityP.delete(name)

    def ensure_function_profile_created_on_apic(self, name):
        """Create the infrastructure function profile."""
        if self.clear_node_profiles:
            self.apic.infraAccPortGrp.delete(name)
        self.function_profile = self.apic.infraAccPortGrp.get(name)
        if not self.function_profile:
            try:
                self.apic.infraAccPortGrp.create(name)
                # Attach entity profile to function profile
                entp_dn = self.entity_profile[DN_KEY]
                self.apic.infraRsAttEntP.create(name, tDn=entp_dn)
                self.function_profile = self.apic.infraAccPortGrp.get(name)
            except (cexc.ApicResponseNotOk, KeyError):
                with excutils.save_and_reraise_exception():
                    # Delete the created function profile
                    self.apic.infraAccPortGrp.delete(name)

    def ensure_infra_created_for_switch(self, switch):
            # Create a node and profile for this switch
            if not self.function_profile:
                self.function_profile = self.apic.infraAccPortGrp.get(
                    self.apic_config.apic_function_profile)
            self.ensure_node_profile_created_for_switch(switch)
            ppname = self.ensure_port_profile_created_for_switch(switch)

            # Setup each module and port range
            for module in self.db.get_modules_for_switch(switch):
                module = module[0]
                profile = self.db.get_profile_for_module(
                    switch, ppname, module)
                if not profile:
                    hname = uuid.uuid4()
                    try:
                        self.apic.infraHPortS.create(ppname, hname, 'range')
                        fpdn = self.function_profile[DN_KEY]
                        self.apic.infraRsAccBaseGrp.create(
                            ppname, hname, 'range', tDn=fpdn)
                    except (cexc.ApicResponseNotOk, KeyError):
                        with excutils.save_and_reraise_exception():
                            self.apic.infraHPortS.delete(
                                ppname, hname, 'range')
                else:
                    hname = profile.hpselc_id

                # Add this module and ports to the profile
                ports = [p[0] for p in
                         self.db.get_ports_for_switch_module(switch, module)]
                ports.sort()
                #ranges = APICManager.group_by_ranges(ports)
                ranges = zip(ports, ports)
                for prange in ranges:
                    if not self.db.get_profile_for_module_and_ports(
                            switch, ppname, module, prange[0], prange[-1]):
                        # Create port block for this port range
                        pbname = uuid.uuid4()
                        self.apic.infraPortBlk.create(ppname, hname, 'range',
                                                      pbname, fromCard=module,
                                                      toCard=module,
                                                      fromPort=str(prange[0]),
                                                      toPort=str(prange[-1]))
                        # Add DB row
                        self.db.add_profile_for_module_and_ports(
                            switch, ppname, hname, module,
                            prange[0], prange[-1])

    def ensure_node_profile_created_for_switch(self, switch_id):
        """Creates a switch node profile.

        Create a node profile for a switch and add a switch
        to the leaf node selector
        """
        if self.clear_node_profiles:
            self.apic.infraNodeP.delete(switch_id)
            self.db.delete_profile_for_node(switch_id)
        sobj = self.apic.infraNodeP.get(switch_id)
        if not sobj:
            try:
                # Create Node profile
                self.apic.infraNodeP.create(switch_id)
                # Create leaf selector
                lswitch_id = uuid.uuid4()
                self.apic.infraLeafS.create(switch_id, lswitch_id, 'range')
                # Add leaf nodes to the selector
                name = uuid.uuid4()
                self.apic.infraNodeBlk.create(switch_id, lswitch_id, 'range',
                                              name, from_=switch_id,
                                              to_=switch_id)
                sobj = self.apic.infraNodeP.get(switch_id)
            except (cexc.ApicResponseNotOk, KeyError):
                with excutils.save_and_reraise_exception():
                    # Remove the node profile
                    self.apic.infraNodeP.delete(switch_id)

        self.node_profiles[switch_id] = {
            'object': sobj
        }

    def ensure_port_profile_created_for_switch(self, switch):
        """Check and create infra port profiles for a node."""
        sprofile = self.db.get_port_profile_for_node(switch)
        if not sprofile:
            # Generate uuid for port profile name
            ppname = uuid.uuid4()
            try:
                # Create port profile for this switch
                pprofile = self.ensure_port_profile_on_apic(ppname)
                # Add port profile to node profile
                ppdn = pprofile[DN_KEY]
                self.apic.infraRsAccPortP.create(switch, ppdn)
            except (cexc.ApicResponseNotOk, KeyError):
                with excutils.save_and_reraise_exception():
                    # Delete port profile
                    self.apic.infraAccPortP.delete(ppname)
        else:
            ppname = sprofile.profile_id

        return ppname

    def ensure_port_profile_on_apic(self, name):
        """Create a port profile."""
        try:
            if not self.apic.infraAccPortP.get(name):
                self.apic.infraAccPortP.create(name)
            return self.apic.infraAccPortP.get(name)
        except (cexc.ApicResponseNotOk, KeyError):
            with excutils.save_and_reraise_exception():
                self.apic.infraAccPortP.delete(name)

    def ensure_bgp_pod_policy_created_on_apic(self, bgp_pol_name='default',
                                              asn='1', pp_group_name='default',
                                              p_selector_name='default'):
        """Set the route reflector for the fabric if missing."""
        self._create_if_not_exist(self.apic.bgpInstPol, bgp_pol_name)
        if not self.apic.bgpRRP.get_subtree(bgp_pol_name):
            for node in self.apic.fabricNode.list_all(role='spine'):
                self._create_if_not_exist(self.apic.bgpRRNodePEp,
                                          bgp_pol_name,
                                          node['id'])

        self._create_if_not_exist(self.apic.bgpAsP, bgp_pol_name, asn=asn)

        self._create_if_not_exist(self.apic.fabricPodPGrp, pp_group_name)
        reference = self.apic.fabricRsPodPGrpBGPRRP.get(pp_group_name)
        if not reference['tnBgpInstPolName']:
            self.apic.fabricRsPodPGrpBGPRRP.update(
                pp_group_name, tnBgpInstPolName=bgp_pol_name)

        self._create_if_not_exist(self.apic.fabricPodS__ALL, p_selector_name,
                                  type='ALL')
        self._create_if_not_exist(self.apic.fabricRsPodPGrp, p_selector_name,
                                  tDn=POD_POLICY_GROUP_DN_PATH % pp_group_name)

    @staticmethod
    def group_by_ranges(i):
        """Group a list of numbers into tuples of contiguous ranges."""
        for a, b in itertools.groupby(enumerate(sorted(i)),
                                      lambda (x, y): y - x):
            b = list(b)
            yield b[0][1], b[-1][1]

    def ensure_tenant_created_on_apic(self, tenant_id):
        """Make sure a tenant exists on the APIC."""
        if not self.apic.fvTenant.get(tenant_id):
            self.apic.fvTenant.create(tenant_id)

    def ensure_bd_created_on_apic(self, tenant_id, bd_id):
        """Creates a Bridge Domain on the APIC."""
        if not self.apic.fvBD.get(tenant_id, bd_id):
            try:
                self.apic.fvBD.create(tenant_id, bd_id)
                # Add default context to the BD
                self.ensure_context_enforced(tenant_id, CONTEXT_DEFAULT)
                self.apic.fvRsCtx.create(tenant_id, bd_id,
                                         tnFvCtxName=CONTEXT_DEFAULT)
            except (cexc.ApicResponseNotOk, KeyError):
                with excutils.save_and_reraise_exception():
                    # Delete the bridge domain
                    self.apic.fvBD.delete(tenant_id, bd_id)

    def delete_bd_on_apic(self, tenant_id, bd_id):
        """Deletes a Bridge Domain from the APIC."""
        self.apic.fvBD.delete(tenant_id, bd_id)

    def ensure_subnet_created_on_apic(self, tenant_id, bd_id, gw_ip):
        """Creates a subnet on the APIC

        The gateway ip (gw_ip) should be specified as a CIDR
        e.g. 10.0.0.1/24
        """
        if not self.apic.fvSubnet.get(tenant_id, bd_id, gw_ip):
            self.apic.fvSubnet.create(tenant_id, bd_id, gw_ip)

    def ensure_filter_created_on_apic(self, tenant_id, filter_id):
        """Create a filter on the APIC."""
        if not self.apic.vzFilter.get(tenant_id, filter_id):
            self.apic.vzFilter.create(tenant_id, filter_id)

    def ensure_epg_created_for_network(self, tenant_id, network_id):
        """Creates an End Point Group on the APIC.

        Create a new EPG on the APIC for the network spcified. This information
        is also tracked in the local DB and associate the bridge domain for the
        network with the EPG created.
        """
        # Check if an EPG is already present for this network
        epg = self.db.get_epg_for_network(network_id)
        if epg:
            return epg

        # Create a new EPG on the APIC
        epg_uid = network_id
        try:
            self.apic.fvAEPg.create(tenant_id, self.app_profile_name, epg_uid)

            # Add bd to EPG
            bd = self.apic.fvBD.get(tenant_id, network_id)
            bd_name = bd['name']

            # create fvRsBd
            self.apic.fvRsBd.create(tenant_id, self.app_profile_name, epg_uid,
                                    tnFvBDName=bd_name)

            # Add EPG to physical domain
            phys_dn = self.phys_domain[DN_KEY]
            self.apic.fvRsDomAtt.create(
                tenant_id, self.app_profile_name, epg_uid, phys_dn)
        except (cexc.ApicResponseNotOk, KeyError):
            with excutils.save_and_reraise_exception():
                # Delete the EPG
                self.apic.fvAEPg.delete(
                    tenant_id, self.app_profile_name, epg_uid)

        # Stick it in the DB
        epg = self.db.write_epg_for_network(network_id, epg_uid)

        return epg

    def delete_epg_for_network(self, tenant_id, network_id):
        """Deletes the EPG from the APIC and removes it from the DB."""
        # Check if an EPG is already present for this network
        epg = self.db.get_epg_for_network(network_id)
        if not epg:
            return False

        # Delete this epg
        self.apic.fvAEPg.delete(tenant_id, self.app_profile_name, epg.epg_id)
        # Remove DB row
        self.db.delete_epg(epg)

    def create_tenant_filter(self, tenant_id, fuuid, euuid=CP_ENTRY):
        """Creates a tenant filter and a generic entry under it."""
        try:
            # Create a new tenant filter
            self._create_if_not_exist(self.apic.vzFilter, tenant_id, fuuid)
            # Create a new entry
            self._create_if_not_exist(self.apic.vzEntry, tenant_id, fuuid,
                                      euuid)
        except (cexc.ApicResponseNotOk, KeyError):
            with excutils.save_and_reraise_exception():
                self.apic.vzFilter.delete(tenant_id, fuuid)

    def get_prov_contract_for_epg(self, tenant_id, epg_id, contract_id):
        return self.apic.fvRsProv.get(
            tenant_id, self.app_profile_name, epg_id, contract_id)

    def get_cons_contract_for_epg(self, tenant_id, epg_id, contract_id):
        return self.apic.fvRsCons.get(
            tenant_id, self.app_profile_name, epg_id, contract_id)

    def set_contract_for_epg(self, tenant_id, epg_id,
                             contract_id, provider=False):
        """Set the contract for an EPG.

        By default EPGs are consumers of a contract.
        Set provider flag to True for the EPG to act as a provider.
        """
        if provider:
            try:
                self.apic.fvRsProv.create(
                    tenant_id, self.app_profile_name, epg_id, contract_id)
                self.db.set_provider_contract(epg_id)
            except (cexc.ApicResponseNotOk, KeyError):
                with excutils.save_and_reraise_exception():
                    self.apic.fvRsProv.delete(
                        tenant_id, self.app_profile_name, epg_id, contract_id)
        else:
            self.apic.fvRsCons.create(
                tenant_id, self.app_profile_name, epg_id, contract_id)

    def delete_contract_for_epg(self, tenant_id, epg_id,
                                contract_id, provider=False):
        """Delete the contract for an End Point Group.

        Check if the EPG was a provider and attempt to grab another contract
        consumer from the DB and set that as the new contract provider.
        """
        if provider:
            self.apic.fvRsProv.delete(
                tenant_id, self.app_profile_name, epg_id, contract_id)
            self.db.unset_provider_contract(epg_id)
        else:
            self.apic.fvRsCons.delete(
                tenant_id, self.app_profile_name, epg_id, contract_id)

    def get_router_contract(self, router_id, owner=TENANT_COMMON,
                            suuid=CP_SUBJ, iuuid=CP_INTERFACE,
                            fuuid=CP_FILTER):
        """Creates a tenant contract for router

        Create a tenant contract if one doesn't exist. Also create a
        subject, filter and entry and set the filters to allow all
        protocol traffic on all ports
        """
        contract = self.db.get_contract_for_router(router_id)
        cuuid = uuid.uuid4() if not contract else contract.contract_id
        try:
            # Create contract
            scope = SCOPE_GLOBAL if owner == TENANT_COMMON else SCOPE_TENANT
            self._create_if_not_exist(self.apic.vzBrCP, owner, cuuid,
                                      scope=scope)

            # Create subject
            self._create_if_not_exist(self.apic.vzSubj, owner, cuuid,
                                      suuid)
            # Create filter and entry
            self.create_tenant_filter(owner, fuuid)
            self._create_if_not_exist(self.apic.vzRsSubjFiltAtt, owner,
                                      cuuid, suuid, fuuid)
            # Create contract interface
            self._create_if_not_exist(self.apic.vzCPIf, owner, iuuid)
            self.apic.vzRsIf.create(owner, iuuid,
                                    tDn=CP_PATH_DN % (owner, cuuid))
            # Store contract in DB
            if not contract:
                contract = self.db.write_contract_for_router(owner, router_id,
                                                             cuuid, fuuid)
        except (cexc.ApicResponseNotOk, KeyError):
            with excutils.save_and_reraise_exception():
                # Delete tenant contract
                self.apic.vzBrCP.delete(owner, cuuid)

        return contract

    def delete_router_contract(self, router_id):
        """Delete the contract related to a given Router."""
        contract = self.db.get_contract_for_router(router_id)
        if contract:
            self.apic.vzBrCP.delete(contract.tenant_id, contract.contract_id)
            self.db.delete_contract_for_router(router_id)

    def ensure_context_unenforced(self, tenant_id, ctx_id):
        """Set the specified tenant's context to unenforced."""
        ctx = self.apic.fvCtx.get(tenant_id, ctx_id)
        if not ctx:
            self.apic.fvCtx.create(
                tenant_id, ctx_id, pcEnfPref=CONTEXT_UNENFORCED)
        elif ctx['pcEnfPref'] != CONTEXT_UNENFORCED:
            self.apic.fvCtx.update(
                tenant_id, ctx_id, pcEnfPref=CONTEXT_UNENFORCED)

    def ensure_context_enforced(self, tenant_id=TENANT_COMMON,
                                ctx_id=CONTEXT_SHARED):
        """Set the specified tenant's context to enforced."""
        ctx = self.apic.fvCtx.get(tenant_id, ctx_id)
        if not ctx:
            self.apic.fvCtx.create(
                tenant_id, ctx_id, pcEnfPref=CONTEXT_ENFORCED)
        elif ctx['pcEnfPref'] != CONTEXT_ENFORCED:
            self.apic.fvCtx.update(
                tenant_id, ctx_id, pcEnfPref=CONTEXT_ENFORCED)

    def ensure_context_any_contract(self, tenant_id, ctx_id, contract_id):
        """Set the specified tenant's context to enforced."""
        vzany = self.apic.vzAny.get(tenant_id, ctx_id)
        if not vzany:
            vzany = self.apic.vzAny.create(tenant_id, ctx_id)

        provider = self.apic.vzRsAnyToProv.get(
            tenant_id, ctx_id, contract_id)
        if not provider:
            self.apic.vzRsAnyToProv.create(
                tenant_id, ctx_id, contract_id)

        consumer = self.apic.vzRsAnyToCons.get(
            tenant_id, ctx_id, contract_id)
        if not consumer:
            self.apic.vzRsAnyToCons.create(
                tenant_id, ctx_id, contract_id)

    def make_tenant_contract_global(self, router_id):
        """Mark the tenant contract's scope to global."""
        contract = self.db.get_contract_for_router(router_id)
        self.apic.vzBrCP.update(contract.tenant_id, contract.contract_id,
                                scope=SCOPE_GLOBAL)

    def make_tenant_contract_local(self, router_id):
        """Mark the tenant contract's scope to tenant."""
        contract = self.db.get_contract_for_router(router_id)
        self.apic.vzBrCP.update(contract.tenant_id, contract.contract_id,
                                scope=SCOPE_TENANT)

    def ensure_path_created_for_port(self, tenant_id, network_id,
                                     host_id, encap):
        """Create path attribute for an End Point Group."""
        epg = self.ensure_epg_created_for_network(tenant_id, network_id)
        eid = epg.epg_id

        # Get attached switch and port for this host
        host_config = self.db.get_switch_and_port_for_host(host_id)
        if not host_config:
            raise cexc.ApicHostNotConfigured(host=host_id)

        for switch, module, port in host_config:
            self.ensure_path_binding_for_port(
                tenant_id, eid, encap, switch, module, port)

    def ensure_path_binding_for_port(self, tenant_id, epg_id, encap,
                                     switch, module, port):
        # Verify that it exists, or create it if required
        encap = 'vlan-' + str(encap)
        pdn = PORT_DN_PATH % (switch, module, port)
        patt = self.apic.fvRsPathAtt.get(
            tenant_id, self.app_profile_name, epg_id, pdn)
        if not patt:
            self.apic.fvRsPathAtt.create(
                tenant_id, self.app_profile_name, epg_id, pdn,
                encap=encap, mode="regular",
                instrImedcy="immediate")

    def ensure_vlans_created_for_host(self, host):
        segments = self.db.get_tenant_network_vlan_for_host(host)
        for tenant, network, encap in segments:
            tenant_id = self.db.get_apic_name(
                tenant, apic_mapper.NAME_TYPE_TENANT)[0]
            network_id = self.db.get_apic_name(
                network, apic_mapper.NAME_TYPE_NETWORK)[0]
            self.ensure_path_created_for_port(
                tenant_id, network_id, host, encap)

    def add_router_interface(self, tenant_id, router_id,
                             network_id, owner=TENANT_COMMON,
                             context=CONTEXT_SHARED):
        # Get contract and epg
        contract = self.get_router_contract(router_id, owner=owner)
        epg = self.ensure_epg_created_for_network(tenant_id, network_id)

        # Ensure that the router ctx exists
        self.ensure_context_enforced(owner, context)

        # update corresponding BD's ctx to this router ctx
        bd_id = network_id
        self.apic.fvRsCtx.update(tenant_id, bd_id, tnFvCtxName=context)

        # set the EPG to provide this contract
        if not self.get_prov_contract_for_epg(tenant_id, epg.epg_id,
                                              contract.contract_id):
            self.set_contract_for_epg(
                tenant_id, epg.epg_id, contract.contract_id, True)

        # set the EPG to consume this contract
        if not self.get_cons_contract_for_epg(tenant_id, epg.epg_id,
                                              contract.contract_id):
            self.set_contract_for_epg(
                tenant_id, epg.epg_id, contract.contract_id)

    def remove_router_interface(self, tenant_id, router_id,
                                network_id, owner=TENANT_COMMON,
                                context=CONTEXT_SHARED):
        # Get contract and epg
        contract = self.get_router_contract(router_id, owner=owner)
        epg = self.ensure_epg_created_for_network(tenant_id, network_id)

        # Delete contract for this epg
        self.delete_contract_for_epg(
            tenant_id, epg.epg_id, contract.contract_id, epg.provider)

        # set the BDs' ctx to default
        bd_id = network_id
        self.apic.fvRsCtx.update(
            tenant_id, bd_id, tnFvCtxName=context)

    def delete_router(self, router_id):
        self.delete_router_contract(router_id)

    def delete_external_routed_network(self, network_id, owner=TENANT_COMMON):
        self.apic.l3extRsEctx.delete(owner, network_id)

    def ensure_external_routed_network_created(self, network_id,
                                               owner=TENANT_COMMON,
                                               context=CONTEXT_SHARED):
        """Creates a L3 External context on the APIC."""
        self._create_if_not_exist(self.apic.l3extOut, owner, network_id)
        # Link external context to the internal router ctx
        self.apic.l3extRsEctx.update(owner,
                                     network_id, tnFvCtxName=context)

    def ensure_logical_node_profile_created(self, network_id,
                                            switch, module, port, encap,
                                            address, owner=TENANT_COMMON):
        """Creates Logical Node Profile for External Network in APIC."""
        try:
            self._create_if_not_exist(self.apic.l3extLNodeP, owner, network_id,
                                      EXT_NODE)
            # TODO(ivar): default value for router id
            self._create_if_not_exist(
                self.apic.l3extRsNodeL3OutAtt, owner, network_id, EXT_NODE,
                NODE_DN_PATH % switch, rtrId='1.0.0.1')
            self._create_if_not_exist(
                self.apic.l3extRsPathL3OutAtt, owner, network_id, EXT_NODE,
                EXT_INTERFACE, PORT_DN_PATH % (switch, module, port),
                encap=encap or 'unknown', addr=address,
                ifInstT='l3-port' if not encap else 'sub-interface')

        except (cexc.ApicResponseNotOk, KeyError):
            with excutils.save_and_reraise_exception():
                self.apic.l3extLNodeP.delete(owner, network_id,
                                             EXT_NODE)

    def ensure_static_route_created(self, network_id, switch,
                                    next_hop, subnet='0.0.0.0/0',
                                    owner=TENANT_COMMON):
        """Add static route to existing External Routed Network."""
        self._create_if_not_exist(self.apic.ipNexthopP, owner, network_id,
                                  EXT_NODE, NODE_DN_PATH % switch, subnet,
                                  next_hop)

    def ensure_external_epg_created(self, router_id, subnet='0.0.0.0/0',
                                    owner=TENANT_COMMON):
        """Add EPG to existing External Routed Network."""
        self._create_if_not_exist(self.apic.l3extSubnet, owner, router_id,
                                  EXT_EPG, subnet)

    def ensure_external_epg_consumed_contract(self, network_id, contract_id,
                                              owner=TENANT_COMMON):
        self._create_if_not_exist(self.apic.fvRsCons__Ext, owner,
                                  network_id, EXT_EPG, contract_id)

    def ensure_external_epg_provided_contract(self, network_id, contract_id,
                                              owner=TENANT_COMMON):
        self._create_if_not_exist(self.apic.fvRsProv__Ext, owner,
                                  network_id, EXT_EPG, contract_id)

    def delete_external_epg_contract(self, router_id, network_id,
                                     owner=TENANT_COMMON):
        contract = self.db.get_contract_for_router(router_id)
        if contract:
            self.apic.fvRsCons__Ext.delete(owner, network_id, EXT_EPG,
                                           contract.contract_id)
            self.apic.fvRsProv__Ext.delete(owner, network_id, EXT_EPG,
                                           contract.contract_id)

    def ensure_external_routed_network_deleted(self, network_id,
                                               owner=TENANT_COMMON):
        self.apic.l3extOut.delete(owner, network_id)

    def add_hostlink(self,
                     host, ifname, ifmac,
                     switch, module, port):
        prev_links = self.db.get_hostlinks_for_host_switchport(
            host, switch, module, port)
        self.db.add_hostlink(host, ifname, ifmac,
                             switch, module, port)
        if not prev_links:
            self.ensure_infra_created_for_switch(switch)
            self.ensure_vlans_created_for_host(host)

    def remove_hostlink(self,
                        host, ifname, ifmac,
                        switch, module, port):
        self.db.delete_hostlink(host, ifname)
        # TODO(mandeep): delete the right elements

    def clean(self):
        """Clean up apic profiles and DB information (useful for testing)."""
        # clean infra profiles
        vlan_ns_name = self.apic_config.apic_vlan_ns_name
        self.apic.fvnsVlanInstP.delete(vlan_ns_name, 'dynamic')
        self.apic.fvnsVlanInstP.delete(vlan_ns_name, 'static')

        # delete physdom profiles
        phys_name = self.apic_config.apic_domain_name
        self.apic.physDomP.delete(phys_name)

        # delete entity profile
        ent_name = self.apic_config.apic_entity_profile
        self.apic.infraAttEntityP.delete(ent_name)

        # delete function profile
        func_name = self.apic_config.apic_function_profile
        self.apic.infraAccPortGrp.delete(func_name)

        # delete switch profile for switches in DB
        for switch_id in self.db.get_nodes_with_port_profile():
            self.apic.infraNodeP.delete(switch_id)

        # clean db
        self.db.clean()
