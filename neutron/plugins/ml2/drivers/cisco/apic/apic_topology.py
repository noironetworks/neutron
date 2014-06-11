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

import eventlet
import re

from oslo.config import cfg

from neutron.agent.common import config
from neutron.agent.linux import ip_lib
from neutron.agent.linux import utils
from neutron.common import rpc as neutron_rpc
from neutron.common import utils as neutron_utils
from neutron.db import agents_db
from neutron import manager
from neutron.openstack.common import lockutils
from neutron.openstack.common import log as logging
from neutron.openstack.common import periodic_task
from neutron.openstack.common import rpc
from neutron.openstack.common import service as svc
from neutron.plugins.ml2.drivers.cisco.apic import mechanism_apic
from neutron import service

ACI_PORT_DESCR_FORMATS = [
    'topology/pod-1/node-(\d+)/sys/conng/path-\[eth(\d+)/(\d+)\]',
    'topology/pod-1/paths-(\d+)/pathep-\[eth(\d+)/(\d+)\]',
]
AGENT_FORCE_UPDATE_COUNT = 100
BINARY_APIC_SERVICE_AGENT = 'neutron-cisco-apic-service-agent'
BINARY_APIC_HOST_AGENT = 'neutron-cisco-apic-host-agent'
TOPIC_APIC_SERVICE = 'apic-service'
TYPE_APIC_SERVICE_AGENT = 'cisco-apic-service-agent'
TYPE_APIC_HOST_AGENT = 'cisco-apic-host-agent'


LOG = logging.getLogger(__name__)


class ApicTopologyService(manager.Manager):

    RPC_API_VERSION = '1.1'

    def __init__(self, host=None):
        if host is None:
            host = neutron_utils.get_hostname()
        super(ApicTopologyService, self).__init__(host=host)

        self.conf = cfg.CONF.ml2_cisco_apic
        self.conn = None
        self.peers = {}
        self.dispatcher = None
        self.state = None
        self.state_agent = None
        self.topic = TOPIC_APIC_SERVICE
        self.apic_manager = \
            mechanism_apic.APICMechanismDriver.get_apic_manager()

    def init_host(self):
        LOG.info(_("APIC service agent starting ..."))

        ###self.state_agent = agent_rpc.PluginReportStateAPI(self.topic)
        self.state = {
            'binary': BINARY_APIC_SERVICE_AGENT,
            'host': self.host,
            'topic': self.topic,
            'configurations': {},
            'start_flag': True,
            'agent_type': TYPE_APIC_SERVICE_AGENT,
        }

        self.conn = rpc.create_connection(new=True)
        self.dispatcher = neutron_rpc.PluginRpcDispatcher(
            [self, agents_db.AgentExtRpcCallback()])
        self.conn.create_consumer(
            self.topic, self.dispatcher, fanout=True)
        self.conn.consume_in_thread()

    def after_start(self):
        LOG.info(_("APIC service agent started"))

    def report_send(self, context):
        if not self.state_agent:
            return
        LOG.debug(_("APIC service agent: sending report state"))

        try:
            self.state_agent.report_state(context, self.state)
            self.state.pop('start_flag', None)
        except AttributeError:
            # This means the server does not support report_state
            # ignore it
            return
        except Exception:
            LOG.exception(_("APIC service agent: failed in reporting state"))

    @lockutils.synchronized('apic_service')
    def update_link(self, context,
                    host, interface, mac,
                    switch, module, port):
        LOG.debug(_("APIC service agent: received update_link: %s"),
                  ", ".join([host, interface, mac, switch, module, port]))

        nlink = (host, interface, mac, switch, module, port)
        clink = self.peers.get((host, interface), None)

        if switch == 0:
            # this is a link delete, remove it
            if clink is not None:
                self.apic_manager.remove_hostlink(*clink)
                self.peers.pop((host, interface))
        else:
            if clink is None:
                # add new link to database
                self.apic_manager.add_hostlink(*nlink)
                self.peers[(host, interface)] = nlink
            elif clink != nlink:
                # delete old link and add new one (don't update in place)
                self.apic_manager.remove_hostlink(*clink)
                self.peers.pop((host, interface))
                self.apic_manager.add_hostlink(*nlink)
                self.peers[(host, interface)] = nlink


class ApicTopologyServiceNotifierApi(rpc.proxy.RpcProxy):

    RPC_API_VERSION = '1.1'

    def __init__(self):
        super(ApicTopologyServiceNotifierApi, self).__init__(
            topic=TOPIC_APIC_SERVICE,
            default_version=self.RPC_API_VERSION)

    def update_link(self, context, host, interface, mac, switch, module, port):
        self.fanout_cast(
            context, self.make_msg(
                'update_link',
                host=host, interface=interface, mac=mac,
                switch=switch, module=module, port=port),
            topic=TOPIC_APIC_SERVICE)

    def delete_link(self, context, host, interface):
        self.fanout_cast(
            context, self.make_msg(
                'delete_link',
                host=host, interface=interface, mac=None,
                switch=0, module=0, port=0),
            topic=TOPIC_APIC_SERVICE)


class ApicTopologyAgent(manager.Manager):
    def __init__(self, host=None):
        if host is None:
            host = neutron_utils.get_hostname()
        super(ApicTopologyAgent, self).__init__(host=host)

        self.conf = cfg.CONF.ml2_cisco_apic
        self.count_current = 0
        self.count_force_send = AGENT_FORCE_UPDATE_COUNT
        self.interfaces = {}
        self.lldpcmd = None
        self.peers = {}
        self.port_desc_re = map(re.compile, ACI_PORT_DESCR_FORMATS)
        self.root_helper = self.conf.root_helper
        self.service_agent = ApicTopologyServiceNotifierApi()
        self.state = None
        self.state_agent = None
        self.topic = TOPIC_APIC_SERVICE
        self.uplink_ports = []

    def init_host(self):
        LOG.info(_("APIC host agent: agent starting on %s"), self.host)

        ###self.state_agent = agent_rpc.PluginReportStateAPI(self.topic)
        self.state = {
            'binary': BINARY_APIC_HOST_AGENT,
            'host': self.host,
            'topic': self.topic,
            'configurations': {},
            'start_flag': True,
            'agent_type': TYPE_APIC_HOST_AGENT,
        }

        self.uplink_ports = []
        for inf in self.conf.apic_host_uplink_ports:
            if ip_lib.device_exists(inf):
                self.uplink_ports.append(inf)
            else:
                # ignore unknown interfaces
                LOG.error(_("No such interface (ignored): %s"), inf)
        self.lldpcmd = ['lldpctl', '-f', 'keyvalue'] + self.uplink_ports

    def after_start(self):
        LOG.info(_("APIC host agent: started on %s"), self.host)

    @periodic_task.periodic_task
    def _check_for_new_peers(self, context):
        LOG.debug(_("APIC host agent: _check_for_new_peers"))

        if not self.lldpcmd:
            return
        try:
            # Check if we must send update even if there is no change
            force_send = False
            self.count_current += 1
            if self.count_current >= self.count_force_send:
                force_send = True
                self.count_current = 0

            # Make a copy of self.peers
            old_peers = {}
            for interface in self.peers:
                old_peers[interface] = self.peers[interface]

            # Check for lldp peers
            lldpkeys = utils.execute(self.lldpcmd, self.root_helper)
            for line in lldpkeys.split('\n'):
                if not line or '=' not in line:
                    continue
                fqkey, value = line.split('=', 1)
                lldp, interface, key = fqkey.split('.', 2)
                if key == 'port.descr':
                    for regexp in self.port_desc_re:
                        match = regexp.match(value)
                        if match:
                            mac = self._get_mac(interface)
                            if interface in old_peers:
                                old_peers.pop(interface)
                            switch, module, port = match.group(1, 2, 3)
                            peer = (self.host, interface, mac,
                                    switch, module, port)
                            if force_send or interface not in self.peers or \
                                    self.peers[interface] != peer:
                                self.service_agent.update_link(context, *peer)
                                self.peers[interface] = peer
            if old_peers:
                for interface in old_peers:
                    olink = old_peers[interface]
                    self.service_agent.update_link(
                        context, olink[0], olink[1], None, 0, 0, 0)
        except Exception:
            LOG.exception(_("APIC service agent: exception in LLDP parsing"))

    def _get_mac(self, interface):
        mac = None
        if interface in self.interfaces:
            return self.interfaces[interface]
        try:
            mac = ip_lib.IPDevice(interface).link.address
            self.interfaces[interface] = mac
        except Exception:
            # we can safely ignore it, it is only needed for debugging
            LOG.exception(_("APIC service agent: can not get MACaddr for %s"),
                          interface)
        return mac

    def report_send(self, context):
        if not self.state_agent:
            return
        LOG.debug(_("APIC host agent: sending report state"))

        try:
            self.state_agent.report_state(context, self.state)
            self.state.pop('start_flag', None)
        except AttributeError:
            # This means the server does not support report_state
            # ignore it
            return
        except Exception:
            LOG.exception(_("APIC host agent: failed in reporting state"))


def launch(binary, manager, topic=None):
    eventlet.monkey_patch()
    cfg.CONF(project='neutron')
    config.setup_logging(cfg.CONF)
    report_period = cfg.CONF.ml2_cisco_apic.apic_agent_report_interval
    poll_period = cfg.CONF.ml2_cisco_apic.apic_agent_poll_interval
    server = service.Service.create(
        binary=binary, manager=manager, topic=topic,
        report_interval=report_period, periodic_interval=poll_period)
    svc.launch(server).wait()


def service_main():
    launch(
        BINARY_APIC_SERVICE_AGENT,
        'neutron.plugins.ml2.drivers.' +
        'cisco.apic.apic_topology.ApicTopologyService',
        TOPIC_APIC_SERVICE)


def agent_main():
    launch(
        BINARY_APIC_HOST_AGENT,
        'neutron.plugins.ml2.drivers.' +
        'cisco.apic.apic_topology.ApicTopologyAgent')
