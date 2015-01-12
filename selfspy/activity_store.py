# -*- coding: utf-8 -*-
"""
Selfspy: Track your computer activity
Copyright (C) 2012 Bjarte Johansen
Modified 2014 by Adam Rule, Aurélien Tabard, and Jonas Keper

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
import sys

import math

import csv
import json
import zlib

import re
import random

import time
import datetime

import sqlalchemy

from Foundation import *
from AppKit import *

from Cocoa import NSNotificationCenter, NSTimer, NSWorkspace
import Quartz
from Quartz import (CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly,
                    kCGNullWindowID)

from selfspy import sniff_cocoa as sniffer
from selfspy import config as cfg
from selfspy import models
from selfspy.models import (RecordingEvent, Process, ProcessEvent, Window,
                            WindowEvent, Geometry, Click, Keys, Bookmark,
                            FilteredWindowActivation)

from urlparse import urlparse


NOW = datetime.datetime.now
SKIP_MODIFIERS = {"", "Shift_L", "Control_L", "Super_L", "Alt_L", "Super_R",
                    "Control_R", "Shift_R", "[65027]"}  # [65027] is AltGr in X
SCROLL_BUTTONS = {4, 5, 6, 7}
SCROLL_COOLOFF = 10  # seconds


class Display:
    def __init__(self):
        self.proc_id = None
        self.win_id = None
        self.geo_id = None


class KeyPress:
    def __init__(self, key, time, is_repeat):
        self.key = key
        self.time = time
        self.is_repeat = is_repeat


class MouseMove:
    def __init__(self, xy, time):
        self.xy = xy
        self.time = time


class ActivityStore:
    def __init__(self, db_name):
        # check if a selfspy thumbdrive is plugged in and available
        # if so, store screenshots and DB there, otherwise store locally
        self.lookupThumbdrive()
        self.defineCurrentDrive()

        screenshot_directory = os.path.join(cfg.CURRENT_DIR, 'screenshots')
        try:
            os.makedirs(screenshot_directory)
        except OSError:
            pass

        audio_directory = os.path.join(cfg.CURRENT_DIR, 'audio')
        try:
            os.makedirs(audio_directory)
        except OSError:
            pass

        db_name = os.path.join(cfg.CURRENT_DIR, db_name)
        try:
            self.session_maker = models.initialize(db_name)
        except sqlalchemy.exc.OperationalError:
            self.show_alert("Database operational error. Your storage device may be full. Exiting Selfspy...")

            sys.exit()

        self.key_presses = []
        self.mouse_path = []

        self.current_window = Display()
        self.current_apps = []
        self.active_app = ''
        self.current_windows = []
        self.regularWindowsIds = []
        self.active_window = {'title': '', 'process': '', 'url': ''}

        self.last_scroll = {button: 0 for button in SCROLL_BUTTONS}

        self.last_key_time = time.time()
        self.last_move_time = time.time()
        self.last_commit = time.time()
        self.last_screenshot = time.time()

        self.screenshots_active = True
        self.screenshot_time_min = 0.2
        self.screenshot_time_max = 60
        self.thumbdrive_time = 10

        self.addObservers()

        self.started = NOW()

    def run(self):
        self.session = self.session_maker()

        self.sniffer = sniffer.Sniffer()
        self.sniffer.screen_hook = self.got_screen_change
        self.sniffer.key_hook = self.got_key
        self.sniffer.mouse_button_hook = self.got_mouse_click
        self.sniffer.mouse_move_hook = self.got_mouse_move

        self.sniffer.run()

    def addObservers(self):
        # Listen for events from the Preferences window
        s = objc.selector(self.checkMaxScreenshotOnPrefChange_,signature='v@:@')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'changedMaxScreenshotPref', None)

        s = objc.selector(self.clearData_,signature='v@:@')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'clearData', None)

        s = objc.selector(self.getAppsAndWindows_,signature='v@:@')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'getAppsAndWindows', None)

        s = objc.selector(self.getFilteredWindowEvents_,signature='v@:@')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'getFilteredWindowEvents', None)

        # Listen for events thrown by the Status bar menu
        s = objc.selector(self.checkLoops_,signature='v@:@')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'checkLoops', None)

        s = objc.selector(self.noteRecordingState_,signature='v@:@')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'noteRecordingState', None)

        # Listen for events from Bookmark window
        s = objc.selector(self.recordBookmark_,signature='v@:@')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'recordBookmark', None)

        # Listen for close of Selfspy
        s = objc.selector(self.gotCloseNotification_,signature='v@:')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'closeNotification', None)

        # Listen for data requests
        s = objc.selector(self.prepDataForChronoviz_,signature='v@:')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'prepDataForChronoviz', None)

    ### Loop Functions ###
    def checkLoops_(self, notification):
        recording = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('recording')
        if(recording):
            self.startLoops()
        else:
            self.stopLoops()

    def startLoops(self):
        # Timer for taking screenshots when idle
        s = objc.selector(self.runMaxScreenshotLoop,signature='v@:')
        self.screenshotTimer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(self.screenshot_time_max, self, s, None, False)

        # Timer for checking if thumbdrive/memory card is available
        s = objc.selector(self.defineCurrentDrive,signature='v@:')
        self.thumbdriveTimer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(self.thumbdrive_time, self, s, None, True)
        self.thumbdriveTimer.fire() # get location immediately

    def stopLoops(self):
        try:
            if self.screenshotTimer:
                self.screenshotTimer.invalidate()
            if self.thumbdriveTimer:
                self.thumbdriveTimer.invalidate()
        except(AttributeError):
            pass

    ### Event Hook Functions ###
    def got_screen_change(self, process_name, window_name, win_x, win_y, win_width, win_height, browser_url, regularApps, regularWindows):
        """ Receives a screen change and stores any changes. If the process or window has
            changed it will also store any queued pressed keys.
            process_name is the name of the process running the current window
            window_name is the name of the window
            win_x is the x position of the window
            win_y is the y position of the window
            win_width is the width of the window
            win_height is the height of the window """

        # check if current process, window, or geometry are new
        # if so, update currents, store keys, and take screenshot
        # if the event occured on a new process or window, update currents
        windows_to_ignore = ["Focus Proxy", "Clipboard"]
        if window_name not in windows_to_ignore:

            # add process to db if not already there
            cur_process = self.session.query(Process).filter_by(name=process_name).scalar()
            if not cur_process:
                cur_process = Process(process_name)
                self.session.add(cur_process)
                self.trycommit()
                cur_process = self.session.query(Process).filter_by(name=process_name).scalar()

            # check if process is same as before, if not, update active process
            if cur_process.name != self.active_app:
                process_event = ProcessEvent(cur_process.id, "Active")
                self.session.add(process_event)
                self.active_app = cur_process.name

            # add Window to db if not already there
            if browser_url == "NO_URL":
                cur_window = self.session.query(Window).filter_by(title=window_name, process_id=cur_process.id).scalar()
            else:
                cur_window = self.session.query(Window).filter_by(process_id=cur_process.id, title=window_name, browser_url=browser_url).scalar()

            if not cur_window:
                cur_window = Window(window_name, cur_process.id, browser_url)
                self.session.add(cur_window)
                self.trycommit()
                cur_window = self.session.query(Window).filter_by(process_id=cur_process.id, title=window_name, browser_url=browser_url).scalar()

            # check if window is same as before, if not, update active window
            if (cur_window.title != self.active_window['title'] or cur_window.process_id != self.active_window['process'] or cur_window.browser_url != self.active_window['url']):
                window_event = WindowEvent(cur_window.id, "Active")
                self.session.add(window_event)
                self.trycommit()
                self.active_window = {'title': window_name, 'process': cur_process.id, 'url': browser_url}

            # check if geomerty is the same as before
            cur_geometry = self.session.query(Geometry).filter_by(xpos=win_x, ypos=win_y, width=win_width, height=win_height).scalar()
            if not cur_geometry:
                cur_geometry = Geometry(win_x, win_y, win_width, win_height)
                self.session.add(cur_geometry)
                self.trycommit()
                cur_geometry = self.session.query(Geometry).filter_by(xpos=win_x, ypos=win_y, width=win_width, height=win_height).scalar()

            # if its a new window, commit changes and update ids
            if (self.current_window.proc_id != cur_process.id
                    or self.current_window.win_id != cur_window.id):
                self.trycommit()
                self.store_keys()  # happens before as these keypresses belong to the previous window
                self.current_window.proc_id = cur_process.id
                self.current_window.win_id = cur_window.id
                self.current_window.geo_id = cur_geometry.id
                self.take_screenshot()

                # find apps that have opened or become active since the last check
                for app in regularApps:
                    # get app's db entry, or create one for it
                    db_process = self.session.query(Process).filter_by(name=app.localizedName()).scalar()
                    if not db_process:
                        process_to_add = Process(app.localizedName())
                        self.session.add(process_to_add)
                        self.trycommit()
                        db_process = self.session.query(Process).filter_by(name=app.localizedName()).scalar()
                    process_id = db_process.id

                    # if app has opened since last check, add Open process event to db
                    if app not in self.current_apps:
                        process_event = ProcessEvent(process_id, "Open")
                        self.session.add(process_event)
                        self.current_apps.append(app)

                # find apps that have closed since the last check
                for app in self.current_apps:
                    if app not in regularApps:
                        db_process = self.session.query(Process).filter_by(name=app.localizedName()).scalar()
                        process_id = db_process.id
                        process_event = ProcessEvent(process_id, "Close")
                        self.session.add(process_event)
                        self.trycommit()
                        try:
                            self.current_apps.remove(app)
                        except ValueError:
                            print("Error: Can not remove app from list. It does not seem to exist.")

                # find windows that have opened or become active since the last check
                for window in regularWindows:
                    # get id of process in database
                    process = self.session.query(Process).filter_by(name=window['process']).scalar()
                    pid = process.id if process else 0
                    geometry = window['geometry']

                    # add new windows and tabs to the database
                    db_window = self.session.query(Window).filter_by(title=window['title'], process_id=pid, browser_url=window['url']).scalar()
                    if not db_window:
                        window_to_add = Window(window['title'], pid, window['url'])
                        self.session.add(window_to_add)
                        self.trycommit()
                        db_window = self.session.query(Window).filter_by(title=window['title'], process_id=pid, browser_url=window['url']).scalar()
                    window_id = db_window.id
                    self.regularWindowsIds.append(window_id)

                    if window_id not in self.current_windows:
                        window_event = WindowEvent(window_id, "Open")
                        self.session.add(window_event)
                        self.trycommit()
                        self.current_windows.append(window_id)

                    # add new geometries to the database
                    db_geometry = self.session.query(Geometry).filter_by(xpos=geometry['X'], ypos=geometry['Y'], width=geometry['Width'], height=geometry['Height']).scalar()
                    if not db_geometry:
                        geometry_to_add = Geometry(geometry['X'], geometry['Y'], geometry['Width'], geometry['Height'])
                        self.session.add(geometry_to_add)
                        self.trycommit()
                        db_geometry = self.session.query(Geometry).filter_by(xpos=geometry['X'], ypos=geometry['Y'], width=geometry['Width'], height=geometry['Height']).scalar()
                    geometry_id = db_geometry.id

                # find windows that have closed since the last check
                for window_id in self.current_windows:
                    if window_id not in self.regularWindowsIds:
                        window_event = WindowEvent(window_id, "Close")
                        self.session.add(window_event)
                        self.trycommit()
                        try:
                            self.current_windows.remove(window_id)
                        except ValueError:
                            print("Error: Can not remove window_id from list. It does not seem to exist.")

    def filter_many(self):
        specials_in_row = 0
        lastpress = None
        newpresses = []

        for press in self.key_presses:
            key = press.key
            if specials_in_row and key != lastpress.key:
                if specials_in_row > 1:
                    lastpress.key = '%s]x%d>' % (lastpress.key[:-2], specials_in_row)
                newpresses.append(lastpress)
                specials_in_row = 0

            if len(key) > 1:
                specials_in_row += 1
                lastpress = press
            else:
                newpresses.append(press)

        if specials_in_row:
            if specials_in_row > 1:
                lastpress.key = '%s]x%d>' % (lastpress.key[:-2], specials_in_row)
            newpresses.append(lastpress)

        self.key_presses = newpresses

    def store_keys(self):
        """ Stores the current queued key-presses """
        self.filter_many()

        if self.key_presses:
            keys = [press.key for press in self.key_presses]
            timings = [press.time for press in self.key_presses]
            add = lambda count, press: count + (0 if press.is_repeat else 1)
            nrkeys = reduce(add, self.key_presses, 0)

            # we don't store the keys pressed for privacy reasons
            # but we do keep their timings and numbers.
            curtext = u""
            keys = []

            self.session.add(Keys(curtext.encode('utf8'),
                                  keys,
                                  timings,
                                  nrkeys,
                                  self.started,
                                  self.current_window.proc_id,
                                  self.current_window.win_id,
                                  self.current_window.geo_id))

            self.trycommit()

            self.started = NOW()
            self.key_presses = []
            self.last_key_time = time.time()

    def got_key(self, keycode, state, string, is_repeat):
        """ Receives key-presses and queues them for storage.
            keycode is the code sent by the keyboard to represent the pressed key
            state is the list of modifier keys pressed, each modifier key should be represented
                  with capital letters and optionally followed by an underscore and location
                  specifier, i.e: SHIFT or SHIFT_L/SHIFT_R, ALT, CTRL
            string is the string representation of the key press
            repeat is True if the current key is a repeat sent by the keyboard """
        now = time.time()

        if string in SKIP_MODIFIERS:
            return

        if len(state) > 1 or (len(state) == 1 and state[0] != "Shift"):
            string = '<[%s: %s]>' % (' '.join(state), string)
        elif len(string) > 1:
            string = '<[%s]>' % string

        self.key_presses.append(KeyPress(string, now - self.last_key_time, is_repeat))
        self.last_key_time = now

        self.take_screenshot()

    def store_click(self, button, x, y):
        """ Stores incoming mouse-clicks """

        #Put mouse locations and timings in arrays
        locs = [loc.xy for loc in self.mouse_path]
        timings = [loc.time for loc in self.mouse_path]

        self.session.add(Click(button,
                               True,
                               x, y,
                               len(self.mouse_path),
                               locs,
                               timings,
                               self.current_window.proc_id,
                               self.current_window.win_id,
                               self.current_window.geo_id))
        self.mouse_path = []
        self.trycommit()

    def got_mouse_click(self, button, x, y):
        """ Receives mouse clicks and sends them for storage.
            Mouse buttons: left: 1, middle: 2, right: 3, scroll up: 4, down:5, left:6, right:7
            x,y are the coordinates of the keypress
            press is True if it pressed down, False if released"""
        if button in [4, 5, 6, 7]:
            if time.time() - self.last_scroll[button] < SCROLL_COOLOFF:
                return
            self.last_scroll[button] = time.time()
        # it seems that the macpro trackpad triggers fake clicks when touched
        # elif button == 1: #if a "real" click happens we take a screenshot
        self.take_screenshot()
        self.store_click(button, x, y)

    def got_mouse_move(self, x, y):
        """ Queues mouse movements at 10Hz.
            x,y are the new coordinates on moving the mouse"""
        frequency = 10.0
        now = time.time()

        if now-self.last_move_time > 1/frequency:
            self.mouse_path.append(MouseMove([x,y], now - self.last_move_time))
            self.last_move_time = now

    ### Misc Functions ###
    def trycommit(self):
        self.last_commit = time.time()
        for _ in xrange(1000):
            try:
                self.session.commit()
                break
            except sqlalchemy.exc.OperationalError:
                if(NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('recording')):
                    self.sniffer.delegate.toggleLogging_(self)
                self.session.rollback()

                self.show_alert("Database operational error. Your storage device may be full. Turning off Selfspy recording.")

                break
            except:
                print "Rollback"
                self.session.rollback()

    def show_alert(self, message):
        print message

        alert = NSAlert.alloc().init()
        alert.addButtonWithTitle_("OK")
        alert.setMessageText_(message)
        alert.setAlertStyle_(NSWarningAlertStyle)
        alert.runModal()

    def recordBookmark_(self, notification):
        # write bookmark to database
        t = notification.object().t
        doing_report = notification.object().doingText.stringValue()
        audio_file = notification.object().audio_file
        time_since = notification.object().timeDropdown.titleOfSelectedItem()

        self.session.add(Bookmark(t, doing_report, audio_file, time_since))
        self.trycommit()

        # close Bookmark window and return focus to previous app
        notification.object().close()
        self.sniffer.app.hide_(notification)

    def getAppsAndWindows_(self, notification):
        controller = notification.object().reviewController
        controller.results = NSMutableArray([])

        try:
            q_apps = self.session.query(Process).all()
            q_windows = self.session.query(Window).all()

            for a in q_apps:
                app_dict = NSMutableDictionary({'checked':False, 'image':'', 'appId':NSMutableArray([a.id]), 'appName': a.name, 'windows':NSMutableArray([]), 'windows_mixed':NSMutableArray([])})
                controller.results.append(app_dict)

            for w in q_windows:
                if w.browser_url == 'NO_URL' or w.browser_url == '' or not w.browser_url:
                    windowName = w.title if w.title else 'NO_TITLE'
                    window_dict = NSMutableDictionary({'checked':False, 'windowId':NSMutableArray([w.id]), 'windowName':windowName, 'image':''})
                    controller.results[w.process_id-1]['windows'].append(window_dict)
                else:
                    short_url = urlparse(w.browser_url).hostname
                    try:
                        window_dict = (d for d in controller.results[w.process_id-1]['windows'] if d['windowName'] == short_url).next()
                        window_dict['windowId'].append(w.id)
                    except:
                        window_dict = NSMutableDictionary({'checked':False, 'windowId':NSMutableArray([w.id]), 'windowName':short_url, 'image':''})
                        controller.results[w.process_id-1]['windows'].append(window_dict)

        except UnicodeEncodeError:
                pass

    def getFilteredWindowEvents_(self, notification):

        controller = notification.object().reviewController
        windows_to_watch = []
        filtered_events = []
        past_event = None

        # clear the contents of the FilteredWindowActivations table
        self.session.query(FilteredWindowActivation).delete()

        # get list of selected windows
        for app in controller.results:
            for wind in app['windows']:
                if wind['checked']:
                    for i in wind['windowId']:
                        windows_to_watch.append(i)

        # filter window events from db down to only selected windows
        q = self.session.query(WindowEvent).join(WindowEvent.window).all()

        for e in q:
            if e.event_type == 'Active':
                if past_event:
                    filtered_events.append([past_event.window_id,
                                            "No_name",
                                            past_event.window.title,
                                            past_event.created_at,
                                            e.created_at])
                past_event = e
            elif past_event and e.window_id == past_event.window_id and e.event_type == 'Close':
                filtered_events.append([past_event.window_id,
                                        "No_name",
                                        past_event.window.title,
                                        past_event.created_at,
                                        e.created_at])
                past_event = None

        # add events from watched windows to the table
        for e in filtered_events:
            if e[0] in windows_to_watch:
                event = FilteredWindowActivation(e[0], e[1], e[2], e[3], e[4])
                self.session.add(event)
            self.trycommit()

    def checkMaxScreenshotOnPrefChange_(self, notification):
        self.screenshotTimer.invalidate()
        self.runMaxScreenshotLoop()

    def runMaxScreenshotLoop(self):
        self.screenshot_time_max = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('imageTimeMax')
        time_since_last_screenshot = time.time() - self.last_screenshot
        if (time_since_last_screenshot > self.screenshot_time_max):
            self.take_screenshot()
            time_since_last_screenshot = 0.0
        sleep_time = self.screenshot_time_max - time_since_last_screenshot + 0.01
        s = objc.selector(self.runMaxScreenshotLoop,signature='v@:')
        self.screenshotTimer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(sleep_time, self, s, None, False)

    def clearData_(self, notification):
        # get time to delete from
        minutes_to_delete = notification.object().clearDataPopup.selectedItem().tag()
        text = notification.object().clearDataPopup.selectedItem().title()

        if minutes_to_delete == -1:
            delete_from_time = datetime.datetime.min
        else:
            delta = datetime.timedelta(minutes=minutes_to_delete)
            now = datetime.datetime.now()
            delete_from_time = now - delta

        # delete data from all tables
        q = self.session.query(Click).filter(Bookmark.created_at > delete_from_time).delete()
        q = self.session.query(Click).filter(Click.created_at > delete_from_time).delete()
        q = self.session.query(Debrief).filter(FilteredWindowActivation.created_at > delete_from_time).delete()
        q = self.session.query(Geometry).filter(Geometry.created_at > delete_from_time).delete()
        q = self.session.query(Keys).filter(Keys.created_at > delete_from_time).delete()
        q = self.session.query(Process).filter(Process.created_at > delete_from_time).delete()
        q = self.session.query(Process).filter(ProcessEvent.created_at > delete_from_time).delete()
        q = self.session.query(Process).filter(RecordingEvent.created_at > delete_from_time).delete()
        q = self.session.query(Window).filter(Window.created_at > delete_from_time).delete()
        q = self.session.query(Window).filter(WindowEvent.created_at > delete_from_time).delete()

        # delete screenshots
        screenshot_directory = os.path.expanduser(os.path.join(cfg.CURRENT_DIR,"screenshots"))
        screenshot_files = os.listdir(screenshot_directory)

        for f in screenshot_files:
            if f[0:19] > delete_from_time.strftime("%y%m%d-%H%M%S%f") or  minutes_to_delete == -1 :
                os.remove(os.path.join(screenshot_directory,f))

        # delete audio files
        audio_directory = os.path.expanduser(os.path.join(cfg.CURRENT_DIR,"audio"))
        audio_files = os.listdir(screenshot_directory)

        for f in audio_files:
            if f[0:19] > delete_from_time.strftime("%y%m%d-%H%M%S%f") or  minutes_to_delete == -1 :
                os.remove(os.path.join(audio_directory,f))

        print "You deleted the last " + text + " of your history"

    def take_screenshot(self):
      # We check whether the screenshot option is on and then limit the screenshot taking rate to user defined rate
      self.screenshots_active = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('screenshots')
      self.screenshot_time_min = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('imageTimeMin') / 1000.0

      if (self.screenshots_active
        and (time.time() - self.last_screenshot) > self.screenshot_time_min) :
          try:
              folder = os.path.join(cfg.CURRENT_DIR,"screenshots")
              filename = datetime.datetime.now().strftime("%y%m%d-%H%M%S%f")
              path = os.path.join(folder,""+filename+".jpg")

              self.sniffer.screenshot(path)
              self.last_screenshot = time.time()
          except:
              print "error with image backup"

    ### Thumbdrive Functions ###
    def lookupThumbdrive(self, namefilter=""):
        for dir in os.listdir('/Volumes') :
            if namefilter in dir :
                volume = os.path.join('/Volumes', dir)
                if (os.path.ismount(volume)) :
                    subDirs = os.listdir(volume)
                    for filename in subDirs:
                        if "selfspy.cfg" == filename :
                            print "backup drive found ", volume
                            cfg.THUMBDRIVE_DIR = volume
                            return cfg.THUMBDRIVE_DIR
        return None

    def defineCurrentDrive(self):
        if (self.isThumbdrivePlugged()) :
            cfg.CURRENT_DIR = cfg.THUMBDRIVE_DIR
        else :
            cfg.CURRENT_DIR = os.path.expanduser(cfg.LOCAL_DIR)

    def isThumbdrivePlugged(self):
        if (cfg.THUMBDRIVE_DIR != None and cfg.THUMBDRIVE_DIR != ""):
            if (os.path.ismount(cfg.THUMBDRIVE_DIR)):
                return True
            else :
                print "Thumbdrive defined but not plugged\n TODO: display alert message"
                cfg.THUMBDRIVE_DIR = None
                self.lookupThumbdrive()
                return False
        else :
            return False

    ### Closing Functions ###
    def close(self):
        """ stops the sniffer and stores the latest keys and close of programs. To be used on shutdown of program"""
        self.sniffer.cancel()
        self.store_keys()

    def gotCloseNotification_(self, notification):
        """ adds "Close" entry to DB for each app open at the close of Selfspy """

        for app in self.current_apps:
            db_process = self.session.query(Process).filter_by(name=app.localizedName()).scalar()
            process_id = 0
            if db_process:
                process_id = db_process.id
            process_event = ProcessEvent(process_id, "Close")
            self.session.add(process_event)

        for window_id in self.current_windows:
            db_window = self.session.query(Window).filter_by(id=window_id).scalar()
            window_id = db_window.id if db_window else 0
            pid = db_window.process_id if db_window else 0
            window_event = WindowEvent(window_id, "Close")
            self.session.add(window_event)

        recording_event = RecordingEvent(NOW(), "Off")
        self.session.add(recording_event)
        self.trycommit()

    def noteRecordingState_(self, notification):
        recording = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('recording')
        value = "On" if recording else "Off"
        recording_event = RecordingEvent(NOW(), value)
        self.session.add(recording_event)

    def prepDataForChronoviz_(self, notification):
        # get time from first image
        screenshot_directory = os.path.expanduser(os.path.join(cfg.CURRENT_DIR,"screenshots"))
        screenshot_files = os.listdir(screenshot_directory)
        start_time = datetime.datetime.strptime(screenshot_files[0][0:19], "%y%m%d-%H%M%S%f")

        # create csvs for visualization
        self.getBookmarks_(start_time)
        self.getClicksPerMinute_(start_time)
        self.getKeystrokesPerMinute_(start_time)
        self.getAppWindowEvents_(start_time)

    def getBookmarks_(self, start_time):
        q = self.session.query(Bookmark).all()
        file = os.path.join(cfg.CURRENT_DIR, 'bookmark.csv')

        # remove file if already exists
        if os.path.isfile(file):
            os.remove(file)

        with open(file, 'wb') as csvfile:
            writer = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            writer.writerow(['time'] + ['text'] + ['audio'] + ['delay'])

            for row in q:
                bookmark_time = datetime.datetime.strptime(row.time[0:26], "%Y-%m-%d %H:%M:%S.%f")
                time_diff = bookmark_time - start_time
                writer.writerow([time_diff.total_seconds()] + [row.text] + [row.audio] + [row.delay])

    def getClicksPerMinute_(self, start_time):
        q = self.session.query(Click).all()
        file = os.path.join(cfg.CURRENT_DIR, 'clicks_per_minute.csv')

        # remove file if already exists
        if os.path.isfile(file):
            os.remove(file)

        click_start_time = q[0].created_at[0:16]
        end_time = q[-1].created_at[0:16]
        ref_time = datetime.datetime.strptime(click_start_time, '%Y-%m-%d %H:%M')

        clicks_per_minute = []
        counter = 0
        i = 0

        while i < len(q):
            current_time = datetime.datetime.strptime(q[i].created_at[0:16], '%Y-%m-%d %H:%M')

            if current_time <= ref_time:
                counter += 1
                i += 1
            else:
                time_since_start = ref_time - start_time
                clicks_per_minute.append([time_since_start.total_seconds(), counter])
                counter = 0
                ref_time = ref_time + datetime.timedelta(minutes=1)

        with open(file, 'wb') as csvfile:
            writer = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            writer.writerow(['time'] + ['clicks'])

            for row in clicks_per_minute:
                writer.writerow([row[0]] + [row[1]])

    def getKeystrokesPerMinute_(self, start_time):
        file = os.path.join(cfg.CURRENT_DIR, 'keys_per_minute.csv')
        rec_index = 0
        current_time = None
        relative_times = []

        q = self.session.query(RecordingEvent).filter_by(event_type="On").all()
        recording_times = [[datetime.datetime.strptime(r.time, "%Y-%m-%d %H:%M:%S.%f"), False] for r in q]

        q = self.session.query(Keys).all()
        for r in q:
            row_time = datetime.datetime.strptime(r.created_at, "%Y-%m-%d %H:%M:%S.%f")

            while(rec_index < len(recording_times)-2 and row_time > recording_times[rec_index + 1][0]):
                rec_index += 1

            key_timings = zlib.decompress(r.timings)
            key_timings = map(float, key_timings[1:-2].split(','))
            for key_time in key_timings:
                if recording_times[rec_index][1]:
                    current_time = current_time + key_time
                else:
                    diff_time = recording_times[rec_index][0] - start_time
                    diff_time = diff_time.total_seconds()
                    current_time = diff_time + key_time
                    recording_times[rec_index][1] = True
                relative_times.append(current_time)

        keys_per_minute = []

        for i in range(int(math.ceil(relative_times[-1]/60))):
            keys_per_minute.append([0,0])

        # print keys_per_minute
        for i, val in enumerate(keys_per_minute):
            val[0] = 60 * i

        for t in relative_times:
            keys_per_minute[int(t/60)][1] += 1

        # remove file if already exists
        if os.path.isfile(file):
            os.remove(file)

        with open(file, 'wb') as csvfile:
            writer = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            writer.writerow(['time'] + ['keys'])

            for row in keys_per_minute:
                writer.writerow([row[0]] + [row[1]])

    def getAppWindowEvents_(self, start_time):
        app_window_list = defaults = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('appWindowList')
        file = os.path.join(cfg.CURRENT_DIR, 'activity_events.csv')
        # controller = notification.object().reviewController
        windows_to_watch = []
        filtered_events = []
        past_event = None

        # clear the contents of the FilteredWindowActivations table
        self.session.query(FilteredWindowActivation).delete()

        # get list of selected windows
        for app in app_window_list:
            for wind in app['windows']:
                if wind['checked']:
                    for i in wind['windowId']:
                        windows_to_watch.append(i)

        # filter window events from db down to only selected windows
        q = self.session.query(WindowEvent).join(WindowEvent.window).all()

        for e in q:
            if e.event_type == 'Active':
                if past_event:
                    filtered_events.append([past_event.window_id,
                                            past_event.window.process_id,
                                            past_event.window.title,
                                            past_event.created_at,
                                            e.created_at])
                past_event = e
            elif past_event and e.window_id == past_event.window_id and e.event_type == 'Close':
                filtered_events.append([past_event.window_id,
                                        past_event.window.process_id,
                                        past_event.window.title,
                                        past_event.created_at,
                                        e.created_at])
                past_event = None

        # remove file if already exists
        if os.path.isfile(file):
            os.remove(file)

        with open(file, 'wb') as csvfile:
            writer = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            writer.writerow(['window_id'] + ['process_id'] + ['window_name'] + ['open_time'] + ['close_time'])

            for row in filtered_events:
                time_diff = datetime.datetime.strptime(row[3][0:26], "%Y-%m-%d %H:%M:%S.%f") - start_time
                open_time = time_diff.total_seconds()

                time_diff = datetime.datetime.strptime(row[4][0:26], "%Y-%m-%d %H:%M:%S.%f") - start_time
                close_time = time_diff.total_seconds()

                writer.writerow([row[0]] + [row[1]]  + [row[2]]  + [open_time]  + [close_time])

        # add events from watched windows to the table
        # for e in filtered_events:
        #     if e[0] in windows_to_watch:
        #         event = FilteredWindowActivation(e[0], e[1], e[2], e[3], e[4])
        #         self.session.add(event)
        #     self.trycommit()
