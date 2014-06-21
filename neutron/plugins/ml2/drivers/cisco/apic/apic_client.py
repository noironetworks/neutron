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
# @author: Henry Gessau, Cisco Systems

from collections import deque
from collections import namedtuple
import time

import json
import requests
import requests.exceptions as rexc

from neutron.openstack.common import log as logging
from neutron.plugins.ml2.drivers.cisco.apic import exceptions as cexc


LOG = logging.getLogger(__name__)

APIC_CODE_FORBIDDEN = str(requests.codes.forbidden)

FALLBACK_EXCEPTIONS = (rexc.ConnectionError, rexc.Timeout,
                       rexc.TooManyRedirects, rexc.InvalidURL)

# Info about a Managed Object's relative name (RN) and container.
class ManagedObjectName(namedtuple('MoPath',
                                   ['container', 'rn_fmt', 'can_create'])):
    def __new__(cls, container, rn_fmt, can_create=True):
        return super(ManagedObjectName, cls).__new__(cls, container, rn_fmt,
                                                     can_create)


class ManagedObjectClass(object):

    """Information about a Managed Object (MO) class.

    Constructs and keeps track of the distinguished name (DN) and relative
    name (RN) of a managed object (MO) class. The DN is the RN of the MO
    appended to the recursive RNs of its containers, i.e.:
        DN = uni/container-RN/.../container-RN/object-RN

    Also keeps track of whether the MO can be created in the APIC, as some
    MOs are read-only or used for specifying relationships.
    """

    supported_mos = {
        'fvTenant': ManagedObjectName(None, 'tn-%s'),
        'fvBD': ManagedObjectName('fvTenant', 'BD-%s'),
        'fvRsBd': ManagedObjectName('fvAEPg', 'rsbd'),
        'fvSubnet': ManagedObjectName('fvBD', 'subnet-[%s]'),
        'fvCtx': ManagedObjectName('fvTenant', 'ctx-%s'),
        'fvRsCtx': ManagedObjectName('fvBD', 'rsctx'),
        'fvAp': ManagedObjectName('fvTenant', 'ap-%s'),
        'fvAEPg': ManagedObjectName('fvAp', 'epg-%s'),
        'fvRsProv': ManagedObjectName('fvAEPg', 'rsprov-%s'),
        'fvRsCons': ManagedObjectName('fvAEPg', 'rscons-%s'),
        'fvRsConsIf': ManagedObjectName('fvAEPg', 'rsconsif-%s'),
        'fvRsDomAtt': ManagedObjectName('fvAEPg', 'rsdomAtt-[%s]'),
        'fvRsPathAtt': ManagedObjectName('fvAEPg', 'rspathAtt-[%s]'),

        'vzAny': ManagedObjectName('fvCtx', 'any'),
        'vzRsAnyToCons': ManagedObjectName('vzAny', 'rsanyToCons-%s'),
        'vzRsAnyToProv': ManagedObjectName('vzAny', 'rsanyToProv-%s'),
        'vzBrCP': ManagedObjectName('fvTenant', 'brc-%s'),
        'vzSubj': ManagedObjectName('vzBrCP', 'subj-%s'),
        'vzFilter': ManagedObjectName('fvTenant', 'flt-%s'),
        'vzRsFiltAtt': ManagedObjectName('vzSubj', 'rsfiltAtt-%s'),
        'vzEntry': ManagedObjectName('vzFilter', 'e-%s'),
        'vzInTerm': ManagedObjectName('vzSubj', 'intmnl'),
        'vzRsFiltAtt__In': ManagedObjectName('vzInTerm', 'rsfiltAtt-%s'),
        'vzOutTerm': ManagedObjectName('vzSubj', 'outtmnl'),
        'vzRsFiltAtt__Out': ManagedObjectName('vzOutTerm', 'rsfiltAtt-%s'),
        'vzRsSubjFiltAtt': ManagedObjectName('vzSubj', 'rssubjFiltAtt-%s'),
        'vzCPIf': ManagedObjectName('fvTenant', 'cif-%s'),
        'vzRsIf': ManagedObjectName('vzCPIf', 'rsif'),

        'l3extOut': ManagedObjectName('fvTenant', 'out-%s'),
        'l3extRsEctx': ManagedObjectName('l3extOut', 'rsectx'),
        'l3extLNodeP': ManagedObjectName('l3extOut', 'lnodep-%s'),
        'l3extRsNodeL3OutAtt': ManagedObjectName('l3extLNodeP',
                                                 'rsnodeL3OutAtt-[%s]'),
        'ipRouteP': ManagedObjectName('l3extRsNodeL3OutAtt', 'rt-[%s]'),
        'ipNexthopP': ManagedObjectName('ipRouteP', 'nh-[%s]'),
        'l3extLIfP': ManagedObjectName('l3extLNodeP', 'lifp-%s'),
        'l3extRsPathL3OutAtt': ManagedObjectName('l3extLIfP',
                                                 'rspathL3OutAtt-[%s]'),
        'l3extInstP': ManagedObjectName('l3extOut', 'instP-%s'),
        'fvRsCons__Ext': ManagedObjectName('l3extInstP', 'rscons-%s'),
        'fvRsProv__Ext': ManagedObjectName('l3extInstP', 'rsprov-%s'),
        'fvCollectionCont': ManagedObjectName('fvRsCons', 'collectionDn-[%s]'),
        'l3extSubnet': ManagedObjectName('l3extInstP', 'extsubnet-[%s]'),

        'vmmProvP': ManagedObjectName(None, 'vmmp-%s', False),
        'vmmDomP': ManagedObjectName('vmmProvP', 'dom-%s'),
        'vmmEpPD': ManagedObjectName('vmmDomP', 'eppd-[%s]'),

        'physDomP': ManagedObjectName(None, 'phys-%s'),

        'infra': ManagedObjectName(None, 'infra'),
        'infraNodeP': ManagedObjectName('infra', 'nprof-%s'),
        'infraLeafS': ManagedObjectName('infraNodeP', 'leaves-%s-typ-%s'),
        'infraNodeBlk': ManagedObjectName('infraLeafS', 'nodeblk-%s'),
        'infraRsAccPortP': ManagedObjectName('infraNodeP', 'rsaccPortP-[%s]'),
        'infraAccPortP': ManagedObjectName('infra', 'accportprof-%s'),
        'infraHPortS': ManagedObjectName('infraAccPortP', 'hports-%s-typ-%s'),
        'infraPortBlk': ManagedObjectName('infraHPortS', 'portblk-%s'),
        'infraRsAccBaseGrp': ManagedObjectName('infraHPortS', 'rsaccBaseGrp'),
        'infraFuncP': ManagedObjectName('infra', 'funcprof'),
        'infraAccPortGrp': ManagedObjectName('infraFuncP', 'accportgrp-%s'),
        'infraRsAttEntP': ManagedObjectName('infraAccPortGrp', 'rsattEntP'),
        'infraAttEntityP': ManagedObjectName('infra', 'attentp-%s'),
        'infraRsDomP': ManagedObjectName('infraAttEntityP', 'rsdomP-[%s]'),
        'infraRsVlanNs': ManagedObjectName('physDomP', 'rsvlanNs'),

        'fvnsVlanInstP': ManagedObjectName('infra', 'vlanns-%s-%s'),
        'fvnsEncapBlk__vlan': ManagedObjectName('fvnsVlanInstP',
                                                'from-%s-to-%s'),
        'fvnsVxlanInstP': ManagedObjectName('infra', 'vxlanns-%s'),
        'fvnsEncapBlk__vxlan': ManagedObjectName('fvnsVxlanInstP',
                                                 'from-%s-to-%s'),

        # Fabric
        'fabric': ManagedObjectName(None, 'fabric', False),
        'bgpInstPol': ManagedObjectName('fabric', 'bgpInstP-%s'),
        'bgpRRP': ManagedObjectName('bgpInstPol', 'rr'),
        'bgpRRNodePEp': ManagedObjectName('bgpRRP', 'node-%s'),
        'bgpAsP': ManagedObjectName('bgpInstPol', 'as'),

        'funcprof': ManagedObjectName('fabric', 'funcprof', False),
        'fabricPodPGrp': ManagedObjectName('funcprof', 'podpgrp-%s'),
        'fabricRsPodPGrpBGPRRP': ManagedObjectName('fabricPodPGrp',
                                                   'rspodPGrpBGPRRP'),

        'fabricPodP': ManagedObjectName('fabric', 'podprof-default'),
        'fabricPodS__ALL': ManagedObjectName('fabricPodP', 'pods-%s-typ-ALL'),
        'fabricRsPodPGrp': ManagedObjectName('fabricPodS__ALL', 'rspodPGrp'),

        # Read-only
        'fabricTopology': ManagedObjectName(None, 'topology', False),
        'fabricPod': ManagedObjectName('fabricTopology', 'pod-%s', False),
        'fabricPathEpCont': ManagedObjectName('fabricPod', 'paths-%s', False),
        'fabricPathEp': ManagedObjectName('fabricPathEpCont', 'pathep-%s',
                                          False),
        'fabricNode': ManagedObjectName('fabricPod', 'node-%s', False),
    }

    # Note(Henry): The use of a mutable default argument _inst_cache is
    # intentional. It persists for the life of MoClass to cache instances.
    # noinspection PyDefaultArgument
    def __new__(cls, mo_class, _inst_cache={}):
        """Ensure we create only one instance per mo_class."""
        try:
            return _inst_cache[mo_class]
        except KeyError:
            new_inst = super(ManagedObjectClass, cls).__new__(cls)
            new_inst.__init__(mo_class)
            _inst_cache[mo_class] = new_inst
            return new_inst

    def __init__(self, mo_class):
        self.klass = mo_class
        self.klass_name = mo_class.split('__')[0]
        mo = self.supported_mos[mo_class]
        self.container = mo.container
        self.rn_fmt = mo.rn_fmt
        self.dn_fmt, self.params = self._dn_fmt()
        self.param_count = self.dn_fmt.count('%s')
        rn_has_param = self.rn_fmt.count('%s')
        self.can_create = rn_has_param and mo.can_create

    def _dn_fmt(self):
        """Build the distinguished name format using container and RN.

        DN = uni/container-RN/.../container-RN/object-RN

        Also make a list of the required parameters.
        Note: Call this method only once at init.
        """
        param = [self.klass] if '%s' in self.rn_fmt else []
        if self.container:
            container = ManagedObjectClass(self.container)
            dn_fmt = '%s/%s' % (container.dn_fmt, self.rn_fmt)
            params = container.params + param
            return dn_fmt, params
        return 'uni/%s' % self.rn_fmt, param

    def dn(self, *params):
        """Return the distinguished name for a managed object."""
        return self.dn_fmt % params


class ApicSession(object):

    """Manages a session with the APIC."""

    def __init__(self, hosts, port, usr, pwd, ssl):
        protocol = ssl and 'https' or 'http'
        self.api_base = deque(['%s://%s:%s/api' % (protocol, host, port)
                               for host in hosts])
        self.session = requests.Session()
        self.session_deadline = 0
        self.session_timeout = 0
        self.cookie = {}

        # Log in
        self.authentication = None
        self.username = None
        self.password = None
        if usr and pwd:
            self.login(usr, pwd)

    def _do_request(self, request, url, *args, **kwargs):
        """Use this method to wrap all the http requests"""
        for _ in range(len(self.api_base)):
            try:
                return request(self.api_base[0] + url, *args, **kwargs)
            except FALLBACK_EXCEPTIONS as ex:
                LOG.debug('%s, falling back to a '
                          'new address' % ex.message)
                self.api_base.rotate(-1)
                LOG.debug('New controller address: %s ' % self.api_base[0])
        return request(self.api_base[0] + url, *args, **kwargs)

    @staticmethod
    def _make_data(key, **attrs):
        """Build the body for a msg out of a key and some attributes."""
        return json.dumps({key: {'attributes': attrs}})

    def _api_url(self, api):
        """Create the URL for a generic API."""
        return '/%s.json' % api

    def _mo_url(self, mo, *args):
        """Create a URL for a MO lookup by DN."""
        dn = mo.dn(*args)
        return '/mo/%s.json' % dn

    def _qry_url(self, mo):
        """Create a URL for a query lookup by MO class."""
        return '/class/%s.json' % mo.klass_name

    def _subtree_url(self, mo, *args, **kwargs):
        cfilter = kwargs.get('cfilter')
        return self._mo_url(mo, *args) + \
            '?query-target=children&%srsp-subtree=full' % \
            ('target-subtree-class=' + cfilter + '&' if cfilter else '')

    def _bulid_target_filter(self, mo, **kwargs):
        """Creates an 'and(eq(), eq(), ...)' filter for requests."""
        filt = ', '.join(['eq(%s.%s, \"%s\")' %
                          (mo.klass_name, key, kwargs[key]) for key in kwargs])
        return '' if not filt else 'query-target-filter=and(%s)' % filt

    def _check_session(self):
        """Check that we are logged in and ensure the session is active."""
        if not self.authentication:
            raise cexc.ApicSessionNotLoggedIn
        if time.time() > self.session_deadline:
            self.refresh()

    def _send(self, request, url, data=None, refreshed=None):
        """Send a request and process the response."""
        if data is None:
            response = self._do_request(request, url, cookies=self.cookie)
        else:
            response = self._do_request(request, url, data=data,
                                        cookies=self.cookie)
        if response is None:
            raise cexc.ApicHostNoResponse(url=url)
        # Every request refreshes the timeout
        self.session_deadline = time.time() + self.session_timeout
        if data is None:
            request_str = url
        else:
            request_str = '%s, data=%s' % (url, data)
            LOG.debug(_("data = %s"), data)
        # imdata is where the APIC returns the useful information
        imdata = response.json().get('imdata')
        LOG.debug(_("Response: %s"), imdata)
        if response.status_code != requests.codes.ok:
            try:
                err_code = imdata[0]['error']['attributes']['code']
                err_text = imdata[0]['error']['attributes']['text']
            except (IndexError, KeyError):
                err_code = '[code for APIC error not found]'
                err_text = '[text for APIC error not found]'
            # If invalid token then re-login and retry once
            if (not refreshed and err_code == APIC_CODE_FORBIDDEN and
                    err_text.lower().startswith('token was invalid')):
                self.login()
                return self._send(request, url, data=data, refreshed=True)
            raise cexc.ApicResponseNotOk(request=request_str,
                                         status=response.status_code,
                                         reason=response.reason,
                                         err_text=err_text, err_code=err_code)
        return imdata

    # REST requests

    def get_data(self, request):
        """Retrieve generic data from the server."""
        self._check_session()
        url = self._api_url(request)
        return self._send(self.session.get, url)

    def get_mo(self, mo, *args):
        """Retrieve a managed object by its distinguished name."""
        self._check_session()
        url = self._mo_url(mo, *args) + '?query-target=self'
        return self._send(self.session.get, url)

    def get_mo_subtree(self, mo, *args, **kwargs):
        self._check_session()
        url = self._subtree_url(mo, *args, **kwargs)
        return self._send(self.session.get, url)

    def list_mo(self, mo, **kwargs):
        """Retrieve the list of managed objects for a class."""
        self._check_session()
        url = self._qry_url(mo) + '?' + self._bulid_target_filter(mo, **kwargs)
        return self._send(self.session.get, url)

    def post_data(self, request, data):
        """Post generic data to the server."""
        self._check_session()
        url = self._api_url(request)
        return self._send(self.session.post, url, data=data)

    def post_mo(self, mo, *args, **data):
        """Post data for a managed object to the server."""
        self._check_session()
        url = self._mo_url(mo, *args)
        data = self._make_data(mo.klass_name, **data)
        return self._send(self.session.post, url, data=data)

    # Session management

    def _save_cookie(self, request, response):
        """Save the session cookie and its expiration time."""
        imdata = response.json().get('imdata')
        if response.status_code == requests.codes.ok:
            attributes = imdata[0]['aaaLogin']['attributes']
            if 'token' not in attributes:
                raise cexc.ApicResponseNoCookie(request=request)
            self.cookie = {'APIC-Cookie': attributes['token']}
            timeout = int(attributes['refreshTimeoutSeconds'])
            LOG.debug(_("APIC session will expire in %d seconds"), timeout)
            # Give ourselves a few seconds to refresh before timing out
            self.session_timeout = timeout - 5
            self.session_deadline = time.time() + self.session_timeout
        else:
            attributes = imdata[0]['error']['attributes']
        return attributes

    def login(self, usr=None, pwd=None):
        """Log in to controller. Save user name and authentication."""
        usr = usr or self.username
        pwd = pwd or self.password
        name_pwd = self._make_data('aaaUser', name=usr, pwd=pwd)
        url = self._api_url('aaaLogin')
        self.cookie = {}

        try:
            response = self._do_request(self.session.post, url, data=name_pwd,
                                        timeout=10.0)
        except rexc.Timeout:
            raise cexc.ApicHostNoResponse(url=url)
        attributes = self._save_cookie('aaaLogin', response)
        if response.status_code == requests.codes.ok:
            self.username = usr
            self.password = pwd
            self.authentication = attributes
        else:
            self.authentication = None
            raise cexc.ApicResponseNotOk(request=url,
                                         status=response.status_code,
                                         reason=response.reason,
                                         err_text=attributes['text'],
                                         err_code=attributes['code'])

    def refresh(self):
        """Called when a session has timed out or almost timed out."""
        url = self._api_url('aaaRefresh')
        response = self._do_request(self.session.get, url,
                                    cookies=self.cookie)
        attributes = self._save_cookie('aaaRefresh', response)
        if response.status_code == requests.codes.ok:
            # We refreshed before the session timed out.
            self.authentication = attributes
        else:
            err_code = attributes['code']
            err_text = attributes['text']
            if (err_code == APIC_CODE_FORBIDDEN and
                    err_text.lower().startswith('token was invalid')):
                # This means the token timed out, so log in again.
                LOG.debug(_("APIC session timed-out, logging in again."))
                self.login()
            else:
                self.authentication = None
                raise cexc.ApicResponseNotOk(request=url,
                                             status=response.status_code,
                                             reason=response.reason,
                                             err_text=err_text,
                                             err_code=err_code)

    def logout(self):
        """End session with controller."""
        if not self.username:
            self.authentication = None
        if self.authentication:
            data = self._make_data('aaaUser', name=self.username)
            self.post_data('aaaLogout', data=data)
        self.authentication = None


class ManagedObjectAccess(object):

    """CRUD operations on APIC Managed Objects."""

    def __init__(self, session, mo_class):
        self.session = session
        self.mo = ManagedObjectClass(mo_class)

    def _create_container(self, *params):
        """Recursively create all container objects."""
        if self.mo.container:
            container = ManagedObjectAccess(self.session, self.mo.container)
            if container.mo.can_create:
                container_params = params[0: container.mo.param_count]
                container._create_container(*container_params)
                container.session.post_mo(container.mo, *container_params)

    def create(self, *params, **attrs):
        self._create_container(*params)
        if self.mo.can_create and 'status' not in attrs:
            attrs['status'] = 'created'
        return self.session.post_mo(self.mo, *params, **attrs)

    def _mo_attributes(self, obj_data):
        if (self.mo.klass_name in obj_data and
                'attributes' in obj_data[self.mo.klass_name]):
            return obj_data[self.mo.klass_name]['attributes']

    def get(self, *params):
        """Return a dict of the MO's attributes, or None."""
        imdata = self.session.get_mo(self.mo, *params)
        if imdata:
            return self._mo_attributes(imdata[0])

    def get_subtree(self, *params, **kwargs):
        return self.session.get_mo_subtree(self.mo, *params, **kwargs)

    def list_all(self, **kwargs):
        imdata = self.session.list_mo(self.mo, **kwargs)
        return filter(None, [self._mo_attributes(obj) for obj in imdata])

    def list_names(self, **kwargs):
        return [obj['name'] for obj in self.list_all(**kwargs)]

    def update(self, *params, **attrs):
        return self.session.post_mo(self.mo, *params, **attrs)

    def delete(self, *params):
        return self.session.post_mo(self.mo, *params, status='deleted')


class RestClient(ApicSession):

    """APIC REST client for OpenStack Neutron."""

    def __init__(self, hosts, port=80, usr=None, pwd=None, ssl=False):
        """Establish a session with the APIC."""
        super(RestClient, self).__init__(hosts, port, usr, pwd, ssl)

        # TODO(Henry): Instantiate supported MOs on demand instead of
        #              creating all of them up front here.
        # Supported objects for OpenStack Neutron
        for mo_class in ManagedObjectClass.supported_mos:
            self.__dict__[mo_class] = ManagedObjectAccess(self, mo_class)
