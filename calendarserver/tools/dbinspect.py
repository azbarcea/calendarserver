#!/usr/bin/env python
# -*- test-case-name: calendarserver.tools.test.test_calverify -*-
##
# Copyright (c) 2011 Apple Inc. All rights reserved.
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

"""
This tool allows data in the database to be directly inspected using a set
of simple commands.
"""

from calendarserver.tap.util import directoryFromConfig
from calendarserver.tools import tables
from calendarserver.tools.cmdline import utilityMain
from pycalendar.datetime import PyCalendarDateTime
from twext.enterprise.dal.syntax import Select, Parameter, Count, Delete
from twisted.application.service import Service
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python.filepath import FilePath
from twisted.python.reflect import namedClass
from twisted.python.text import wordWrap
from twisted.python.usage import Options
from twistedcaldav import caldavxml
from twistedcaldav.config import config
from twistedcaldav.datafilters.peruserdata import PerUserDataFilter
from twistedcaldav.directory import calendaruserproxy
from twistedcaldav.query import calendarqueryfilter
from twistedcaldav.stdconfig import DEFAULT_CONFIG_FILE
from txdav.common.datastore.sql_tables import schema, _BIND_MODE_OWN
import os
import sys
import traceback

def usage(e=None):
    if e:
        print e
        print ""
    try:
        DBInspectOptions().opt_help()
    except SystemExit:
        pass
    if e:
        sys.exit(64)
    else:
        sys.exit(0)


description = '\n'.join(
    wordWrap(
        """
        Usage: calendarserver_calverify [options] [input specifiers]\n
        """,
        int(os.environ.get('COLUMNS', '80'))
    )
)

class DBInspectOptions(Options):
    """
    Command-line options for 'calendarserver_dbinspect'
    """

    synopsis = description

    optFlags = [
        ['verbose', 'v', "Verbose logging."],
        ['purging', 'p', "Enable Purge command."],
    ]

    optParameters = [
        ['config', 'f', DEFAULT_CONFIG_FILE, "Specify caldavd.plist configuration path."],
    ]

    def __init__(self):
        super(DBInspectOptions, self).__init__()
        self.outputName = '-'

def UserNameFromUID(txn, uid):
    record = txn._directory.recordWithGUID(uid)
    return record.shortNames[0] if record else "(%s)" % (uid,)
    
class Cmd(object):
    
    _name = None
    
    @classmethod
    def name(cls):
        return cls._name

    def doIt(self, txn):
        raise NotImplementedError

class CalendarHomes(Cmd):
    
    _name = "List Calendar Homes"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        uids = yield self.getAllHomeUIDs(txn)
        
        # Print table of results
        missing = 0
        table = tables.Table()
        table.addHeader(("Owner UID", "Short Name"))
        for uid in sorted(uids):
            shortname = UserNameFromUID(txn, uid)
            if shortname.startswith("("):
                missing += 1
            table.addRow((
                uid,
                shortname,
            ))
        
        print "\n"
        print "Calendar Homes (total=%d, missing=%d):\n" % (len(uids), missing,)
        table.printTable()

    @inlineCallbacks
    def getAllHomeUIDs(self, txn):
        ch = schema.CALENDAR_HOME
        rows = (yield Select(
            [ch.OWNER_UID,],
            From=ch,
        ).on(txn))
        returnValue(tuple([row[0] for row in rows]))


class CalendarHomesSummary(Cmd):
    
    _name = "List Calendar Homes with summary information"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        uids = yield self.getCalendars(txn)
        
        results = {}
        for uid, calname, count in sorted(uids, key=lambda x:x[0]):
            totalname, totalcount = results.get(uid, (0, 0,))
            if calname != "inbox":
                totalname += 1
                totalcount += count
                results[uid] = (totalname, totalcount,)
        
        # Print table of results
        table = tables.Table()
        table.addHeader(("Owner UID", "Short Name", "Calendars", "Resources"))
        totals = [0, 0, 0]
        for uid in sorted(results.keys()):
            shortname = UserNameFromUID(txn, uid)
            table.addRow((
                uid,
                shortname,
                results[uid][0],
                results[uid][1],
            ))
            totals[0] += 1
            totals[1] += results[uid][0]
            totals[2] += results[uid][1]
        table.addFooter(("Total", totals[0], totals[1], totals[2]))
        table.addFooter((
            "Average",
            "",
            "%.2f" % ((1.0 * totals[1])/totals[0] if totals[0] else 0,),
            "%.2f" % ((1.0 * totals[2])/totals[0] if totals[0] else 0,),
        ))
        
        print "\n"
        print "Calendars with resource count (total=%d):\n" % (len(results),)
        table.printTable()

    @inlineCallbacks
    def getCalendars(self, txn):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                Count(co.RESOURCE_ID),
            ],
            From=ch.join(
                cb, type="inner", on=(ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID).And(
                    cb.BIND_MODE == _BIND_MODE_OWN)).join(
                co, type="left", on=(cb.CALENDAR_RESOURCE_ID == co.CALENDAR_RESOURCE_ID)),
            GroupBy=(ch.OWNER_UID, cb.CALENDAR_RESOURCE_NAME)
        ).on(txn))
        returnValue(tuple(rows))


class Calendars(Cmd):
    
    _name = "List Calendars"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        uids = yield self.getCalendars(txn)
        
        # Print table of results
        table = tables.Table()
        table.addHeader(("Owner UID", "Short Name", "Calendar", "Resources"))
        for uid, calname, count in sorted(uids, key=lambda x:(x[0], x[1])):
            shortname = UserNameFromUID(txn, uid)
            table.addRow((
                uid,
                shortname,
                calname,
                count
            ))
        
        print "\n"
        print "Calendars with resource count (total=%d):\n" % (len(uids),)
        table.printTable()

    @inlineCallbacks
    def getCalendars(self, txn):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                Count(co.RESOURCE_ID),
            ],
            From=ch.join(
                cb, type="inner", on=(ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID).And(
                    cb.BIND_MODE == _BIND_MODE_OWN)).join(
                co, type="left", on=(cb.CALENDAR_RESOURCE_ID == co.CALENDAR_RESOURCE_ID)),
            GroupBy=(ch.OWNER_UID, cb.CALENDAR_RESOURCE_NAME)
        ).on(txn))
        returnValue(tuple(rows))


class Events(Cmd):
    
    _name = "List Events"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        uids = yield self.getEvents(txn)
        
        # Print table of results
        table = tables.Table()
        table.addHeader(("Owner UID", "Short Name", "Calendar", "ID", "Type", "UID"))
        for uid, calname, id, caltype, caluid in sorted(uids, key=lambda x:(x[0], x[1])):
            shortname = UserNameFromUID(txn, uid)
            table.addRow((
                uid,
                shortname,
                calname,
                id,
                caltype,
                caluid
            ))
        
        print "\n"
        print "Calendar events (total=%d):\n" % (len(uids),)
        table.printTable()

    @inlineCallbacks
    def getEvents(self, txn):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                co.RESOURCE_ID,
                co.ICALENDAR_TYPE,
                co.ICALENDAR_UID,
            ],
            From=ch.join(
                cb, type="inner", on=(ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID).And(
                    cb.BIND_MODE == _BIND_MODE_OWN)).join(
                co, type="inner", on=(cb.CALENDAR_RESOURCE_ID == co.CALENDAR_RESOURCE_ID)),
        ).on(txn))
        returnValue(tuple(rows))

class Event(Cmd):
    
    _name = "Get Event Data by Resource-ID"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        
        rid = raw_input("Resource-ID: ")
        try:
            int(rid)
        except ValueError:
            print 'Resource ID must be an integer'
            returnValue(None)
        result = yield self.getData(txn, rid)
        if result:
            resource, created, modified, data = result
            table = tables.Table()
            table.addRow(("Resource Name:", resource))
            table.addRow(("Resource ID:", rid))
            table.addRow(("Created", created))
            table.addRow(("Modified", modified))
            print "\n"
            table.printTable()
            print data
        else:
            print "Could not find resource"

    @inlineCallbacks
    def getData(self, txn, rid):
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                co.RESOURCE_NAME,
                co.CREATED,
                co.MODIFIED,
                co.ICALENDAR_TEXT,
            ],
            From=co,
            Where=(co.RESOURCE_ID == Parameter("ResourceID")),
        ).on(txn, **{"ResourceID": rid}))
        returnValue(rows[0] if rows else None)

class EventsByUID(Cmd):
    
    _name = "Get Event Data by iCalendar UID"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        
        uid = raw_input("UID: ")
        rows = yield self.getData(txn, uid)
        if rows:
            for owner, calendar, resource_id, resource, created, modified, data in rows:
                shortname = UserNameFromUID(txn, owner)
                table = tables.Table()
                table.addRow(("User Name:", shortname,))
                table.addRow(("Calendar:", calendar,))
                table.addRow(("Resource Name:", resource))
                table.addRow(("Resource ID:", resource_id))
                table.addRow(("Created", created))
                table.addRow(("Modified", modified))
                print "\n"
                table.printTable()
                print data
        else:
            print "Could not find icalendar data"

    @inlineCallbacks
    def getData(self, txn, uid):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                co.RESOURCE_ID,
                co.RESOURCE_NAME,
                co.CREATED,
                co.MODIFIED,
                co.ICALENDAR_TEXT,
            ],
            From=ch.join(
                cb, type="inner", on=(ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID).And(
                    cb.BIND_MODE == _BIND_MODE_OWN)).join(
                co, type="inner", on=(cb.CALENDAR_RESOURCE_ID == co.CALENDAR_RESOURCE_ID)),
            Where=(co.ICALENDAR_UID == Parameter("UID")),
        ).on(txn, **{"UID": uid}))
        returnValue(tuple(rows))


class EventsByName(Cmd):
    
    _name = "Get Event Data by resource name"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        
        name = raw_input("Resource Name: ")
        rows = yield self.getData(txn, name)
        if rows:
            for owner, calendar, resource_id, resource, created, modified, data in rows:
                shortname = UserNameFromUID(txn, owner)
                table = tables.Table()
                table.addRow(("User Name:", shortname,))
                table.addRow(("Calendar:", calendar,))
                table.addRow(("Resource Name:", resource))
                table.addRow(("Resource ID:", resource_id))
                table.addRow(("Created", created))
                table.addRow(("Modified", modified))
                print "\n"
                table.printTable()
                print data
        else:
            print "Could not find icalendar data"

    @inlineCallbacks
    def getData(self, txn, name):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                co.RESOURCE_ID,
                co.RESOURCE_NAME,
                co.CREATED,
                co.MODIFIED,
                co.ICALENDAR_TEXT,
            ],
            From=ch.join(
                cb, type="inner", on=(ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID).And(
                    cb.BIND_MODE == _BIND_MODE_OWN)).join(
                co, type="inner", on=(cb.CALENDAR_RESOURCE_ID == co.CALENDAR_RESOURCE_ID)),
            Where=(co.RESOURCE_NAME == Parameter("Name")),
        ).on(txn, **{"Name": name}))
        returnValue(tuple(rows))


class EventsByOwner(Cmd):
    
    _name = "Get Event Data by Owner UID"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        
        uid = raw_input("Owner UID: ")
        rows = yield self.getData(txn, uid)
        if rows:
            for owner, calendar, resource_id, resource, created, modified, data in rows:
                shortname = UserNameFromUID(txn, owner)
                table = tables.Table()
                table.addRow(("User Name:", shortname,))
                table.addRow(("Calendar:", calendar,))
                table.addRow(("Resource Name:", resource))
                table.addRow(("Resource ID:", resource_id))
                table.addRow(("Created", created))
                table.addRow(("Modified", modified))
                print "\n"
                table.printTable()
                print data
        else:
            print "Could not find icalendar data"

    @inlineCallbacks
    def getData(self, txn, uid):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                co.RESOURCE_ID,
                co.RESOURCE_NAME,
                co.CREATED,
                co.MODIFIED,
                co.ICALENDAR_TEXT,
            ],
            From=ch.join(
                cb, type="inner", on=(ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID).And(
                    cb.BIND_MODE == _BIND_MODE_OWN)).join(
                co, type="inner", on=(cb.CALENDAR_RESOURCE_ID == co.CALENDAR_RESOURCE_ID)),
            Where=(ch.OWNER_UID == Parameter("UID")),
        ).on(txn, **{"UID": uid}))
        returnValue(tuple(rows))


class EventsByOwnerCalendar(Cmd):
    
    _name = "Get Event Data by Owner UID and calendar name"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        
        uid = raw_input("Owner UID: ")
        name = raw_input("Calendar resource name: ")
        rows = yield self.getData(txn, uid, name)
        if rows:
            for owner, calendar, resource_id, resource, created, modified, data in rows:
                shortname = UserNameFromUID(txn, owner)
                table = tables.Table()
                table.addRow(("User Name:", shortname,))
                table.addRow(("Calendar:", calendar,))
                table.addRow(("Resource Name:", resource))
                table.addRow(("Resource ID:", resource_id))
                table.addRow(("Created", created))
                table.addRow(("Modified", modified))
                print "\n"
                table.printTable()
                print data
        else:
            print "Could not find icalendar data"

    @inlineCallbacks
    def getData(self, txn, uid, name):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                co.RESOURCE_ID,
                co.RESOURCE_NAME,
                co.CREATED,
                co.MODIFIED,
                co.ICALENDAR_TEXT,
            ],
            From=ch.join(
                cb, type="inner", on=(ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID).And(
                    cb.BIND_MODE == _BIND_MODE_OWN)).join(
                co, type="inner", on=(cb.CALENDAR_RESOURCE_ID == co.CALENDAR_RESOURCE_ID)),
            Where=((ch.OWNER_UID == Parameter("UID")).And(cb.CALENDAR_RESOURCE_NAME == Parameter("NAME"))),
        ).on(txn, **{"UID": uid, "NAME": name}))
        returnValue(tuple(rows))


class EventsByContent(Cmd):
    
    _name = "Get Event Data by Searching its Text Data"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        
        uid = raw_input("Search for: ")
        rows = yield self.getData(txn, uid)
        if rows:
            for owner, calendar, resource_id, resource, created, modified, data in rows:
                shortname = UserNameFromUID(txn, owner)
                table = tables.Table()
                table.addRow(("User Name:", shortname,))
                table.addRow(("Calendar:", calendar,))
                table.addRow(("Resource Name:", resource))
                table.addRow(("Resource ID:", resource_id))
                table.addRow(("Created", created))
                table.addRow(("Modified", modified))
                print "\n"
                table.printTable()
                print data
        else:
            print "Could not find icalendar data"

    @inlineCallbacks
    def getData(self, txn, text):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                co.RESOURCE_ID,
                co.RESOURCE_NAME,
                co.CREATED,
                co.MODIFIED,
                co.ICALENDAR_TEXT,
            ],
            From=ch.join(
                cb, type="inner", on=(ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID).And(
                    cb.BIND_MODE == _BIND_MODE_OWN)).join(
                co, type="inner", on=(cb.CALENDAR_RESOURCE_ID == co.CALENDAR_RESOURCE_ID)),
            Where=(co.ICALENDAR_TEXT.Contains(Parameter("Text"))),
        ).on(txn, **{"Text": text}))
        returnValue(tuple(rows))


class EventsInTimerange(Cmd):
    
    _name = "Get Event Data within a specified time range"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        
        uid = raw_input("Owner UID: ")
        start = raw_input("Start Time (UTC YYYYMMDDTHHMMSSZ or YYYYMMDD): ")
        if len(start) == 8:
            start += "T000000Z"
        end = raw_input("End Time (UTC YYYYMMDDTHHMMSSZ or YYYYMMDD): ")
        if len(end) == 8:
            end += "T000000Z"

        try:
            start = PyCalendarDateTime.parseText(start)
        except ValueError:
            print "Invalid start value"
            returnValue(None)
        try:
            end = PyCalendarDateTime.parseText(end)
        except ValueError:
            print "Invalid end value"
            returnValue(None)
        timerange = caldavxml.TimeRange(start=start.getText(), end=end.getText())

        home = yield txn.calendarHomeWithUID(uid)
        if home is None:
            print "Could not find calendar home"
            returnValue(None)
            
        yield self.eventsForEachCalendar(home, uid, timerange)

    @inlineCallbacks
    def eventsForEachCalendar(self, home, uid, timerange):
        
        calendars = yield home.calendars()
        for calendar in calendars:
            if calendar.name() == "inbox":
                continue
            yield self.eventsInTimeRange(calendar, uid, timerange)

    @inlineCallbacks
    def eventsInTimeRange(self, calendar, uid, timerange):
        
        # Create fake filter element to match time-range
        filter =  caldavxml.Filter(
                      caldavxml.ComponentFilter(
                          caldavxml.ComponentFilter(
                              timerange,
                              name=("VEVENT",),
                          ),
                          name="VCALENDAR",
                       )
                  )
        filter = calendarqueryfilter.Filter(filter)
        filter.settimezone(None)

        matches = yield calendar._index.indexedSearch(filter, useruid=uid, fbtype=False)
        if matches is None:
            returnValue(None)
        for name, _ignore_uid, _ignore_type in matches:
            event = yield calendar.calendarObjectWithName(name)
            ical_data = yield event.component()
            ical_data = PerUserDataFilter(uid).filter(ical_data)
            ical_data.stripKnownTimezones()

            table = tables.Table()
            table.addRow(("Calendar:", calendar.name(),))
            table.addRow(("Resource Name:", name))
            table.addRow(("Resource ID:", event._resourceID))
            table.addRow(("Created", event.created()))
            table.addRow(("Modified", event.modified()))
            print "\n"
            table.printTable()
            print ical_data.getTextWithTimezones(includeTimezones=False)


class Purge(Cmd):
    
    _name = "Purge all data from tables"
    
    @inlineCallbacks
    def doIt(self, txn):
        
        
        if raw_input("Do you really want to remove all data [y/n]: ")[0].lower() != 'y':
            print "No data removed"
            returnValue(None)

        wipeout = (
            # These are ordered in such a way as to ensure key constraints are not 
            # violated as data is removed

            schema.RESOURCE_PROPERTY,

            schema.CALENDAR_OBJECT_REVISIONS,

            schema.CALENDAR,
            #schema.CALENDAR_BIND, - cascades
            #schema.CALENDAR_OBJECT, - cascades
            #schema.TIME_RANGE, - cascades
            #schema.TRANSPARENCY, - cascades
            

            schema.CALENDAR_HOME,
            #schema.CALENDAR_HOME_METADATA - cascades
            schema.INVITE,
            schema.ATTACHMENT,
            
            schema.ADDRESSBOOK_OBJECT_REVISIONS,

            schema.ADDRESSBOOK,
            #schema.ADDRESSBOOK_BIND, - cascades
            #schema.ADDRESSBOOK_OBJECT, - cascades

            schema.ADDRESSBOOK_HOME,
            #schema.ADDRESSBOOK_HOME_METADATA, - cascades

            schema.NOTIFICATION_HOME,
            schema.NOTIFICATION,
            #schema.NOTIFICATION_OBJECT_REVISIONS - cascades,
        )

        for tableschema in wipeout:
            yield self.removeTableData(txn, tableschema)
            print "Removed rows in table %s" % (tableschema,)
            
        if calendaruserproxy.ProxyDBService is not None:
            calendaruserproxy.ProxyDBService.clean() #@UndefinedVariable
            print "Removed all proxies"
        else:
            print "No proxy database to clean."
        
        fp = FilePath(config.AttachmentsRoot)
        if fp.exists():
            for child in fp.children():
                child.remove()
            print "Removed attachments."
        else:
            print "No attachments path to delete."

    @inlineCallbacks
    def removeTableData(self, txn, tableschema):
        yield Delete(
            From=tableschema,
            Where=None  # Deletes all rows
        ).on(txn)


class DBInspectService(Service, object):
    """
    Service which runs, exports the appropriate records, then stops the reactor.
    """

    def __init__(self, store, options, reactor, config):
        super(DBInspectService, self).__init__()
        self.store   = store
        self.options = options
        self.reactor = reactor
        self.config = config
        self._directory = None
        self.commands = []
        self.commandMap = {}


    def startService(self):
        """
        Start the service.
        """
        super(DBInspectService, self).startService()
        
        # Register commands
        self.registerCommand(CalendarHomes)
        self.registerCommand(CalendarHomesSummary)
        self.registerCommand(Calendars)
        self.registerCommand(Events)
        self.registerCommand(Event)
        self.registerCommand(EventsByUID)
        self.registerCommand(EventsByName)
        self.registerCommand(EventsByOwner)
        self.registerCommand(EventsByOwnerCalendar)
        self.registerCommand(EventsByContent)
        self.registerCommand(EventsInTimerange)
        self.doDBInspect()


    def registerCommand(self, cmd):
        self.commands.append(cmd.name())
        self.commandMap[cmd.name()] = cmd

    @inlineCallbacks
    def runCommandByPosition(self, position):
        try:
            yield self.runCommandByName(self.commands[position])
        except IndexError:
            print "Position %d not available" % (position,)
            returnValue(None)

    @inlineCallbacks
    def runCommandByName(self, name):
        try:
            yield self.runCommand(self.commandMap[name])
        except IndexError:
            print "Unknown command: '%s'" % (name,)

    @inlineCallbacks
    def runCommand(self, cmd):
        txn = self.store.newTransaction()
        txn._directory = self.directoryService()
        try:
            yield cmd().doIt(txn)
            yield txn.commit()
        except Exception, e:
            yield txn.abort()
            print "Command '%s' failed because of: %s" % (cmd.name(), e,)
            traceback.print_exc()

    def printCommands(self):
        
        print "\n<---- Commands ---->"
        for ctr, name in enumerate(self.commands):
            print "%d. %s" % (ctr+1, name,)
        if self.options["purging"]:
            print "P. Purge\n"
        print "Q. Quit\n"

    @inlineCallbacks
    def doDBInspect(self):
        """
        Poll for commands, stopping the reactor when done.
        """
        
        while True:
            self.printCommands()
            cmd = raw_input("Command: ")
            if cmd.lower() == 'q':
                break
            if self.options["purging"] and cmd.lower() == 'p':
                yield self.runCommand(Purge)
            else:
                try:
                    position = int(cmd)
                except ValueError:
                    print "Invalid command. Try again.\n"
                    continue
            
                yield self.runCommandByPosition(position-1)

        self.reactor.stop()


    def directoryService(self):
        """
        Get an appropriate directory service for this L{DBInspectService}'s
        configuration, creating one first if necessary.
        """
        if self._directory is None:
            self._directory = directoryFromConfig(self.config)
            proxydbClass = namedClass(config.ProxyDBService.type)
            try:
                calendaruserproxy.ProxyDBService = proxydbClass(**config.ProxyDBService.params)
            except IOError:
                print "Could not start proxydb service"
        return self._directory


    def stopService(self):
        """
        Stop the service.  Nothing to do; everything should be finished by this
        time.
        """
        # TODO: stopping this service mid-export should really stop the export
        # loop, but this is not implemented because nothing will actually do it
        # except hitting ^C (which also calls reactor.stop(), so that will exit
        # anyway).



def main(argv=sys.argv, stderr=sys.stderr, reactor=None):
    """
    Do the export.
    """
    if reactor is None:
        from twisted.internet import reactor
    options = DBInspectOptions()
    options.parseOptions(argv[1:])
    def makeService(store):
        return DBInspectService(store, options, reactor, config)
    utilityMain(options['config'], makeService, reactor)

if __name__ == '__main__':
    main()
