##
# Copyright (c) 2009 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

from twistedcaldav.test.util import TestCase
from twistedcaldav.directory.appleopendirectory import buildQueries, OpenDirectoryService
import dsattributes

class BuildQueryTests(TestCase):

    def test_buildQuery(self):
        self.assertEquals(
            buildQueries(
                [dsattributes.kDSStdRecordTypeUsers],
                (
                    ("firstName", "morgen", True, "starts-with"),
                    ("lastName", "sagen", True, "starts-with"),
                ),
                OpenDirectoryService._ODFields
            ),
            {
                (
                    ('dsAttrTypeStandard:FirstName', 'morgen', True, 'starts-with'),
                    ('dsAttrTypeStandard:LastName', 'sagen', True, 'starts-with')
                ): ['dsRecTypeStandard:Users']
            }
        )
        self.assertEquals(
            buildQueries(
                [
                    dsattributes.kDSStdRecordTypeUsers,
                    dsattributes.kDSStdRecordTypePlaces
                ],
                (
                    ("firstName", "morgen", True, "starts-with"),
                    ("emailAddresses", "morgen", True, "contains"),
                ),
                OpenDirectoryService._ODFields
            ),
            {
                (
                    ('dsAttrTypeStandard:FirstName', 'morgen', True, 'starts-with'),
                    ('dsAttrTypeStandard:EMailAddress', 'morgen', True, 'contains'),
                ): ['dsRecTypeStandard:Users'],
                (): ['dsRecTypeStandard:Places']
            }
        )
        self.assertEquals(
            buildQueries(
                [
                    dsattributes.kDSStdRecordTypeGroups,
                    dsattributes.kDSStdRecordTypePlaces
                ],
                (
                    ("firstName", "morgen", True, "starts-with"),
                    ("lastName", "morgen", True, "starts-with"),
                    ("fullName", "morgen", True, "starts-with"),
                    ("emailAddresses", "morgen", True, "contains"),
                ),
                OpenDirectoryService._ODFields
            ),
            {
                (
                    ('dsAttrTypeStandard:RealName', 'morgen', True, 'starts-with'),
                    ('dsAttrTypeStandard:EMailAddress', 'morgen', True, 'contains'),
                ): ['dsRecTypeStandard:Groups'],
                (
                    ('dsAttrTypeStandard:RealName', 'morgen', True, 'starts-with'),
                ): ['dsRecTypeStandard:Places']
            }
        )
        self.assertEquals(
            buildQueries(
                [
                    dsattributes.kDSStdRecordTypeUsers,
                    dsattributes.kDSStdRecordTypeGroups,
                    dsattributes.kDSStdRecordTypeResources,
                    dsattributes.kDSStdRecordTypePlaces
                ],
                (
                    ("firstName", "morgen", True, "starts-with"),
                    ("lastName", "morgen", True, "starts-with"),
                    ("fullName", "morgen", True, "starts-with"),
                    ("emailAddresses", "morgen", True, "contains"),
                ),
                OpenDirectoryService._ODFields
            ),
            {
                (
                    ('dsAttrTypeStandard:RealName', 'morgen', True, 'starts-with'),
                    ('dsAttrTypeStandard:EMailAddress', 'morgen', True, 'contains')
                ): ['dsRecTypeStandard:Groups'],
                (
                    ('dsAttrTypeStandard:RealName', 'morgen', True, 'starts-with'),
                ): ['dsRecTypeStandard:Resources', 'dsRecTypeStandard:Places'],
                (
                    ('dsAttrTypeStandard:FirstName', 'morgen', True, 'starts-with'),
                    ('dsAttrTypeStandard:LastName', 'morgen', True, 'starts-with'),
                    ('dsAttrTypeStandard:RealName', 'morgen', True, 'starts-with'),
                    ('dsAttrTypeStandard:EMailAddress', 'morgen', True, 'contains')
                ): ['dsRecTypeStandard:Users']
            }
        )