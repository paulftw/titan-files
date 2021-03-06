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

"""Tests for microversions.py."""

from tests.common import testing

from google.appengine.api import files as blobstore_files
from google.appengine.api import users
from google.appengine.datastore import datastore_stub_util
from titan.common.lib.google.apputils import basetest
from titan.files import files
from titan.services import microversions
from titan.services import versions

# Content larger than the task invocation RPC size limit.
LARGE_FILE_CONTENT = 'a' * (1 << 21)  # 2 MiB

class MicroversionsTest(testing.ServicesTestCase):

  def setUp(self):
    super(MicroversionsTest, self).setUp()
    services = (
        'titan.services.microversions',
        'titan.services.versions',
    )
    self.EnableServices(services)
    self.vcs = versions.VersionControlService()

  def testHooks(self):
    # Exists() should check root tree files, not _FilePointers.
    self.assertFalse(files.Exists('/foo'))
    files.Touch('/foo', disabled_services=True)
    self.assertTrue(files.Exists('/foo'))

    # Get() should pull from the root tree.
    files.Write('/foo', 'foo', disabled_services=True)
    self.assertEqual('foo', files.Get('/foo').content)
    files.Delete('/foo', 'foo', disabled_services=True)
    self.assertEqual(None, files.Get('/foo'))

    # Write(), Touch(), and Delete() should all modify root tree files
    # and defer a versioning task which commits a single-file changeset.
    files.Write('/foo', 'foo')
    self.assertEqual(1, len(self.taskqueue_stub.get_filtered_tasks()))
    self.assertEqual('foo', files.DeprecatedFile('/foo').content)
    files.Touch('/foo')
    self.assertEqual(2, len(self.taskqueue_stub.get_filtered_tasks()))
    self.assertEqual('foo', files.DeprecatedFile('/foo').content)
    files.Delete('/foo')
    self.assertEqual(3, len(self.taskqueue_stub.get_filtered_tasks()))
    self.assertEqual(None, files._File.get_by_key_name('/foo'))

    # Verify large RPC deferred task handling.
    files.Write('/foo', LARGE_FILE_CONTENT)
    # TODO(user): right now, these are dropped. When handled later,
    # there will be 4 items in the queue, not 3.
    self.assertEqual(3, len(self.taskqueue_stub.get_filtered_tasks()))

  def testCommitMicroversion(self):
    created_by = users.User('test@example.com')

    # Write.
    final_changeset = microversions._CommitMicroversion(
        created_by=created_by, write=True, path='/foo', content='foo')
    self.assertEqual(2, final_changeset.num)
    file_obj = files.Get('/foo', changeset=final_changeset.linked_changeset)
    self.assertEqual('foo', file_obj.content)

    # Verify the final changeset's created_by.
    self.assertEqual('test@example.com', str(final_changeset.created_by))

    # Write with an existing root file (which should be copied to the version).
    files.Write('/foo', 'new foo', disabled_services=True)
    final_changeset = microversions._CommitMicroversion(
        created_by=created_by, write=True, path='/foo', meta={'color': 'blue'})
    self.assertEqual(4, final_changeset.num)
    file_obj = files.Get('/foo', changeset=final_changeset.linked_changeset)
    self.assertEqual('new foo', file_obj.content)
    self.assertEqual('blue', file_obj.color)

    # Touch.
    final_changeset = microversions._CommitMicroversion(
        created_by=created_by, touch=True, paths='/foo')
    self.assertEqual(6, final_changeset.num)
    file_obj = files.Get('/foo', changeset=final_changeset.linked_changeset)
    self.assertEqual('new foo', file_obj.content)

    # Delete. In the real code path, the delete of the root file will often
    # complete before the task is started, so we delete /foo to verify that
    # deletes don't rely on presence of the root file.
    files.Delete('/foo', disabled_services=True)
    final_changeset = microversions._CommitMicroversion(
        created_by=created_by, delete=True, paths='/foo')
    self.assertEqual(8, final_changeset.num)
    file_obj = files.Get('/foo', changeset=final_changeset.linked_changeset)
    self.assertEqual('', file_obj.content)

    # Check file versions.
    file_versions = self.vcs.GetFileVersions('/foo')
    self.assertEqual(8, file_versions[0].changeset.num)
    self.assertEqual(6, file_versions[1].changeset.num)
    self.assertEqual(4, file_versions[2].changeset.num)
    self.assertEqual(2, file_versions[3].changeset.num)
    self.assertEqual(versions.FILE_DELETED, file_versions[0].status)
    self.assertEqual(versions.FILE_EDITED, file_versions[1].status)
    self.assertEqual(versions.FILE_EDITED, file_versions[2].status)
    self.assertEqual(versions.FILE_CREATED, file_versions[3].status)

    # Touch multi.
    final_changeset = microversions._CommitMicroversion(
        created_by=created_by, touch=True, paths=['/foo', '/bar'])
    self.assertEqual(10, final_changeset.num)
    file_objs = files.Get(['/foo', '/bar'],
                          changeset=final_changeset.linked_changeset)
    self.assertEqual(2, len(file_objs))

    # Delete multi.
    final_changeset = microversions._CommitMicroversion(
        created_by=created_by, delete=True, paths=['/foo', '/bar'])
    self.assertEqual(12, final_changeset.num)
    file_objs = files.Get(['/foo', '/bar'],
                          changeset=final_changeset.linked_changeset)
    self.assertEqual(versions.FILE_DELETED, file_objs['/foo'].status)
    self.assertEqual(versions.FILE_DELETED, file_objs['/bar'].status)

  def testStronglyConsistentCommits(self):
    created_by = users.User('test@example.com')

    # Microversions use FinalizeAssociatedPaths so the Commit() path should use
    # the always strongly-consistent GetFiles(), rather than a query. Verify
    # this behavior by simulating a never-consistent HR datastore.
    policy = datastore_stub_util.PseudoRandomHRConsistencyPolicy(probability=0)
    self.testbed.init_datastore_v3_stub(consistency_policy=policy)

    final_changeset = microversions._CommitMicroversion(
        created_by=created_by, write=True, path='/foo', content='foo')
    self.assertEqual(2, final_changeset.num)
    file_obj = files.Get('/foo', changeset=final_changeset.linked_changeset)
    self.assertEqual('foo', file_obj.content)

  def testKeepOldBlobs(self):
    # Create a blob and blob_reader for testing.
    filename = blobstore_files.blobstore.create(
        mime_type='application/octet-stream')
    with blobstore_files.open(filename, 'a') as fp:
      fp.write('Blobstore!')
    blobstore_files.finalize(filename)
    blob_key = blobstore_files.blobstore.get_blob_key(filename)

    # Verify that the blob is not deleted when microversioned content resizes.
    files.Write('/foo', blob=blob_key)
    self._RunDeferredTasks(microversions.SERVICE_NAME)
    file_obj = files.Get('/foo')
    self.assertTrue(file_obj.blob)
    self.assertEqual('Blobstore!', file_obj.content)
    self._RunDeferredTasks(microversions.SERVICE_NAME)
    # Resize as smaller (shouldn't delete the old blob).
    files.Write('/foo', 'foo')
    files.Write('/foo', blob=blob_key)  # Resize back to large size.
    # Delete file (shouldn't delete the old blob).
    files.Delete('/foo')
    self._RunDeferredTasks(microversions.SERVICE_NAME)

    file_versions = self.vcs.GetFileVersions('/foo')

    # Deleted file (blob should be None).
    changeset = file_versions[0].changeset.linked_changeset
    file_obj = files.Get('/foo', changeset=changeset)
    self.assertIsNone(file_obj.blob)

    # Created file (blob key and blob content should still exist).
    changeset = file_versions[-1].changeset.linked_changeset
    file_obj = files.Get('/foo', changeset=changeset)
    self.assertTrue(file_obj.blob)
    self.assertEqual('Blobstore!', file_obj.content)

if __name__ == '__main__':
  basetest.main()
