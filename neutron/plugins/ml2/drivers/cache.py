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

from keystoneauth1.identity import v3
from keystoneauth1 import session
from keystoneclient import auth as ksc_auth
from keystoneclient import session as ksc_session
from keystoneclient.v3 import client as ksc_client
from neutronclient.neutron.v2_0 import purge
from neutronclient.v2_0 import client as neutron_client
from oslo_config import cfg
from oslo_log import log as logging


LOG = logging.getLogger(__name__)


class ProjectDetailsCache(object):
    """Cache of Keystone project ID to project details mappings."""

    def __init__(self):
        self.project_details = {}
        self.keystone = None
        self.neutron = None

    def get_auth(self):
        auth = v3.Password(auth_url=cfg.CONF.keystone_authtoken.auth_url ,
                           username=cfg.CONF.keystone_authtoken.username,
                           password=cfg.CONF.keystone_authtoken.password,
                           project_name=cfg.CONF.keystone_authtoken.project_name,
                           user_domain_name=cfg.CONF.keystone_authtoken.user_domain_name,
                           project_domain_name=cfg.CONF.keystone_authtoken.project_domain_name)
        return auth

    def _get_keystone_client(self):
        LOG.debug("Getting keystone client")
        res = [{k:v} for k, v in cfg.CONF.items()]
        LOG.debug("res: %s", res)
        auth = self.get_auth()
        LOG.debug("Got auth: %s", auth)
        sess = session.Session(auth=auth)
        LOG.debug("Got session: %s", sess)
        self.keystone = ksc_client.Client(session=sess)
        LOG.debug("Got keystone client: %s", self.keystone)
        endpoint_type = 'publicURL'
        self.neutron = neutron_client.Client(session=session,
                endpoint_type=endpoint_type)

    def ensure_project(self, project_id):
        """Ensure cache contains mapping for project.

        :param project_id: ID of the project

        Ensure that the cache contains a mapping for the project
        identified by project_id. If it is not, Keystone will be
        queried for the current list of projects, and any new mappings
        will be added to the cache. This method should never be called
        inside a transaction with a project_id not already in the
        cache.
        """
        if project_id and project_id not in self.project_details:
            self.load_projects()

    def load_projects(self):
        if self.keystone is None:
            self._get_keystone_client()
        LOG.debug("Calling project API")
        projects = self.keystone.projects.list()
        LOG.debug("Received projects: %s", projects)
        for project in projects:
            self.project_details[project.id] = (project.name,
                project.description)

    def get_project_details(self, project_id):
        """Get name and descr of project from cache.

        :param project_id: ID of the project

        If the cache contains project_id, a tuple with
        project name and description is returned
        else a tuple (None,None) is returned
        """
        if self.project_details.get(project_id):
            return self.project_details[project_id]
        return ('', '')

    def purge_prj(self, project_id):
        class TempArg(object):
            pass

        self._get_keystone_client()
        LOG.debug("Calling purge() API")
        temp_arg = TempArg()
        temp_arg.tenant = project_id
        neutron_purge = PurgeAPI(None, None, self.neutron)
        neutron_purge.take_action(temp_arg)


class PurgeAPI(purge.Purge):
    def __init__(self, app, app_args, neutron_client):
        self.neutron_client = neutron_client
        super(PurgeAPI, self).__init__(app, app_args)

    def get_client(self):
        return self.neutron_client
