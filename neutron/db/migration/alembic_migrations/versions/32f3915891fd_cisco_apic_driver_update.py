# Copyright 2014 OpenStack Foundation
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

"""Cisco APIC Mechanism Driver

Revision ID: 32f3915891fd
Revises: 1b837a7125a9
Create Date: 2014-04-23 09:27:08.177021

"""

# revision identifiers, used by Alembic.
revision = '32f3915891fd'
down_revision = '1b837a7125a9'

migration_for_plugins = [
    'neutron.plugins.ml2.plugin.Ml2Plugin'
]

from alembic import op
import sqlalchemy as sa

from neutron.db import migration


def upgrade(active_plugins=None, options=None):
    if not migration.should_run(active_plugins, migration_for_plugins):
        return

    op.drop_table('cisco_ml2_apic_contracts')
    op.drop_table('cisco_ml2_apic_port_profiles')
    op.drop_table('cisco_ml2_apic_epgs')

    op.create_table(
        'cisco_ml2_apic_host_links',
        sa.Column('host', sa.String(length=255), nullable=False),
        sa.Column('ifname', sa.String(length=64), nullable=False),
        sa.Column('ifmac', sa.String(length=32), nullable=True),
        sa.Column('swid', sa.String(length=32), nullable=False),
        sa.Column('module', sa.String(length=32), nullable=False),
        sa.Column('port', sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint('host', 'ifname'))

    op.create_table(
        'cisco_ml2_apic_namemap',
        sa.Column('neutron_id', sa.String(length=36), nullable=False),
        sa.Column('neutron_type', sa.String(length=32), nullable=False),
        sa.Column('apic_name', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('neutron_id', 'neutron_type'))

    op.create_table(
        'cisco_ml2_apic_contracts',
        sa.Column('tenant_id', sa.String(length=255), nullable=False),
        sa.Column('router_id', sa.String(length=64), nullable=False),
        sa.Column('contract_id', sa.String(length=64), nullable=False),
        sa.Column('filter_id', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('router_id'))


def downgrade(active_plugins=None, options=None):
    if not migration.should_run(active_plugins, migration_for_plugins):
        return

    op.drop_table('cisco_ml2_apic_config')
    op.drop_table('cisco_ml2_apic_names')
    op.drop_table('cisco_ml2_apic_host_links')
    op.drop_table('cisco_ml2_apic_contracts')

    op.create_table(
        'cisco_ml2_apic_epgs',
        sa.Column('network_id', sa.String(length=255), nullable=False),
        sa.Column('epg_id', sa.String(length=64), nullable=False),
        sa.Column('segmentation_id', sa.String(length=64), nullable=False),
        sa.Column('provider', sa.Boolean(), default=False, nullable=False),
        sa.PrimaryKeyConstraint('network_id'))

    op.create_table(
        'cisco_ml2_apic_port_profiles',
        sa.Column('node_id', sa.String(length=255), nullable=False),
        sa.Column('profile_id', sa.String(length=64), nullable=False),
        sa.Column('hpselc_id', sa.String(length=64), nullable=False),
        sa.Column('module', sa.String(length=10), nullable=False),
        sa.Column('from_port', sa.Integer(), nullable=False),
        sa.Column('to_port', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('node_id'))

    op.create_table(
        'cisco_ml2_apic_contracts',
        sa.Column('tenant_id', sa.String(length=255), nullable=False),
        sa.Column('contract_id', sa.String(length=64), nullable=False),
        sa.Column('filter_id', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id'))
