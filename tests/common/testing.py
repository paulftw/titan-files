#!/usr/bin/env python
# Copyright 2011 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Base test case classes for App Engine."""

import base64
import cStringIO
import datetime
import os
import shutil
import types
import urlparse
import mox
from mox import stubout
from google.appengine.datastore import datastore_stub_util
from google.appengine.ext import deferred
from google.appengine.ext import testbed
import gflags as flags
from titan.common.lib.google.apputils import basetest
from google.appengine.api import apiproxy_stub_map
from google.appengine.api.blobstore import blobstore_stub
from google.appengine.api.blobstore import file_blob_storage
from google.appengine.api.files import file_service_stub
from google.appengine.api.search import simple_search_stub
from tests.common import webapp_testing
from titan.common import hooks
from titan.files import client
from titan.files import files_cache
from titan.files import handlers
from google.appengine.runtime import request_environment
from tests.common import appengine_rpc_test_util

def DisableCaching(func):
  """Decorator for disabling the files_cache module for a test case."""

  def Wrapper(self, *args, **kwargs):
    # Stub out each function in files_cache.
    func_return_none = lambda *args, **kwargs: None
    for attr in dir(files_cache):
      if isinstance(getattr(files_cache, attr), types.FunctionType):
        self.stubs.Set(files_cache, attr, func_return_none)

    # Stub special packed return from files_cache.GetFiles.
    self.stubs.Set(files_cache, 'GetFiles',
                   lambda *args, **kwargs: (None, False))
    func(self, *args, **kwargs)

  return Wrapper

class MockableTestCase(basetest.TestCase):
  """Base test case supporting stubs and mox."""

  def setUp(self):
    self.stubs = stubout.StubOutForTesting()
    self.mox = mox.Mox()

  def tearDown(self):
    self.stubs.SmartUnsetAll()
    self.stubs.UnsetAll()
    self.mox.UnsetStubs()
    self.mox.ResetAll()

class BaseTestCase(MockableTestCase):
  """Base test case for tests requiring Datastore, Memcache, or Blobstore."""

  def setUp(self, enable_environ_patch=True):
    # Evil os-environ patching which mirrors dev_appserver and production.
    # This patch turns os.environ into a thread-local object, which also happens
    # to support storing more than just strings. This patch must come first.
    self.enable_environ_patch = enable_environ_patch
    if self.enable_environ_patch:
      self._old_os_environ = os.environ.copy()
      request_environment.current_request.Clear()
      request_environment.PatchOsEnviron()
      os.environ.update(self._old_os_environ)

    # Manually setup blobstore and files stubs (until supported in testbed).
    #
    # Setup base blobstore service stubs.
    self.appid = os.environ['APPLICATION_ID'] = 'testbed-test'
    apiproxy_stub_map.apiproxy = apiproxy_stub_map.APIProxyStubMap()
    storage_directory = os.path.join(flags.FLAGS.test_tmpdir, 'blob_storage')
    if os.access(storage_directory, os.F_OK):
      shutil.rmtree(storage_directory)
    blob_storage = file_blob_storage.FileBlobStorage(
        storage_directory, self.appid)
    self.blobstore_stub = blobstore_stub.BlobstoreServiceStub(blob_storage)
    apiproxy_stub_map.apiproxy.RegisterStub('blobstore', self.blobstore_stub)

    # Setup blobstore files service stubs.
    apiproxy_stub_map.apiproxy.RegisterStub(
        'file', file_service_stub.FileServiceStub(blob_storage))
    file_service_stub._now_function = datetime.datetime.now

    # Setup the simple search stub.
    self.search_stub = simple_search_stub.SearchServiceStub()
    apiproxy_stub_map.apiproxy.RegisterStub('search', self.search_stub)

    # Must come after the apiproxy stubs above.
    self.testbed = testbed.Testbed()
    self.testbed.activate()
    # Fake an always strongly-consistent HR datastore.
    policy = datastore_stub_util.PseudoRandomHRConsistencyPolicy(probability=1)
    self.testbed.init_datastore_v3_stub(consistency_policy=policy)
    self.testbed.init_memcache_stub()
    # All task queues must be specified in common/queue.yaml.
    self.testbed.init_taskqueue_stub(root_path=os.path.dirname(__file__),
                                     _all_queues_valid=True)
    self.testbed.setup_env(
        app_id=self.appid,
        user_email='titanuser@example.com',
        user_id='1',
        overwrite=True,
        http_host='localhost:8080',
    )
    self.taskqueue_stub = self.testbed.get_stub(testbed.TASKQUEUE_SERVICE_NAME)

    super(BaseTestCase, self).setUp()

  def tearDown(self):
    if self.enable_environ_patch:
      os.environ = self._old_os_environ
    self.testbed.deactivate()
    super(BaseTestCase, self).tearDown()

  def assertEntityEqual(self, ent, other_ent, ignore=None):
    """Assert equality of properties and dynamic properties of two entities.

    Args:
      ent: First entity.
      other_ent: Second entity.
      ignore: A list of strings of properties to ignore in equality checking.
    Raises:
      AssertionError: if either entity is None.
    """
    if not ent or not other_ent:
      raise AssertionError('%r != %r' % (ent, other_ent))
    if not self._EntityEquals(ent, other_ent):
      properties = self._GetDereferencedProperties(ent)
      properties.update(ent._dynamic_properties)
      other_properties = self._GetDereferencedProperties(other_ent)
      other_properties.update(other_ent._dynamic_properties)
      if ignore:
        for item in ignore:
          if item in properties:
            del properties[item]
          if item in other_properties:
            del other_properties[item]
      self.assertDictEqual(properties, other_properties)

  def assertEntitiesEqual(self, entities, other_entities):
    """Assert that two iterables of entities have the same elements."""
    self.assertEqual(len(entities), len(other_entities))
    # Compare properties of all entities in O(n^2) (but usually 0 < n < 10).
    for ent in entities:
      found = False
      for other_ent in other_entities:
        # Shortcut: display debug if we expect the two entities to be equal:
        if ent.key() == other_ent.key():
          self.assertEntityEqual(ent, other_ent)
        if self._EntityEquals(ent, other_ent):
          found = True
      if not found:
        raise AssertionError('%s not found in %s' % (ent, other_entities))

  def assertNdbEntityEqual(self, ent, other_ent, ignore=None):
    """Assert equality of properties and dynamic properties of two ndb entities.

    Args:
      ent: First ndb entity.
      other_ent: Second ndb entity.
      ignore: A list of strings of properties to ignore in equality checking.
    Raises:
      AssertionError: if either entity is None.
    """
    ent_properties = {}
    other_ent_properties = {}
    for key, prop in ent._properties.iteritems():
      if key not in ignore:
        ent_properties[key] = prop._get_value(ent)
    for key, prop in other_ent._properties.iteritems():
      if key not in ignore:
        other_ent_properties[key] = prop._get_value(other_ent)
    self.assertDictEqual(ent_properties, other_ent_properties)

  def assertSameObjects(self, objs, other_objs):
    """Assert that two lists' objects are equal."""
    self.assertEqual(len(objs), len(other_objs),
                     'Not equal!\nFirst: %s\nSecond: %s' % (objs, other_objs))
    for obj in objs:
      if obj not in other_objs:
        raise AssertionError('%s not found in %s' % (obj, other_objs))

  def _EntityEquals(self, ent, other_ent):
    """Compares entities by comparing their properties and keys."""
    props = self._GetDereferencedProperties(ent)
    other_props = self._GetDereferencedProperties(other_ent)
    return (ent.key().name() == other_ent.key().name()
            and props == other_props
            and ent._dynamic_properties == other_ent._dynamic_properties)

  def _GetDereferencedProperties(self, ent):
    """Directly get properties since they don't dereference nicely."""
    # ent.properties() contains lazy-loaded objects which are always equal even
    # if their actual contents are different. Dereference all the references!
    props = {}
    for key in ent.properties():
      props[key] = ent.__dict__['_' + key]
    return props

  def _RunDeferredTasks(self, queue):
    tasks = self.taskqueue_stub.GetTasks(queue)
    for task in tasks:
      deferred.run(base64.b64decode(task['body']))
    self.taskqueue_stub.FlushQueue(queue)

class TitanClientStub(appengine_rpc_test_util.TestRpcServer,
                      client.TitanClient):
  """Mocks out RPC openers for Titan."""

  def __init__(self, *args, **kwargs):
    super(client.TitanClient, self).__init__(*args, **kwargs)
    self.handlers = dict(handlers.URL_MAP)  # {'/path': Handler}
    for url_path in self.handlers:
      self.opener.AddResponse(
          'https://%s%s' % (args[0], url_path), self.HandleRequest)

  def ValidateClientAuth(self, test=False):
    # Mocked out entirely, but testable by calling with test=True.
    if test:
      super(TitanClientStub, self).ValidateClientAuth()

  def HandleRequest(self, request):
    """Handles Titan requests by passing to the appropriate webapp handler.

    Args:
      request: A urllib2.Request object.
    Returns:
      A appengine_rpc_test_util.TestRpcServer.MockResponse object.
    """
    url = urlparse.urlparse(request.get_full_url())
    environ = webapp_testing.WebAppTestCase.GetDefaultEnvironment()
    method = request.get_method()

    if method == 'GET':
      environ['QUERY_STRING'] = url.query
    elif method == 'POST':
      environ['REQUEST_METHOD'] = 'POST'
      post_data = request.get_data()
      environ['wsgi.input'] = cStringIO.StringIO(post_data)
      environ['CONTENT_LENGTH'] = len(post_data)

    handler_class = self.handlers.get(url.path)
    if not handler_class:
      return self.MockResponse('Not found: %s' % url.path, code=404)

    handler = webapp_testing.WebAppTestCase.CreateRequestHandler(
        handler_factory=handler_class, env=environ)
    if method == 'GET':
      handler.get()
    elif method == 'POST':
      handler.post()
    else:
      raise NotImplementedError('%s method is not supported' % method)
    result = self.MockResponse(handler.response.out.getvalue(),
                               code=handler.response.status,
                               headers=handler.response.headers)
    return result

class ServicesTestCase(BaseTestCase):
  """Base test class for Titan service tests."""

  def tearDown(self):
    hooks._global_hooks = {}
    hooks._global_service_configs = {}
    hooks._global_services_order = []
    super(ServicesTestCase, self).tearDown()

  def EnableServices(self, services):
    """Let tests define a custom set of TITAN_SERVICES."""
    hooks.LoadServices(services)

  def SetServiceConfig(self, service_name, config):
    """Proxy to set a config object for a given service."""
    hooks.SetServiceConfig(service_name, config)
