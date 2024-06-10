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

import yaml
import json
import sys
from neutron.plugins.ml2.drivers.ndfc_helper import NdfcHelper
import neutron.plugins.ml2.drivers.constants as constants
from oslo_log import log
import os.path
import time

LOG = log.getLogger(__name__)

glob_nwk_map = {}

class Ndfc:
    '''
    DCNM class.
    '''
    def __init__(self, ndfc_ip, user, pwd, fabric):
        '''
        Init routine that initializes reads the yaml file
        '''
        self.ip = ndfc_ip
        self.user = user
        self.pwd = pwd
        self.fabric = fabric
        self.network_name_id_map = {}
        self.tmp_filename = "/tmp/network_map.json"

        self.netword_id_start = constants.NetworkIdStart
        self.network_id = self.netword_id_start
        self.mcast_group = constants.MCAST_GROUP

        #self.physnet_file = self.read_json("/usr/lib/python3/dist-packages/neutron/plugins/ml2/drivers/topology.json")
        self.physnet_file = self.read_json("./neutron/plugins/ml2/drivers/topology.json")
        self.physnets = self.physnet_file.get('Physnets')
        LOG.debug("Pyhsnets read is %s", self.physnets)

        self.physnet_map = {}
        for phys in self.physnets:
            name = phys.get("Name")
            self.physnet_map[name] = phys

        #self.switches = self.physnets.get('Switches')

        t = time.localtime()
        self.current_time = time.strftime("%H:%M:%S", t)
        self.set_network_id("dummy", self.netword_id_start)
        LOG.debug("REMOVE NDFC Network ID Map Init called %s Current %s", self.network_name_id_map, self.current_time)
        #yself.ndfc_obj = ndfc_helper.NdfcHelper(ip=self.ip, user=self.user, pwd=self.pwd)
        self.ndfc_obj = NdfcHelper(ip=self.ip, user=self.user, pwd=self.pwd)

    def get_network_id(self, network_name):
        global glob_nwk_map
        LOG.debug("REMOVE network map in get is %s and glob is glob_nwk_map %s", self.network_name_id_map, glob_nwk_map)
        if network_name in self.network_name_id_map:
            return self.network_name_id_map[network_name]
        LOG.info("*** Network name %s not found in map, trying global", network_name)
        if network_name in glob_nwk_map:
            return glob_nwk_map[network_name]
        LOG.info("*** Network name %s not found in glob map, trying file")
        glob_nwk_map = self.read_json(self.tmp_filename)
        LOG.info("After reading from file %s", glob_nwk_map)
        self.network_name_id_map = glob_nwk_map
        return self.network_name_id_map[network_name]

    def set_network_id(self, network_name, network_id):
        global glob_nwk_map
        glob_nwk_map = self.read_json(self.tmp_filename)
        if glob_nwk_map is None:
            glob_nwk_map = {}
        self.network_name_id_map = glob_nwk_map
        self.network_name_id_map[network_name] = network_id
        glob_nwk_map[network_name] = network_id
        self.write_json(self.tmp_filename, glob_nwk_map)
        LOG.debug("REMOVE Network map set to %s andf global set to %s", self.network_name_id_map, glob_nwk_map)

    def get_next_network_id(self):
        nwkmap = self.read_json(self.tmp_filename)
        nids = []
        for n, nid in nwkmap.items():
            nids.append(nid)
        LOG.debug("Read nids is %s", nids)
        newnids = sorted(nids)
        newnid = newnids[len(newnids) - 1]
        LOG.debug("Returning new nid is %s", newnid)
        return newnid + 1

    def reset_network_id(self, network_name, network_id):
        nwkmap = self.read_json(self.tmp_filename)
        nwkmap.pop(network_name)
        LOG.debug("New nwk map is %s", nwkmap)
        self.write_json(self.tmp_filename, nwkmap)

    def read_json(self, filename):
        is_file = os.path.isfile(filename)
        if not is_file:
            LOG.info("File %s does not exist", filename)
            return None
        with open(filename, "r") as stream:
            try:
                json_in = json.load(stream)
                LOG.debug("READ is %s", json_in)
                return json_in
            except Exception as exc:
                LOG.error("Exception in read_json %s", exc)
        LOG.debug("Returning none")
        return None

    def write_json(self, filename, dct):
        with open(filename, "w") as outfile:
            json.dump(dct, outfile, indent=4)

    def read_yml(self, filename):
        with open(filename, "r") as stream:
            try:
                yml_in = yaml.safe_load(stream)
                return yml_in
            except yaml.YAMLError as exc:
                print(exc)

    def create_vrf(self, vrf_name):
        #import pdb
        #pdb.set_trace()
        fabric = self.fabric
        tag = constants.TAG
        template_config_vrf = {'routeTarget': 'auto',
                'vrfName': vrf_name,'vrfVlanName': '',
                'vrfIntfDescription': '', 'vrfDescription': '',
                'trmEnabled': 'false', 'isRPExternal': 'false',
                'advertiseHostRouteFlag': 'false',
                'advertiseDefaultRouteFlag': 'true',
                'configureStaticDefaultRouteFlag': 'true',
                'tag': tag,
                'vrfRouteMap': 'FABRIC-RMAP-REDIST-SUBNET',
                'maxBgpPaths': '1','maxIbgpPaths': '2',
                'rpAddress': '', 'loopbackNumber': '',
                'L3VniMcastGroup': '', 'multicastGroup': ''}
        dct = {"fabric": fabric,"vrfName": vrf_name,
                "vrfTemplate": "Default_VRF_Universal",
                "vrfTemplateConfig": template_config_vrf,
                "vrfTemplateParams": "{}",
                "vrfExtensionTemplate":"Default_VRF_Extension_Universal"}
        ret = self.ndfc_obj.create_vrf(fabric, dct)
        LOG.info("For fabric %s, vrf %s, create vrf returned %s", fabric, vrf_name, ret)
        return ret

    def create_network(self, vrf_name, network_name, vlan, physnet):
        LOG.info("Create network called for vrf %s network %s vlan %s and physnet %s",
                vrf_name, network_name, vlan, physnet)
        self.network_id = self.get_next_network_id()
        LOG.debug("REMOVE Network id %s time %s", self.network_id, self.current_time)
        subnet = ""
        gw = ""
        tag = constants.TAG
        fabric = self.fabric
        template_config_network = {'gatewayIpAddress': gw,
                'gatewayIpV6Address': '',
                'intfDescription': '', 'suppressArp': False,
                'enableIR': False, 'mcastGroup': self.mcast_group,
                'dhcpServerAddr1': '', 'dhcpServerAddr2': '',
                'loopbackId': '', 'vrfDhcp': '',
                'mtu': 9216, 'segmentId': self.network_id,
                'vrfName': vrf_name, 'networkName': network_name,
                'isLayer2Only': False, 'nveId': 1,
                'vlanId': vlan, 'vlanName': '',
                'secondaryGW1': '', 'secondaryGW2': '',
                'trmEnabled': '', 'rtBothAuto': '',
                'enableL3OnBorder': '','tag': tag}
        dct = {'fabric': fabric,'vrf': vrf_name,
                'networkName': network_name,
                'networkId': self.network_id,
                'networkTemplateConfig': template_config_network,
                'networkTemplate': 'Default_Network_Universal'}

        attach_list = []
        physmap = self.physnet_map.get(physnet)
        if physmap is None:
            LOG.error("No physnet info found for %s", physnet)
            return False
        switch_info = physmap.get("Switches")
        if switch_info is None:
            LOG.error("No switches found for physnet %s", physnet)
            return False
        for sw in switch_info:
            snum = sw.get('Serial')
            intf = sw.get('Interfaces')
            tor_intf = sw.get('TorInterfaces')
            if tor_intf is not None:
                attach = {"fabric": fabric, "networkName": network_name,
                        "serialNumber": snum, "switchPorts": "",
                        "torPorts": tor_intf, "detachSwitchPorts":"",
                        "vlan": vlan, "dot1QVlan":1,
                        "untagged": "false", "freeformConfig":"",
                        "deployment": "true", "extensionValues":"",
                        "instanceValues":""}
            else:
                attach = {"fabric": fabric, "networkName": network_name,
                        "serialNumber": snum, "switchPorts": intf,
                        "detachSwitchPorts":"", "vlan": vlan,
                        "dot1QVlan":1, "untagged": "false",
                        "freeformConfig":"", "deployment": "true",
                        "extensionValues":"", "instanceValues":""}
            attach_list.append(attach)
        attach_dct = [{"networkName":network_name,"lanAttachList":attach_list}]
        LOG.debug("Calling create network for %s:%s with %s and %s", fabric, network_name, dct, attach_dct)
        ret = self.ndfc_obj.create_attach_deploy_network(fabric, network_name, dct, attach_dct)
        LOG.info("Network id %s time %s", self.network_id, self.current_time)
        LOG.info("For %s:%s, create attach NDFC network returned %s", fabric, network_name, ret)
        if ret:
            self.set_network_id(network_name, self.network_id)
        return ret

    def _get_deploy_payload(self, physnet, network):
        dct = {}
        physmap = self.physnet_map.get(physnet)
        if physmap is None:
            LOG.error("No physnet info found for %s", physnet)
            return dct
        switch_info = physmap.get("Switches")
        if switch_info is None:
            LOG.error("No switches found for physnet %s", physnet)
            return dct
        for sw in switch_info:
            snum = sw.get('Serial')
            dct[snum] = network
        return dct
 
    def update_network(self, vrf_name, network_name, vlan, gw, physnet):
        fabric = self.fabric
        tag = constants.TAG
        LOG.info("NDFC update network called for %s:%s:%s with GW %s", vrf_name, network_name, vlan, gw)
        LOG.debug("REMOVE Network id %s time %s", self.network_id, self.current_time)
        #LOG.debug("REMOVE NDFC Network ID Map in update_network %s fabric %s network %s current time %s", self.network_name_id_map, fabric, network_name, self.current_time)
        #network_id = self.network_name_id_map.get(network_name)
        network_id = self.get_network_id(network_name)
        LOG.debug("REMOVE obtained network id %s", network_id)
        template_config_network = {'gatewayIpAddress': gw,
                'gatewayIpV6Address': '', 'intfDescription': '',
                'suppressArp': False, 'enableIR': False,
                'mcastGroup': self.mcast_group,
                'dhcpServerAddr1': '', 'dhcpServerAddr2': '',
                'loopbackId': '', 'vrfDhcp': '',
                'mtu': 9216, 'segmentId': network_id,
                'vrfName': vrf_name, 'networkName': network_name,
                'isLayer2Only': False, 'nveId': 1,
                'vlanId': vlan, 'vlanName': '',
                'secondaryGW1': '', 'secondaryGW2': '',
                'trmEnabled': '', 'rtBothAuto': '',
                'enableL3OnBorder': '', 'tag': tag}
        dct = {'fabric': fabric,'vrf': vrf_name,
                'networkName': network_name,
                'networkId': network_id,
                'networkTemplateConfig': template_config_network,
                'networkTemplate': 'Default_Network_Universal'}
        LOG.debug("REMOVE dct for update network is %s", dct)
        LOG.debug("REMOVE update end Network id %s time %s", self.network_id, self.current_time)
        payload = self._get_deploy_payload(physnet, network_name)
        if len(payload) == 0:
            LOG.error("No switches found")
            return False
        ret = self.ndfc_obj.update_deploy_network(fabric, network_name, dct, payload)
        LOG.info("For %s:%s update network returned %s", fabric, network_name, ret)
        return ret

    def delete_network(self, network_name, vlan, physnet):
        fabric = self.fabric
        attach_list = []
        physmap = self.physnet_map.get(physnet)
        if physmap is None:
            LOG.error("No physnet info found for %s", physnet)
            return False
        switch_info = physmap.get("Switches")
        if switch_info is None:
            LOG.error("No switches found for physnet %s", physnet)
            return False
        for sw in switch_info:
            snum = sw.get('Serial')
            intf = sw.get('Interfaces')
            attach = {"fabric": fabric,
                    "networkName": network_name,
                    "serialNumber": snum,
                    "switchPorts": intf,
                    "detachSwitchPorts":"",
                    "vlan": vlan,
                    "dot1QVlan":1,
                    "untagged": "false",
                    "freeformConfig":"",
                    "deployment": "false",
                    "extensionValues":"",
                    "instanceValues":""}
            attach_list.append(attach)
        attach_dct = [{"networkName":network_name,"lanAttachList":attach_list}]
        payload = self._get_deploy_payload(physnet, network_name)
        if len(payload) == 0:
            LOG.error("No switches found")
            return False
        ret = self.ndfc_obj.detach_delete_deploy_network(fabric, network_name, attach_dct, payload)
        LOG.info("For %s:%s delete network returned %s", fabric, network_name, ret)
        if ret:
            self.reset_network_id(network_name, self.network_id)
        return ret

    def delete_vrf(self, vrf_name):
        LOG.info("Delete vrf called with %s", vrf_name)
        fabric = self.fabric
        ret = self.ndfc_obj.delete_vrf(fabric, vrf_name)
        LOG.info("For %s:%s delete vrf returned %s", fabric, vrf_name, ret)
        return ret
