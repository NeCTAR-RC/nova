# Copyright 2015 OpenStack Foundation
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

from oslo_config import cfg

api_group = cfg.OptGroup('api',
    title='API options',
    help="""
Options under this group are used to define Nova API.
""")

auth_opts = [
    cfg.StrOpt("auth_strategy",
        default="keystone",
        choices=[
            ("keystone", "Use keystone for authentication."),
            ("noauth2", "Designed for testing only, as it does no actual "
             "credential checking. 'noauth2' provides administrative "
             "credentials only if 'admin' is specified as the username."),
        ],
        deprecated_for_removal=True,
        deprecated_since='21.0.0',
        deprecated_reason="""
The only non-default choice, ``noauth2``, is for internal development and
testing purposes only and should not be used in deployments. This option and
its middleware, NoAuthMiddleware[V2_18], will be removed in a future release.
""",
        help="""
Determine the strategy to use for authentication.
"""),
]

metadata_opts = [
    cfg.StrOpt("config_drive_skip_versions",
        default=("1.0 2007-01-19 2007-03-01 2007-08-29 2007-10-10 "
                 "2007-12-15 2008-02-01 2008-09-01"),
        deprecated_group="DEFAULT",
        help="""
When gathering the existing metadata for a config drive, the EC2-style
metadata is returned for all versions that don't appear in this option.
As of the Liberty release, the available versions are:

* 1.0
* 2007-01-19
* 2007-03-01
* 2007-08-29
* 2007-10-10
* 2007-12-15
* 2008-02-01
* 2008-09-01
* 2009-04-04

The option is in the format of a single string, with each version separated
by a space.

Possible values:

* Any string that represents zero or more versions, separated by spaces.
"""),
    cfg.ListOpt('vendordata_providers',
        item_type=cfg.types.String(choices=[
            ('StaticJSON', 'Load a JSON file from the path configured by '
             '``vendordata_jsonfile_path`` and use this as the source for '
             '``vendor_data.json`` and ``vendor_data2.json``.'),
            ('DynamicJSON', 'Build a JSON file using values defined in '
             '``vendordata_dynamic_targets`` and use this as the source '
             'for ``vendor_data2.json``.'),
        ]),
        default=['StaticJSON'],
        deprecated_group="DEFAULT",
        help="""
A list of vendordata providers.

vendordata providers are how deployers can provide metadata via configdrive
and metadata that is specific to their deployment.

For more information on the requirements for implementing a vendordata
dynamic endpoint, please see the vendordata.rst file in the nova developer
reference.

Related options:

* ``vendordata_dynamic_targets``
* ``vendordata_dynamic_ssl_certfile``
* ``vendordata_dynamic_connect_timeout``
* ``vendordata_dynamic_read_timeout``
* ``vendordata_dynamic_failure_fatal``
"""),
    cfg.ListOpt('vendordata_dynamic_targets',
        default=[],
        deprecated_group="DEFAULT",
        help="""
A list of targets for the dynamic vendordata provider. These targets are of
the form ``<name>@<url>``.

The dynamic vendordata provider collects metadata by contacting external REST
services and querying them for information about the instance. This behaviour
is documented in the vendordata.rst file in the nova developer reference.
"""),
    cfg.StrOpt('vendordata_dynamic_ssl_certfile',
        default='',
        deprecated_group="DEFAULT",
        help="""
Path to an optional certificate file or CA bundle to verify dynamic
vendordata REST services ssl certificates against.

Possible values:

* An empty string, or a path to a valid certificate file

Related options:

* vendordata_providers
* vendordata_dynamic_targets
* vendordata_dynamic_connect_timeout
* vendordata_dynamic_read_timeout
* vendordata_dynamic_failure_fatal
"""),
    cfg.IntOpt('vendordata_dynamic_connect_timeout',
        default=5,
        min=3,
        deprecated_group="DEFAULT",
        help="""
Maximum wait time for an external REST service to connect.

Possible values:

* Any integer with a value greater than three (the TCP packet retransmission
  timeout). Note that instance start may be blocked during this wait time,
  so this value should be kept small.

Related options:

* vendordata_providers
* vendordata_dynamic_targets
* vendordata_dynamic_ssl_certfile
* vendordata_dynamic_read_timeout
* vendordata_dynamic_failure_fatal
"""),
    cfg.IntOpt('vendordata_dynamic_read_timeout',
        default=5,
        min=0,
        deprecated_group="DEFAULT",
        help="""
Maximum wait time for an external REST service to return data once connected.

Possible values:

* Any integer. Note that instance start is blocked during this wait time,
  so this value should be kept small.

Related options:

* vendordata_providers
* vendordata_dynamic_targets
* vendordata_dynamic_ssl_certfile
* vendordata_dynamic_connect_timeout
* vendordata_dynamic_failure_fatal
"""),
    cfg.BoolOpt('vendordata_dynamic_failure_fatal',
        default=False,
        help="""
Should failures to fetch dynamic vendordata be fatal to instance boot?

Related options:

* vendordata_providers
* vendordata_dynamic_targets
* vendordata_dynamic_ssl_certfile
* vendordata_dynamic_connect_timeout
* vendordata_dynamic_read_timeout
"""),
    cfg.IntOpt("metadata_cache_expiration",
        default=15,
        min=0,
        deprecated_group="DEFAULT",
        help="""
This option is the time (in seconds) to cache metadata. When set to 0,
metadata caching is disabled entirely; this is generally not recommended for
performance reasons. Increasing this setting should improve response times
of the metadata API when under heavy load. Higher values may increase memory
usage, and result in longer times for host metadata changes to take effect.
"""),
    cfg.BoolOpt("local_metadata_per_cell",
                default=False,
                help="""
Indicates that the nova-metadata API service has been deployed per-cell, so
that we can have better performance and data isolation in a multi-cell
deployment. Users should consider the use of this configuration depending on
how neutron is setup. If you have networks that span cells, you might need to
run nova-metadata API service globally. If your networks are segmented along
cell boundaries, then you can run nova-metadata API service per cell. When
running nova-metadata API service per cell, you should also configure each
Neutron metadata-agent to point to the corresponding nova-metadata API
service.
"""),
    cfg.StrOpt("dhcp_domain",
        deprecated_group="DEFAULT",
        default="novalocal",
        help="""
Domain name used to configure FQDN for instances.

Configure a fully-qualified domain name for instance hostnames. The value is
suffixed to the instance hostname from the database to construct the hostname
that appears in the metadata API. To disable this behavior (for example in
order to correctly support microversion's 2.94 FQDN hostnames), set this to the
empty string.

Possible values:

* Any string that is a valid domain name.
"""),
]

file_opts = [
    cfg.StrOpt("vendordata_jsonfile_path",
        deprecated_group="DEFAULT",
        help="""
Cloud providers may store custom data in vendor data file that will then be
available to the instances via the metadata service, and to the rendering of
config-drive. The default class for this, JsonFileVendorData, loads this
information from a JSON file, whose path is configured by this option. If
there is no path set by this option, the class returns an empty dictionary.

Note that when using this to provide static vendor data to a configuration
drive, the nova-compute service must be configured with this option and the
file must be accessible from the nova-compute host.

Possible values:

* Any string representing the path to the data file, or an empty string
  (default).
""")
]

osapi_opts = [
    cfg.IntOpt("max_limit",
        default=1000,
        min=0,
        deprecated_group="DEFAULT",
        deprecated_name="osapi_max_limit",
        help="""
As a query can potentially return many thousands of items, you can limit the
maximum number of items in a single response by setting this option.
"""),
    cfg.StrOpt("compute_link_prefix",
        deprecated_group="DEFAULT",
        deprecated_name="osapi_compute_link_prefix",
        help="""
This string is prepended to the normal URL that is returned in links to the
OpenStack Compute API. If it is empty (the default), the URLs are returned
unchanged.

Possible values:

* Any string, including an empty string (the default).
"""),
    cfg.StrOpt("glance_link_prefix",
        deprecated_group="DEFAULT",
        deprecated_name="osapi_glance_link_prefix",
        help="""
This string is prepended to the normal URL that is returned in links to
Glance resources. If it is empty (the default), the URLs are returned
unchanged.

Possible values:

* Any string, including an empty string (the default).
"""),
    cfg.BoolOpt("instance_list_per_project_cells",
        default=False,
        help="""
When enabled, this will cause the API to only query cell databases
in which the tenant has mapped instances. This requires an additional
(fast) query in the API database before each list, but also
(potentially) limits the number of cell databases that must be queried
to provide the result. If you have a small number of cells, or tenants
are likely to have instances in all cells, then this should be
False. If you have many cells, especially if you confine tenants to a
small subset of those cells, this should be True.
"""),
    cfg.StrOpt("instance_list_cells_batch_strategy",
        default="distributed",
        choices=[
            ("distributed", "Divide the "
             "limit requested by the user by the number of cells in the "
             "system. This requires counting the cells in the system "
             "initially, which will not be refreshed until service restart "
             "or SIGHUP. The actual batch size will be increased by 10% "
             "over the result of ($limit / $num_cells)."),
            ("fixed", "Request fixed-size batches from each cell, as defined "
             "by ``instance_list_cells_batch_fixed_size``. "
             "If the limit is smaller than the batch size, the limit "
             "will be used instead. If you do not wish batching to be used "
             "at all, setting the fixed size equal to the ``max_limit`` "
             "value will cause only one request per cell database to be "
             "issued."),
        ],
        help="""
This controls the method by which the API queries cell databases in
smaller batches during large instance list operations. If batching is
performed, a large instance list operation will request some fraction
of the overall API limit from each cell database initially, and will
re-request that same batch size as records are consumed (returned)
from each cell as necessary. Larger batches mean less chattiness
between the API and the database, but potentially more wasted effort
processing the results from the database which will not be returned to
the user. Any strategy will yield a batch size of at least 100 records,
to avoid a user causing many tiny database queries in their request.

Related options:

* instance_list_cells_batch_fixed_size
* max_limit
"""),
    cfg.IntOpt("instance_list_cells_batch_fixed_size",
        min=100,
        default=100,
        help="""
This controls the batch size of instances requested from each cell
database if ``instance_list_cells_batch_strategy``` is set to ``fixed``.
This integral value will define the limit issued to each cell every time
a batch of instances is requested, regardless of the number of cells in
the system or any other factors. Per the general logic called out in
the documentation for ``instance_list_cells_batch_strategy``, the
minimum value for this is 100 records per batch.

Related options:

* instance_list_cells_batch_strategy
* max_limit
"""),
    cfg.BoolOpt("list_records_by_skipping_down_cells",
        default=True,
        help="""
When set to False, this will cause the API to return a 500 error if there is an
infrastructure failure like non-responsive cells. If you want the API to skip
the down cells and return the results from the up cells set this option to
True.

Note that from API microversion 2.69 there could be transient conditions in the
deployment where certain records are not available and the results could be
partial for certain requests containing those records. In those cases this
option will be ignored. See "Handling Down Cells" section of the Compute API
guide (https://docs.openstack.org/api-guide/compute/down_cells.html) for
more information.
"""),
    cfg.IntOpt("cell_failure_hold_seconds",
        default=0,
        min=0,
        help="""
Short-lived, in-process circuit breaker for the cross-cell scatter-gather
utility. When set to a positive integer, a cell that fails (raises an
exception or times out) during a scatter-gather operation is recorded for
this many seconds; subsequent requests touching that cell short-circuit with
the did_not_respond sentinel instead of re-paying the per-cell timeout.

The cache is local to each process that runs scatter-gather operations:
each nova-api worker keeps its own, and if this option is set for other
services that scatter across cells (nova-scheduler, nova-conductor) their
processes each keep one too - in the scheduler a held cell's hosts are
skipped for the whole hold window, so only set it there deliberately.
The cache is purely additive: a
successful response from a cell immediately clears its failure mark. When
the hold window expires, the cell is re-tested by a single probe request
(single-flight): exactly one request is let through to query the cell while
every other request keeps short-circuiting, until the probe either succeeds
(the mark is cleared) or fails (a new hold window starts). This prevents a
stampede of requests from piling onto a still-dead cell each time the hold
expires.

Set to 0 (default) to preserve the legacy behavior of attempting every cell
on every request. Typical production values are 30-60 seconds, balancing
fast failure during outages against quick recovery when a cell is restored.

Related options:

* list_records_by_skipping_down_cells
"""),
    cfg.IntOpt("cell_timeout",
        default=60,
        min=1,
        help="""
The total time in seconds to wait for cell databases to respond during
cross-cell operations (the scatter-gather utility). A cell that does not
respond within this time is treated as down and reported with the usual
down-cell sentinel. This timeout also governs the per-cell queries made by
multi-cell listings and by the scheduler.

Note that a single API request can wait on several scatter-gather
operations in sequence (for example a server list detail request performs
an instance list plus a block device mapping gather), so the worst-case
request duration is a small multiple of this value. Size it well below the
front-end proxy or load balancer timeout so requests still complete
degraded within the client's budget when a cell is down.

Related options:

* cell_failure_hold_seconds
* list_records_by_skipping_down_cells
"""),
]

os_network_opts = [
    cfg.BoolOpt("use_neutron_default_nets",
        default=False,
        deprecated_group="DEFAULT",
        help="""
When True, the TenantNetworkController will query the Neutron API to get the
default networks to use.

Related options:

* neutron_default_tenant_id
"""),
    cfg.StrOpt("neutron_default_tenant_id",
        default="default",
        deprecated_group="DEFAULT",
        help="""
Tenant ID for getting the default network from Neutron API (also referred in
some places as the 'project ID') to use.

Related options:

* use_neutron_default_nets
"""),
]

enable_inst_pw_opts = [
    cfg.BoolOpt("enable_instance_password",
        default=True,
        deprecated_group="DEFAULT",
        help="""
Enables returning of the instance password by the relevant server API calls
such as create, rebuild, evacuate, or rescue. If the hypervisor does not
support password injection, then the password returned will not be correct,
so if your hypervisor does not support password injection, set this to False.
""")
]

API_OPTS = (auth_opts +
            metadata_opts +
            file_opts +
            osapi_opts +
            os_network_opts +
            enable_inst_pw_opts)


def register_opts(conf):
    conf.register_group(api_group)
    conf.register_opts(API_OPTS, group=api_group)


def list_opts():
    return {api_group: API_OPTS}
