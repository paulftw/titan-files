#!/usr/bin/env python
# Copyright 2012 Google Inc. All Rights Reserved.
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

"""Handlers for Titan Files versions service."""

import json
import logging

import webapp2

from titan.common import utils
from titan.files import files
from titan.files.mixins import versions

class AbstractBaseHandler(webapp2.RequestHandler):
  """Abstract base handler."""

  def WriteJsonResponse(self, data, **kwargs):
    """Data to serialize. Accepts keyword args to pass to the encoder."""
    self.response.headers['Content-Type'] = 'application/json'
    json_data = json.dumps(data, cls=utils.CustomJsonEncoder, **kwargs)
    self.response.out.write(json_data)

class ChangesetHandler(AbstractBaseHandler):
  """RESTful handler for versions.Changeset."""

  def post(self):
    """POST handler."""
    try:
      vcs = versions.VersionControlService()
      changeset = vcs.NewStagingChangeset()
      self.WriteJsonResponse(changeset)
      self.response.set_status(201)
    except (TypeError, ValueError):
      self.error(400)
      logging.exception('Bad request:')

class ChangesetCommitHandler(AbstractBaseHandler):
  """RESTful handler for VersionControlService.Commit."""

  def post(self):
    """POST handler."""
    try:
      vcs = versions.VersionControlService()
      staging_changeset = versions.Changeset(int(self.request.get('changeset')))
      force = bool(self.request.get('force', False))
      manifest = self.request.POST.get('manifest', None)
      if not force and not manifest or force and manifest:
        self.error(400)
        logging.error('Exactly one of "manifest" or "force" params is required')
        return

      # If a client has full knowledge of the files uploaded to a changeset,
      # the "manifest" param may be given to ensure a strongly consistent
      # commit. If given, associate the files to the changeset and finalize it.
      if manifest:
        manifest = json.loads(manifest)
        for path in manifest:
          titan_file = files.File(path, changeset=staging_changeset)
          staging_changeset.AssociateFile(titan_file)
        staging_changeset.FinalizeAssociatedFiles()

      final_changeset = vcs.Commit(staging_changeset, force=force)
      self.WriteJsonResponse(final_changeset)
      self.response.set_status(201)
    except (TypeError, ValueError):
      self.error(400)
      logging.exception('Bad request:')

application = webapp2.WSGIApplication((
    ('/_titan/files/versions/changeset', ChangesetHandler),
    ('/_titan/files/versions/changeset/commit', ChangesetCommitHandler),
), debug=False)
