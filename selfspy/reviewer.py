# -*- coding: utf-8 -*-
"""
Selfspy: Track your computer activity
Copyright (C) 2012 Bjarte Johansen
Modified 2014 by Adam Rule, Aurélien Tabard, and Jonas Kemper

Selfspy is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Selfspy is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Selfspy. If not, see <http://www.gnu.org/licenses/>.
"""


import os
from os import listdir
from os.path import isfile, join

from objc import IBAction, IBOutlet
from AppKit import *

from CBGraphView import CBGraphView

import calendar
from dateutil.parser import parse

SCREENSHOT_REVIEW_INTERVAL = 1
UI_SLIDER_MAX_VALUE = 100


class WindowListController(NSArrayController):

    @IBAction
    def updateAppCheckbox_(self, sender):
        x=1
        # TODO determine why self.review_controller does not work for some list
        # items but works for others

        # print self.review_controller
        #row = self.review_controller.appList.selectedRow()
        # view = self.review_controller.appList.viewAtColumn_row_makeIfNecessary_(0,row,False)
        # if view:
        #     app = view.textField().stringValue()
        #     app_i = 0
        #     for i in range(len(self.review_controller.results)):
        #         if self.review_controller.results[i]["Data"] == app:
        #             app_i = i
        #             break
        #     app_data = self.review_controller.results[app_i]
        #     num_rows = len(app_data['windows'])
        #     num_checked = 0
        #     for j in app_data['windows']:
        #         if j['checked'] == 1:
        #             num_checked += 1
        #     if num_checked == num_rows:
        #         app_data['checked'] = 1
        #     elif num_checked == 0:
        #         app_data['checked'] = 0
        #     else:
        #         app_data['checked'] = -1
        #     print "check value is " + str(app_data['checked'])


# Review window controller
class ReviewController(NSWindowController):

    # outlets for UI elements
    mainPanel = IBOutlet()
    tableView = IBOutlet()
    arrayController = IBOutlet()
    appList = IBOutlet()
    windowList = IBOutlet()
    protoCheckbox = IBOutlet()

    # instance variables
    currentScreenshot = -1
    dateQuery = ""

    # dynamic review table
    NSMutableDictionary = objc.lookUpClass('NSMutableDictionary')
    NSNumber = objc.lookUpClass('NSNumber')
    results = [ NSMutableDictionary.dictionaryWithDictionary_(x) for x in []]
    results_windows = [ NSMutableDictionary.dictionaryWithDictionary_(x) for x in [{'checked':0, 'windowName':'Window 10', 'image':''}]]

    # let activity_store write query results into those
    queryResponse = []
    queryResponse2 = []
    p1Response = []

    # timeline
    timeline_value = 0
    slider_max = 1
    slider_min = 0
    normalized_max_value = 0

    timeline_view = None


    def createWindowListController(self):
        return WindowListController

    @IBAction
    def updateAppCheckbox_(self, sender):
        print "You checked something"
        numCols = self.reviewController.windowList.numberOfColumns()
        numChecked = 0
        for i in range(numCols):
            if self.reviewController.windowList.viewAtColumn_row_makeIfNecessary_(i,0,False):
                print self.reviewController.windowList.viewAtColumn_row_makeIfNecessary_(i,0,False).textField().stringValue()

    # For Debugging purposes
    def printBools(self, self2=None):
        for value in self.results:
            try:
                print value['checkb']
            except KeyError:
                print "NO BOOLEAN"


    def getScreenshotPath(self, self2=None):
        path = os.path.expanduser(u'~/.selfspy/screenshots/')
        # TODO will this now still work on a thumbdrive?
        return path


    def generateScreenshotList(self, self2=None):
        path = self.getScreenshotPath(self)
        list_of_files = [ f for f in listdir(path) if isfile(join(path,f)) ]
        return list_of_files


    def displayScreenshot(self, self2=None, s=None):
        experienceImage = NSImage.alloc().initByReferencingFile_(self.getScreenshotPath(self) + s)

        width = experienceImage.size().width
        height = experienceImage.size().height
        ratio = width / height
        if( width > 960 or height > 600 ):
            if (ratio > 1.6):
                width = 960
                height = 960 / ratio
            else:
                width = 600 * ratio
                height = 600
        experienceImage.setScalesWhenResized_(True)
        experienceImage.setSize_((width, height))
        self.reviewController.mainPanel.setImage_(experienceImage)


    def generateDictEntry(self, checked=None):
        return NSMutableDictionary({'Data': self.queryResponse2[0] if len(self.queryResponse2) > 0 else "",
                                    'Datab': self.queryResponse[0] if len(self.queryResponse) > 0 else "",
                                    'checkb': NSNumber.numberWithBool_(checked)})


    def generateDateQuery(self, s=None):
        self.dateQuery = '20' + s[0:2] + '-' + s[2:4] + '-' + s[4:6] + ' ' + s[7:9] + ':' + s[9:11] + ':' + s[11:13] + '.'


    @IBAction
    def advanceReviewWindow_(self, sender):
        self.moveReviewWindow(direction=1)

    @IBAction
    def revertReviewWindow_(self, sender):
        self.moveReviewWindow(direction=-1)

    def tableViewSelectionDidChange_(self,sender):
        selected_row = self.appList.selectedRow()
        selected_view = self.appList.viewAtColumn_row_makeIfNecessary_(0,selected_row,False)

        # TODO for some reason, when we programatically select the 0 index
        # at launch, the selected_view is none
        if selected_view:
            selected_app = selected_view.textField().stringValue()
            app_index_in_dict = 0
            for i in range(len(self.results)):
                if self.results[i]["Data"] == selected_app:
                    app_index_in_dict = i
                    break
            self.results_windows = [ self.NSMutableDictionary.dictionaryWithDictionary_(x) for x in self.results[app_index_in_dict]['windows']]
            self.windowList.reloadData()

    def moveReviewWindow(self, direction):
        list_of_files = self.generateScreenshotList(self)
        screenshot_found = False

        while (not screenshot_found):
            self.currentScreenshot = self.currentScreenshot + (SCREENSHOT_REVIEW_INTERVAL * direction)
            if (0 <= self.currentScreenshot < len(list_of_files)):

                self.generateDateQuery(list_of_files[self.currentScreenshot])

                # send message to activity_store so it can do the database query
                NSNotificationCenter.defaultCenter().postNotificationName_object_('queryMetadata',self)

                if len(self.queryResponse) > 0:
                     d = self.generateDictEntry(checked=1)
                     if d in self.results:
                         screenshot_found = True
                         filename = s=list_of_files[self.currentScreenshot]
                         self.displayScreenshot(self, s=filename)
                         normalized_current_value = self.mapFilenameDateToNumber(s=filename) - self.slider_min
                         self.timeline_value = normalized_current_value * UI_SLIDER_MAX_VALUE / self.normalized_max_value

                self.queryResponse = []
                self.queryResponse2 = []

            else:
                screenshot_found = True # so that it stops searching
                self.reviewController.close()

    def mapFilenameDateToNumber(self, s=None):
        return int('20' + s[0:2] + s[2:4] + s[4:6] + s[7:9] + s[9:11] + s[11:13])
    #
    # def mapFilenameDateToNumber2(self, s=None):
    #     return int(s[0:4] + s[5:7] + s[8:10] + s[11:13] + s[14:16] + s[17:19])

    def unixTimeFromString(self, s=None):
        # print("attempting unixTimeFromString")
        front_bound = parse(str(s), fuzzy=True)
        ts = calendar.timegm(front_bound.utctimetuple())
        # print("before returning unixTimeFromString")
        return ts

    def getApplicationsAndURLsForTable(self, list_of_files):
        NSNotificationCenter.defaultCenter().postNotificationName_object_('getAppsAndUrls',self)

        for entry in self.queryResponse:
            mutable = NSMutableDictionary({'Data': entry['app_name'],
                                        'Datab': entry['image'],
                                        'checkb': entry['checked'],
                                        'windows': entry['windows']})#NSNumber.numberWithBool_(1)})
            self.results.append(mutable)

        self.queryResponse = []

        #for s in list_of_files:
            #self.generateDateQuery(self, s=s)

            # send message to activity_store so it can do the database query
            # NSNotificationCenter.defaultCenter().postNotificationName_object_('queryMetadata',self)

            # if len(self.queryResponse) > 0:
            #      d = self.generateDictEntry(self, checked=0)
            #      d2 = self.generateDictEntry(self, checked=1)
            #      if d not in self.results and d2 not in self.results:
            #         self.results.append(NSMutableDictionary.dictionaryWithDictionary_(d))

            # self.queryResponse = []
            # self.queryResponse2 = []

    def manageTimeline(self, list_of_files):
        a = self.mapFilenameDateToNumber(self, s=list_of_files[0])
        self.slider_min = self.unixTimeFromString(self, s=a)

        for s in list_of_files:
            self.generateDateQuery(self, s=s)
            a = self.mapFilenameDateToNumber(self, s=s)
            helper = self.unixTimeFromString(self, s=a)
            if self.slider_max < helper:
                self.slider_max = helper
            if self.slider_min > helper:
                self.slider_min = helper

        self.normalized_max_value = self.slider_max - self.slider_min

        NSNotificationCenter.defaultCenter().postNotificationName_object_('getProcess1times',self)

        entry_no = 0
        bounds_detected = 0
        front_bound = 0
        for entry in self.p1Response:
            entryA_no = 0
            for entryA in entry:
                print("entry " + str(entry_no) + " entryA "+ str(entryA_no) + ": " + str(entryA[1])  + " @ " + str(entryA[2]))

                if str(entryA[1]) == "Open" and bounds_detected == 0:
                    front_bound = self.unixTimeFromString(self, str(entryA[2]))
                    bounds_detected = 1

                if str(entryA[1]) == "Close" and bounds_detected == 1:
                    back_bound = self.unixTimeFromString(self, str(entryA[2]))
                    bounds_detected = 2

                if  bounds_detected == 2:
                    print("attempting to draw view at front_bound " + str(front_bound) + " - slider_min " + str(self.slider_min) + " / self.normalized_max_value " + str(self.normalized_max_value) + "  * 600 = " + str((front_bound - self.slider_min) / self.slider_max  * 600))
                    frame = NSRect(NSPoint((front_bound - self.slider_min) * 600 / self.normalized_max_value, 10), NSSize(30, 50))
                    this_view_B = CBGraphView.alloc().initWithFrame_(frame)

                    self.timeline_view.addSubview_(this_view_B)
                    this_view_B.drawRect_(frame)
                    bounds_detected = 0

                entryA_no += 1
            entry_no += 1

    def populateExperienceTable(self):
        list_of_files = self.generateScreenshotList(self)
        self.getApplicationsAndURLsForTable(self, list_of_files)
        self.manageTimeline(self, list_of_files)

        try:
            # re-sort list items and select the first one
            self.reviewController.arrayController.rearrangeObjects()
            index_set = NSIndexSet.indexSetWithIndex_(0)
            self.reviewController.appList.selectRowIndexes_byExtendingSelection_(index_set,False)
        except UnboundLocalError:
            pass

    def windowDidLoad(self):
        NSWindowController.windowDidLoad(self)

    def awakeFromNib(self):
        if self.tableView:
            self.tableView.setTarget_(self)

    def show(self):

        try:
            if self.reviewController:
                self.reviewController.close()
        except AttributeError:
            pass

        # open window from NIB file, show front and center
        self.reviewController = ReviewController.alloc().initWithWindowNibName_("Reviewer")
        self.reviewController.showWindow_(None)
        self.reviewController.window().makeKeyAndOrderFront_(None)
        self.reviewController.window().center()
        self.reviewController.retain()

        # needed to show window on top of other applications
        NSNotificationCenter.defaultCenter().postNotificationName_object_('makeAppActive',self)

        # get cmd-w to close window
        self.reviewController.window().standardWindowButton_(NSWindowCloseButton).setKeyEquivalentModifierMask_(NSCommandKeyMask)
        self.reviewController.window().standardWindowButton_(NSWindowCloseButton).setKeyEquivalent_("w")

        self.reviewController.windowArrayController = self.createWindowListController(self).alloc().init()
        self.reviewController.windowArrayController.review_controller = self.reviewController
        self.reviewController.windowList.setDelegate_(self.reviewController.windowArrayController)

        # get arrayController read for Table
        asc = NSSortDescriptor.alloc().initWithKey_ascending_('Data',True)
        descriptiorArray = [asc]
        self.reviewController.arrayController.setSortDescriptors_(descriptiorArray)
        self.reviewController.arrayController.rearrangeObjects()

        frame = NSRect(NSPoint(50, 50), NSSize(600, 100))
        self.timeline_view = CBGraphView.alloc().initWithFrame_(frame)

        self.reviewController.window().contentView().addSubview_(self.timeline_view)
        self.timeline_view.drawRect_(frame)





        self.populateExperienceTable(self)

    show = classmethod(show)
