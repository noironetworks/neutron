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

from functools import wraps
import json
import logging
from oslo_log import log
import requests
from requests.exceptions import HTTPError
#from workload_auto import logger

ADD = "ADD"
DELETE_ADD = "DELETE_ADD"
NOOP = "NOOP"

LOG = log.getLogger(__name__)
log = logging.getLogger(__name__)

class NdfcHelper:
    '''
    DCNM helper class.
    '''
    def __init__(self, **kwargs):
        '''
        Init routine that initializes the URL's, user, pws etc.
        '''
        self._base_url = "appcenter/cisco/ndfc/api/v1/security/fabrics/"
        #self._vrf_url = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/"
        self._vrf_url = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/v2/fabrics/"
        self._network_url = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/v2/fabrics/"
        self._config_save_url = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/fabrics/"
        self._deploy_save_url = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/fabrics/"
        self._network_deploy_url = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/v2/networks/deploy/"
        self._inventory_url = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/fabrics/"
        self._interface_url = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/interface/detail/filter?serialNumber="
        self._topology_url  = "appcenter/cisco/ndfc/api/v1/lan-fabric/rest/topology/topologydataforvmm?serialNumbers="

        self._ip = kwargs['ip']
        self._user = kwargs['user']
        self._pwd = kwargs['pwd']
        self._timeout_resp = 100
        self._req_headers = {'Accept': 'application/json',
                             'Content-Type': 'application/json; charset=UTF-8'}
        self._resp_ok = (requests.codes.ok, requests.codes.created,
                         requests.codes.accepted)
        self._expiration_time = 100000
        self._protocol_host_url = "https://" + self._ip + "/"

    def _build_url(self, remaining_url):
        '''
        Appends the base URL with the passing URL.
        '''
        return self._protocol_host_url + remaining_url

    def http_exc_handler(http_func):
        '''
        Decorator function for catching exceptions.
        '''
        @wraps(http_func)
        def exc_handler_int(*args):
            try:
                fn_name = http_func.__name__
                return http_func(*args)
            except HTTPError as http_err:
                log.error("HTTP error during call to %(func)s, %(err)s",
                          {'func': fn_name, 'err': http_err})
        return exc_handler_int

    @http_exc_handler
    def get_jwt_token(self):
        '''
        Function to get jwt token
        '''
        login_url = self._build_url('login')
        payload = {'userName': self._user, 'userPasswd': self._pwd,
                   'domain': 'DefaultAuth',
                   'expirationTime': self._expiration_time}
        res = requests.post(login_url, data=json.dumps(payload),
                            headers=self._req_headers,
                            #auth=(self._user, self._pwd),
                            timeout=self._timeout_resp, verify=False)
        session_id = ""
        if res and res.status_code in self._resp_ok:
            session_id = res.json().get('jwttoken')
            return session_id

    @http_exc_handler
    def login(self):
        '''
        Function for login to DCNM.
        '''
        session_id = self.get_jwt_token()
        if session_id is not None:
            self._req_headers.update({'Authorization': 'Bearer ' + session_id})
            return True, session_id
        return False, ""

    @http_exc_handler
    def logout(self):
        '''
        Function for logoff from DCNM.
        '''
        logout_url = self._build_url('rest/logout')
        requests.post(logout_url, headers=self._req_headers,
                      timeout=self._timeout_resp, verify=False)

    @http_exc_handler
    def _get_token(self):
        '''
        Function for retrieving the token
        '''
        url = self._build_url("fm/fmrest/security/apptoken/create?id=2367898177")
        res = requests.get(url, headers=self._req_headers,
                           timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            data = res.json()
        return data

    def get_token(self):
        '''
        Top level function for retrieving the fabrics.
        '''
        fab_info = []
        try:
            ret = self.login()
            if ret:
                token_info = self._get_token()
                self.logout()
        except Exception as exc:
            log.error("Exception in get_token, %(exc)s", {exc:exc})
        return token_info

    @http_exc_handler
    def _get_attachments(self, fabric, nwk_name):
        '''
        Retrieve the network attachment given the fabric and network.
        '''
        url = self._build_url(self._base_url) + fabric + "/" + (
            self._attach_url + "?network-names=" + nwk_name)
        res = requests.get(url, headers=self._req_headers,
                           timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            data = res.json()
            return data
        log.error("invalid result for get_attachments status %(status)s",
                  {'status': res.status_code})
        return None

    @http_exc_handler
    def _attach_network(self, fab_parent, fabric, nwk_name, snum_list,
                        sw_ports, vlan, enable):
        '''
        Function to attach the network.
        '''
        url = self._build_url(self._base_url) + fab_parent + "/" + (
            self._attach_url)
        lan_attach_list = []
        payload_list = []
        dot1q = 1
        lan_attach = {'fabric': fabric, 'networkName': nwk_name,
                      'serialNumber': snum_list,
                      'switchPorts': sw_ports, 'vlan': vlan,
                      'dot1QVlan': dot1q, 'untagged': False,
                      'detachSwitchPorts': "", 'freeformConfig': "",
                      'extensionValues': "", 'instanceValues': "",
                      'deployment': enable}
        lan_attach_list.append(lan_attach)
        payload = {'networkName' : nwk_name,
                   'lanAttachList': lan_attach_list}
        payload_list.append(payload)
        data = json.dumps(payload_list)
        res = requests.post(url, data=data,
                            headers=self._req_headers,
                            timeout=self._timeout_resp, verify=False)
        LOG.info("attach networl url %s res is %s, data %s", url, res, data)
        if res and res.status_code in self._resp_ok:
            LOG.info("attach network successful")
        else:
            LOG.error("attach network failed with status %s, res %s",
                     res.status_code, res.json())

    @http_exc_handler
    def _is_nwk_exist(self, fabric, nwk):
        '''
        Function that returns if the network for the fabric exists
        in DCNM.
        '''
        url = self._build_url(self._nwk_get_url) + fabric + "/networks/" + nwk
        res = requests.get(url, headers=self._req_headers,
                           timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            data = res.json()
            nwk_val = data.get('networkName')
            if nwk_val is None:
                return False
            return nwk_val == nwk
        log.error("invalid result for is_nwk_exist for fabric %s nwk %s",
                  fabric, nwk)
        return False

    def is_nwk_exist(self, fabric, nwk):
        '''
        Function that returns if the network for the fabric exists
        in DCNM.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._is_nwk_exist(fabric, nwk)
            self.logout()
            return ret
        except Exception as exc:
            log.error("Exception raised in is_nwk_exist %s", exc)
            return False

    def attach_network(self, enable, deploy, arg_dict):
        '''
        Top level function to attach the network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return
            log.info("attach_network arg is %s for enable: %s", arg_dict,
                     enable)
            fab_parent = arg_dict.get('fab_parent')
            fabric = arg_dict.get('fab')
            nwk_name = arg_dict.get('nwk')
            snum_intf_dict = arg_dict.get('snum_intf_dict')
            vlan = arg_dict.get('vlan')

            exist_data = self._get_attachments(fab_parent, nwk_name)
            log.info("exist_data is %s", exist_data)
            sw_oper_dict, exist_cfg_dict = self._compare_exist_cfg(exist_data,
                                                                   arg_dict,
                                                                   enable)
            for snum, intf_lst in snum_intf_dict.items():
                oper = sw_oper_dict.get(snum)
                log.info("For %s, operation is %s", snum, oper)
                if oper == NOOP:
                    continue
                if enable:
                    if oper == DELETE_ADD:
                        exist_cfg = exist_cfg_dict.get(snum)
                        self._attach_network(fab_parent, fabric, nwk_name, snum,
                                             exist_cfg.get('portNames'),
                                             exist_cfg.get('vlanId'), False)
                    # For ADD and a DELETE_ADD, the below enable is common
                    self._attach_network(fab_parent, fabric, nwk_name, snum,
                                         ",".join(intf_lst), vlan, enable)
                else:
                    #disable cannopt have a DELETE_ADD
                    self._attach_network(fab_parent, fabric, nwk_name, snum,
                                         ",".join(intf_lst), vlan, enable)
            if deploy:
                self._deploy_network(fabric, nwk_name)
            self.logout()
        except Exception as exc:
            log.error("attach network failed with %(exc)s", {'exc': exc})

    @http_exc_handler
    def _deploy_network(self, fabric, nwk_name):
        '''
        Function to deploy the network in DCNM.
        '''
        url = self._build_url(self._base_url) + fabric + "/" + (
            self._deploy_url)
        payload = {'networkNames' : nwk_name}
        #payload_list.append(payload)
        data = json.dumps(payload)
        res = requests.post(url, data=data,
                            headers=self._req_headers,
                            timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            log.info("deploy network successful")
        else:
            log.info("deploy network failed with res %s", res)

    def deploy_network(self, fabric, nwk_name):
        '''
        Top level function to deployt the network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return
            self._deploy_network(fabric, nwk_name)
            self.logout()
        except Exception as exc:
            log.error("deploy network failed with exception %(exc)s",
                      {'exc': exc})

    @http_exc_handler
    def _create_network(self, fabric, payload):
        '''
        Function to create the Network in DCNM.
        '''
        url = self._build_url(self._network_url) + fabric + "/networks"
        res = requests.post(url, headers=self._req_headers, data=json.dumps(payload), 
                            timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            log.info("create network successful")
            return True
        log.info("create network failed with res %s, payload %s", res, json.dumps(payload))
        return False

    def create_network(self, fabric, payload):
        '''
        Top level function to create the Network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._create_network(fabric, payload)
            if not ret:
                return False
            self.logout()
        except Exception as exc:
            log.error("create network failed with exception %(exc)s",
                      {'exc': exc})
            return False
        return True

    @http_exc_handler
    def _update_network(self, fabric, network_name, payload):
        '''
        Function to update the Network in DCNM.
        '''
        url = self._build_url(self._network_url) + fabric + "/networks/" + network_name
        res = requests.put(url, headers=self._req_headers, data=json.dumps(payload), 
                            timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            log.info("update network successful")
            return True
        log.info("update network failed with res %s and payload %s", res, json.dumps(payload))
        return False

    def update_network(self, fabric, network_name, payload):
        '''
        Top level function to update the Network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._update_network(fabric, network_name, payload)
            if not ret:
                return False
            self.logout()
        except Exception as exc:
            log.error("update network failed with exception %(exc)s",
                      {'exc': exc})
            return False
        return True

    def update_deploy_network(self, fabric, network_name, update_payload, deploy_payload):
        '''
        Function to create, attach and deploy the network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._update_network(fabric, network_name, update_payload)
            if not ret:
                return False
            ret = self._config_deploy_save(fabric, deploy_payload)
            if not ret:
                return False
            self.logout()
        except Exception as exc:
            log.error("create, attach and deploy network failed with exception %(exc)s",
                      {'exc': exc})
            return False
        return True

    @http_exc_handler
    def _attach_network(self, fabric, payload):
        '''
        Function to attach the network in DCNM.
        '''
        url = self._build_url(self._network_url) + fabric + "/networks/attachments"
        res = requests.post(url, headers=self._req_headers, data=json.dumps(payload), 
                            timeout=self._timeout_resp, verify=False)
        LOG.debug("attach network url %s payload %s", url, json.dumps(payload))
        if res and res.status_code in self._resp_ok:
            LOG.info("attach betwork successful")
            return True
        LOG.error("attach betwork failed with res %s", res)
        return False

    def attach_network(self, fabric, payload):
        '''
        Top level function to attach the Network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._attach_network(fabric, payload)
            if not ret:
                return False
            self.logout()
        except Exception as exc:
            log.error("attach network failed with exception %(exc)s",
                      {'exc': exc})
            return False
        return True

    def create_attach_deploy_network(self, fabric, network_name, create_payload, attach_payload):
        '''
        Function to create, attach and deploy the network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False

            ret = self._create_network(fabric, create_payload)
            if not ret:
                return False
            ret = self._attach_network(fabric, attach_payload)
            if not ret:
                return False
            # TODO Have postponed the deploy to subnet creation.
            #ret = self._config_deploy_save(fabric)
            #if not ret:
            #    return False
            self.logout()
        except Exception as exc:
            log.error("create, attach network failed with exception %(exc)s",
                      {'exc': exc})
            self.logout()
            return False
        return True

    @http_exc_handler
    def _delete_network(self, fabric, network):
        '''
        Function to create the Network in DCNM.
        '''
        url = self._build_url(self._network_url) + fabric + "/bulk-delete/networks?network-names=" + network
        res = requests.delete(url, headers=self._req_headers, timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            log.info("delete network successful")
            return True
        log.info("delete network failed with res %s", res)
        return False

    def delete_network(self, fabric, network):
        '''
        Top level function to delete the Network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._delete_network(fabric, network)
            if not ret:
                return False
            self.logout()
        except Exception as exc:
            log.error("delete network failed with exception %(exc)s",
                      {'exc': exc})
            return False
        return True

    def detach_delete_deploy_network(self, fabric, network_name, attach_payload, deploy_payload):
        '''
        Function to detach, delete and deploy the network.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._attach_network(fabric, attach_payload)
            if not ret:
                log.Error("Detaching the network failed")
                return False
            # TODO check with shyam, delete of network fails if network deploy is done below.
            ret = self._config_deploy_save(fabric, {})
            if not ret:
                LOG.error("config deploy save failed after attach")
                return False
            ret = self._delete_network(fabric, network_name)
            if not ret:
                LOG.error("delete network failed")
                return False
            ret = self._config_deploy_save(fabric, deploy_payload)
            if not ret:
                LOG.error("config deploy save failed after delete network")
                return False
            self.logout()
        except Exception as exc:
            log.error("detach, delete network failed with exception %(exc)s",
                      {'exc': exc})
            self.logout()
            return False
        return True

    @http_exc_handler
    def _config_deploy_save(self, fabric, deploy_payload):
        '''
        Function to create the VRF in DCNM.
        '''
        if len(deploy_payload) == 0:
            url = self._build_url(self._deploy_save_url) + fabric + "/config-deploy?forceShowRun=false"
            LOG.info("Deploy called with url %s", url)
            res = requests.post(url, headers=self._req_headers,
                                timeout=self._timeout_resp, verify=False)
        else:
            url = self._build_url(self._network_deploy_url)
            LOG.info("Deploy called with url %s and payload %s", url, deploy_payload)
            res = requests.post(url, headers=self._req_headers, data=json.dumps(deploy_payload), 
                                timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            LOG.info("deploy save successful")
            return True
        LOG.error("deploy save failed with res %s", res)
        return False

    def config_deploy_save(self, fabric):
        '''
        Top level function to save and deploy the config.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._config_deploy_save(fabric)
            if not ret:
                return False
            self.logout()
        except Exception as exc:
            log.error("config/ deploy failed with exception %(exc)s",
                      {'exc': exc})
            return False
        return True

    @http_exc_handler
    def _create_vrf(self, fabric, payload):
        '''
        Function to create the VRF in DCNM.
        '''
        url = self._build_url(self._vrf_url) + fabric + "/vrfs"
        res = requests.post(url, headers=self._req_headers, data=json.dumps(payload), 
                            timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            log.info("create vrf successful")
            return True
        log.info("create vrf failed with res %s", res)
        return False

    def create_vrf(self, fabric, payload):
        '''
        Top level function to create the VRF.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return False
            ret = self._create_vrf(fabric, payload)
            if not ret:
                return False
            self.logout()
        except Exception as exc:
            log.error("create vrf failed with exception %(exc)s",
                      {'exc': exc})
            return False
        return True

    @http_exc_handler
    def _delete_vrf(self, fabric, vrf):
        '''
        Function to create the Vrf in DCNM.
        '''
        url = self._build_url(self._vrf_url) + fabric + "/bulk-delete/vrfs?vrf-names=" + vrf
        res = requests.delete(url, headers=self._req_headers,
                timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            log.info("delete vrf successful")
            return True
        else:
            log.info("delete vrf failed with res %s", res)
            return False

    def delete_vrf(self, fabric, vrf):
        '''
        Top level function to delete the VRF.
        '''
        try:
            ret = self.login()
            if not ret:
                log.error("Failed to login to DCNM")
                return false
            ret = self._delete_vrf(fabric, vrf)
            if not ret:
                return False
            ret = self._config_deploy_save(fabric)
            if not ret:
                return False
            self.logout()
        except Exception as exc:
            log.error("delete vrf failed with exception %(exc)s",
                      {'exc': exc})
            self.logout()
            return False
        return True

    @http_exc_handler
    def _get_switches(self, fabric):
        '''
        Function for retrieving the switch list from DCNM, given the fabric.
        '''
        switches_map = {}
        url = self._build_url(self._inventory_url) + fabric + "/inventory/"
        res = requests.get(url, headers=self._req_headers,
                timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            data = res.json()
            for sw_info in data:
                snum = sw_info.get("serialNumber")
                ip = sw_info.get("ipAddress")
                role = sw_info.get("switchRole")
                name = sw_info.get("logicalName")
                sw_dct = {'serial': snum, 'ip': ip, 'role': role, 'name': name}
                if role == "tor":
                    topo_url = self._build_url(self._topology_url) + snum
                    res_topo = requests.get(topo_url, headers=self._req_headers,
                            timeout=self._timeout_resp, verify=False)
                    topo_data = res_topo.json()
                    neighbor_leafs = []
                    neighbor_leaf_map = {}
                    for node in topo_data.get('nodeList'):
                        node_data = node.get('data')
                        if node_data.get('logicalName') == name:
                            continue
                        if node_data.get('switchRole') != 'leaf':
                            continue
                        neighbor_leaf_map[node_data.get('logicalName')] = node_data.get('serialNumber')
                        #neighbor_leafs.append(neighbor_leaf_map) 
                    tor_leaf_intf_map = {}
                    for edge in topo_data.get('edgeList'):
                        edge_data = edge.get('data')
                        nbr_switch = edge_data.get('toSwitch')
                        nbr_interface = edge_data.get('toInterface')
                        tor_leaf_intf_map[nbr_switch] = nbr_interface
                    sw_dct['tor_leaf_nodes'] = neighbor_leaf_map
                    sw_dct['tor_leaf_intf'] = tor_leaf_intf_map
                #switches_map[snum] = sw_dct
                switches_map[ip] = sw_dct
        else:
            log.error("invalid result for get_switches status %(status)s",
                    {'status': res.status_code})
        return switches_map

    def get_switches(self, fabric):
        '''
        Top level function for retrieving the switches.
        '''
        sw_info = []
        try:
            ret = self.login()
            if ret:
                sw_info = self._get_switches(fabric)
                self.logout()
        except Exception as exc:
            log.error("Exception in get_switches, %(exc)s", {exc:exc})
        return sw_info

    @http_exc_handler
    def _get_po(self, snum, ifname):
        url = self._build_url(self._interface_url) + snum + "&ifName=" + ifname + "&ifTypes=INTERFACE_ETHERNET,INTERFACE_PORT_CHANNEL"
        res = requests.get(url, headers=self._req_headers,
                           timeout=self._timeout_resp, verify=False)
        if res and res.status_code in self._resp_ok:
            data = res.json()
            for intf in data:
                if intf.get('ifName') == ifname and intf.get('ifType') == "INTERFACE_ETHERNET":
                    po = intf.get('channelIdStr')
                    return po
        else:
            log.error("invalid result for get_po status %(status)s", {'status': res.status_code})
        return ""

    def get_po(self, snum, ifname):
        '''
        Top level function for retrieving PO.
        '''
        po = ""
        try:
            ret = self.login()
            if ret:
                po = self._get_po(snum, ifname)
                self.logout()
        except Exception as exc:
            log.error("Exception in get_po, %(exc)s", {exc:exc})
        return po
