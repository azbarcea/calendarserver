#!/usr/bin/env python

##
# Copyright (c) 2006-2009 Apple Inc. All rights reserved.
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

import sys
import os
import itertools
import operator
from getopt import getopt, GetoptError
from uuid import UUID

from twisted.python import log
from twisted.python.reflect import namedClass
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.address import IPv4Address
from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twisted.web2.dav import davxml

from twext.web2.dav.davxml import sname2qname, qname2sname

from twistedcaldav import caldavxml
from twistedcaldav import memcachepool
from twistedcaldav.config import config, defaultConfigFile
from twistedcaldav.customxml import calendarserver_namespace
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
from twistedcaldav.log import setLogLevelForNamespace
from twistedcaldav.notify import installNotificationClient
from twistedcaldav.static import CalendarHomeProvisioningFile

from calendarserver.tools.util import UsageError, booleanArgument
from calendarserver.tools.util import loadConfig, getDirectory, dummyDirectoryRecord
from calendarserver.provision.root import RootResource

def usage(e=None):
    if e:
        print e
        print ""

    name = os.path.basename(sys.argv[0])
    print "usage: %s [options] actions principal [principal ...]" % (name,)
    print ""
    print "  Performs the given actions against the giving principals."
    print ""
    print "  Principals are identified by one of the following:"
    print "    Type and shortname (eg.: users:wsanchez)"
   #print "    A principal path (eg.: /principals/users/wsanchez/)"
    print "    A GUID (eg.: E415DBA7-40B5-49F5-A7CC-ACC81E4DEC79)"
    print ""
    print "options:"
    print "  -h --help: print this help and exit"
    print "  -f --config <path>: Specify caldavd.plist configuration path"
    print ""
    print "actions:"
   #print "  --search <search-string>: search for matching resources"
    print "  -P, --read-property: read DAV property to read (eg.: {DAV:}group-member-set)"
    print "  --list-read-proxies: list proxies with read-only access"
    print "  --list-write-proxies: list proxies with read-write access"
    print "  --list-proxies: list all proxies"
    print "  --add-read-proxy=principal: add a read-only proxy"
    print "  --add-write-proxy=principal: add a read-write proxy"
    print "  --remove-proxy=principal: remove a proxy"
    print "  --set-auto-schedule={true|false}: set auto-accept state"
    print "  --get-auto-schedule: read auto-schedule state"

    if e:
        sys.exit(64)
    else:
        sys.exit(0)

def main():
    try:
        (optargs, args) = getopt(
            sys.argv[1:], "hf:P:", [
                "help",
                "config=",
               #"search=",
                "read-property=",
                "list-read-proxies",
                "list-write-proxies",
                "list-proxies",
                "add-read-proxy=",
                "add-write-proxy=",
                "remove-proxy=",
                "set-auto-schedule=",
                "get-auto-schedule",
            ],
        )
    except GetoptError, e:
        usage(e)

    if not args:
        usage("No principals specified.")

    #
    # Get configuration
    #
    configFileName = None
    actions = []

    for opt, arg in optargs:
        if opt in ("-h", "--help"):
            usage()

        elif opt in ("-f", "--config"):
            configFileName = arg

        elif opt in ("-P", "--read-property"):
            try:
                qname = sname2qname(arg)
            except ValueError, e:
                abort(e)
            actions.append((action_readProperty, qname))

        elif opt in ("", "--list-read-proxies"):
            actions.append((action_listProxies, "read"))

        elif opt in ("", "--list-write-proxies"):
            actions.append((action_listProxies, "write"))

        elif opt in ("-L", "--list-proxies"):
            actions.append((action_listProxies, "read", "write"))

        elif opt in ("--add-read-proxy", "--add-write-proxy"):
            if "read" in opt:
                proxyType = "read"
            elif "write" in opt:
                proxyType = "write"
            else:
                raise AssertionError("Unknown proxy type")

            try:
                principalForPrincipalID(arg, checkOnly=True)
            except ValueError, e:
                abort(e)

            actions.append((action_addProxy, proxyType, arg))

        elif opt in ("", "--remove-proxy"):
            try:
                principalForPrincipalID(arg, checkOnly=True)
            except ValueError, e:
                abort(e)

            actions.append((action_removeProxy, arg))

        elif opt in ("", "--set-auto-schedule"):
            try:
                autoSchedule = booleanArgument(arg)
            except ValueError, e:
                abort(e)

            actions.append((action_setAutoSchedule, autoSchedule))

        elif opt in ("", "--get-auto-schedule"):
            actions.append((action_getAutoSchedule,))

        else:
            raise NotImplementedError(opt)

    #
    # Get configuration
    #
    try:
        loadConfig(configFileName)
    except ConfigurationError, e:
        abort(e)

    #
    # Do a quick sanity check that arguments look like principal
    # identifiers.
    #
    for arg in args:
        try:
            principalForPrincipalID(arg, checkOnly=True)
        except ValueError, e:
            abort(e)

    #
    # Send logging output to stdout
    #
    setLogLevelForNamespace(None, "warn")
    logFileName = "/dev/stdout"
    observer = log.FileLogObserver(open(logFileName, "a"))
    log.addObserver(observer.emit)

    #
    # Start the reactor
    #
    reactor.callLater(0, run, args, actions)
    reactor.run()

@inlineCallbacks
def run(principalIDs, actions):
    try:
        #
        # Connect to memcached, notifications
        #
        if config.Memcached.ClientEnabled:
            memcachepool.installPool(
                IPv4Address(
                    "TCP",
                    config.Memcached.BindAddress,
                    config.Memcached.Port,
                ),
                config.Memcached.MaxClients
            )
        if config.Notifications.Enabled:
            installNotificationClient(
                config.Notifications.InternalNotificationHost,
                config.Notifications.InternalNotificationPort,
            )

        #
        # Wire up the resource hierarchy
        #
        principalCollection = config.directory.getPrincipalCollection()
        root = RootResource(
            config.DocumentRoot,
            principalCollections=(principalCollection,),
        )
        root.putChild("principals", principalCollection)
        calendarCollection = CalendarHomeProvisioningFile(
            os.path.join(config.DocumentRoot, "calendars"),
            config.directory, "/calendars/",
        )
        root.putChild("calendars", calendarCollection)

        #
        # Wrap root resource
        #
        # FIXME: not a fan -wsanchez
        #root = ResourceWrapper(root)

        for principalID in principalIDs:
            # Resolve the given principal IDs to principals
            try:
                principal = principalForPrincipalID(principalID)
            except ValueError:
                principal = None

            if principal is None:
                sys.stderr.write("Invalid principal ID: %s\n" % (principalID,))
                continue

            # Performs requested actions
            for action in actions:
                (yield action[0](principal, *action[1:]))
                print ""

    finally:
        #
        # Stop the reactor
        #
        reactor.stop()

def principalForPrincipalID(principalID, checkOnly=False):
    if principalID.startswith("/"):
        raise ValueError("Can't resolve paths yet")

        if checkOnly:
            return None

    if principalID.startswith("("):
        try:
            i = principalID.index(")")

            if checkOnly:
                return None

            recordType = principalID[1:i]
            shortName = principalID[i+1:]

            if not recordType or not shortName or "(" in recordType:
                raise ValueError()

            return config.directory.principalCollection.principalForShortName(recordType, shortName)

        except ValueError:
            pass

    if ":" in principalID:
        if checkOnly:
            return None

        recordType, shortName = principalID.split(":", 1)

        return config.directory.principalCollection.principalForShortName(recordType, shortName)

    try:
        guid = UUID(principalID)

        if checkOnly:
            return None

        return config.directory.principalCollection.principalForUID(guid)
    except ValueError:
        pass

    raise ValueError("Invalid principal identifier: %s" % (principalID,))

def proxySubprincipal(principal, proxyType):
    return principal.getChild("calendar-proxy-" + proxyType)

@inlineCallbacks
def action_readProperty(resource, qname):
    property = (yield resource.readProperty(qname, None))
    print "%r on %s:" % (qname2sname(qname), resource)
    print ""
    print property.toxml()

@inlineCallbacks
def action_listProxies(principal, *proxyTypes):
    for proxyType in proxyTypes:
        subPrincipal = proxySubprincipal(principal, proxyType)
        if subPrincipal is None:
            print "No %s proxies for %s" % (proxyType, principal)
            continue

        membersProperty = (yield subPrincipal.readProperty(davxml.GroupMemberSet, None))

        if membersProperty.children:
            print "%s proxies for %s:" % (
                {"read": "Read-only", "write": "Read/write"}[proxyType],
                principal,
            )
            for member in membersProperty.children:
                print " *", member
        else:
            print "No %s proxies for %s" % (proxyType, principal)

@inlineCallbacks
def action_addProxy(principal, proxyType, *proxyIDs):
    for proxyID in proxyIDs:
        proxyPrincipal = principalForPrincipalID(proxyID)
        proxyURL = proxyPrincipal.url()

        subPrincipal = proxySubprincipal(principal, proxyType)
        if subPrincipal is None:
            sys.stderr.write("Unable to edit %s proxies for %s\n" % (proxyType, principal))
            continue

        membersProperty = (yield subPrincipal.readProperty(davxml.GroupMemberSet, None))

        for memberURL in membersProperty.children:
            if str(memberURL) == proxyURL:
                print "%s is already a %s proxy for %s" % (proxyPrincipal, proxyType, principal)
                break
        else:
            memberURLs = list(membersProperty.children)
            memberURLs.append(davxml.HRef(proxyURL))
            membersProperty = davxml.GroupMemberSet(*memberURLs)
            (yield subPrincipal.writeProperty(membersProperty, None))
            print "Added %s as a %s proxy for %s" % (proxyPrincipal, proxyType, principal)

        proxyTypes = ["read", "write"]
        proxyTypes.remove(proxyType)

        (yield action_removeProxy(principal, proxyID, proxyTypes=proxyTypes))

@inlineCallbacks
def action_removeProxy(principal, *proxyIDs, **kwargs):
    proxyTypes = kwargs.get("proxyTypes", ("read", "write"))

    for proxyID in proxyIDs:
        for proxyType in proxyTypes:
            proxyPrincipal = principalForPrincipalID(proxyID)
            proxyURL = proxyPrincipal.url()

            subPrincipal = proxySubprincipal(principal, proxyType)
            if subPrincipal is None:
                sys.stderr.write("Unable to edit %s proxies for %s\n" % (proxyType, principal))
                continue

            membersProperty = (yield subPrincipal.readProperty(davxml.GroupMemberSet, None))

            memberURLs = [
                m for m in membersProperty.children
                if str(m) != proxyURL
            ]

            if len(memberURLs) == len(membersProperty.children):
                # No change
                continue

            membersProperty = davxml.GroupMemberSet(*memberURLs)
            (yield subPrincipal.writeProperty(membersProperty, None))
            print "Removed %s as a %s proxy for %s" % (proxyPrincipal, proxyType, principal)

@inlineCallbacks
def action_setAutoSchedule(principal, autoSchedule):
    print "Setting auto-schedule to %s for %s" % (
        { True: "true", False: "false" }[autoSchedule],
        principal,
    )
    (yield principal.setAutoSchedule(autoSchedule))

@inlineCallbacks
def action_getAutoSchedule(principal):
    autoSchedule = (yield principal.getAutoSchedule())
    print "Autoschedule for %s is %s" % (
        principal,
        { True: "true", False: "false" }[autoSchedule],
    )

@inlineCallbacks
def _run(directory, root, optargs, principalIDs):

    print ""

    resource = None

    for opt, arg in optargs:

        if opt in ("-s", "--search",):
            fields = []
            for fieldName in ("fullName", "firstName", "lastName",
                "emailAddresses"):
                fields.append((fieldName, arg, True, "contains"))

            records = list((yield config.directory.recordsMatchingFields(fields)))
            if records:
                records.sort(key=operator.attrgetter('fullName'))
                print "%d matches found:" % (len(records),)
                for record in records:
                    print "\n%s (%s)" % (record.fullName,
                        { "users"     : "User",
                          "groups"    : "Group",
                          "locations" : "Place",
                          "resources" : "Resource",
                        }.get(record.recordType),
                    )
                    print record.guid
                    print "   Record names: %s" % (", ".join(record.shortNames),)
                    if record.authIDs:
                        print "   Auth IDs: %s" % (", ".join(record.authIDs),)
                    if record.emailAddresses:
                        print "   Emails: %s" % (", ".join(record.emailAddresses),)
            else:
                print "No matches found"

    print ""

    # reactor.callLater(0, reactor.stop)
    reactor.stop()

def abort(msg, status=1):
    sys.stdout.write("%s\n" % (msg,))
    try:
        reactor.stop()
    except RuntimeError:
        pass
    sys.exit(status)

if __name__ == "__main__":
    main()