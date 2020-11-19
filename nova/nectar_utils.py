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

from keystoneauth1 import session
from keystoneclient.v3 import client

from nova import cache_utils
from nova import exception
from nova import service_auth


CACHE_SECONDS = 60 * 60
MC = None


def _get_cache():
    global MC

    if MC is None:
        MC = cache_utils.get_client(expiration_time=CACHE_SECONDS)

    return MC


def get_project(context):
    cache = _get_cache()
    project_id = context.project_id
    cache_key = 'project-%s' % project_id
    project = cache.get(cache_key)
    if not project:
        auth_plugin = service_auth.get_auth_plugin(context)
        if not auth_plugin:
            raise exception.Unauthorized()
        sess = session.Session(auth=auth_plugin)
        project = client.Client(session=sess).projects.get(project_id)
        cache.set(cache_key, project)
    return project
