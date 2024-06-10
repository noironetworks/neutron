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

from oslo_config import cfg

from neutron._i18n import _


ndfc_opts = [
    cfg.StrOpt('keystone_notification_exchange',
               default='keystone',
               help=("The exchange used to subscribe to Keystone "
                     "notifications")),
    cfg.StrOpt('keystone_notification_topic',
               default='notifications',
               help=("The topic used to subscribe to Keystone "
                     "notifications")),
    cfg.StrOpt('keystone_notification_pool',
               default=None,
               help=("The pool used to subscribe to Keystone "
                     "notifications. This value should only be configured "
                     "to a value other than 'None' when there are other "
                     "notification listeners subscribed to the same "
                     "keystone exchange and topic, whose pool is set "
                     "to 'None'.")),
    cfg.StrOpt('ndfc_ip',
               default="",
               help=("The IP address of the NDFC host.")),
    cfg.StrOpt('user',
               default="",
               help=("The username for logging in to the NDFC host.")),
    cfg.StrOpt('pwd',
               default="",
               help=("The password for logging in to the NDFC host.")),
    cfg.StrOpt('fabric_name',
                default="",
                help=("Fabric name")),
]


def register_opts():
    cfg.CONF.register_opts(ndfc_opts, group='ndfc')
