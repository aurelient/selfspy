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
import re
import string
import errno

import objc
from objc import IBAction, IBOutlet

from Foundation import *
from AppKit import *
from PyObjCTools import AppHelper

import LaunchServices

from Cocoa import (NSEvent, NSScreen,
                   NSKeyDown, NSKeyDownMask, NSKeyUp, NSKeyUpMask,
                   NSLeftMouseUp, NSLeftMouseDown, NSLeftMouseUpMask,
                   NSLeftMouseDownMask, NSRightMouseUp, NSRightMouseDown,
                   NSRightMouseUpMask, NSRightMouseDownMask, NSMouseMoved,
                   NSMouseMovedMask, NSScrollWheel, NSScrollWheelMask,
                   NSFlagsChanged, NSFlagsChangedMask, NSAlternateKeyMask,
                   NSCommandKeyMask, NSControlKeyMask, NSShiftKeyMask,
                   NSAlphaShiftKeyMask, NSApplicationActivationPolicyProhibited,
                   NSURL, NSString, NSTimer,NSInvocation, NSNotificationCenter)

import Quartz
from Quartz import (CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly,
                    kCGWindowListOptionAll, kCGWindowListExcludeDesktopElements,
                    kCGNullWindowID, CGImageGetHeight, CGImageGetWidth)
import Quartz.CoreGraphics as CG

import time
from datetime import datetime
import mutagen.mp4

from selfspy import reviewer
from selfspy import preferences
from selfspy import bookmark
import config as cfg

from urlparse import urlparse

start_time = NSDate.date()


class Sniffer:
    def __init__(self):
        self.key_hook = lambda x: True
        self.mouse_button_hook = lambda x: True
        self.mouse_move_hook = lambda x: True
        self.screen_hook = lambda x: True

        self.regularApps = []
        self.regularWindows = []
        self.last_app_window_check = time.time()
        self.app_window_check_interval = 1.0
        self.windows_to_ignore = ["Focus Proxy", "Clipboard"]

        self.screenSize = [NSScreen.mainScreen().frame().size.width, NSScreen.mainScreen().frame().size.height]
        self.screenRatio = self.screenSize[0]/self.screenSize[1]

        self.delegate = None

    def createAppDelegate(self):
        sc = self

        class AppDelegate(NSObject):
            statusbar = None
            state = 'pause'
            screenshot = True
            recordingAudio = False

            def applicationDidFinishLaunching_(self, notification):
                NSLog("Application did finish launching...")

                # Register preferance defaults for user-facing preferences
                prefDictionary = {}
                prefDictionary[u"screenshots"] = True
                prefDictionary[u'imageSize'] = 720          # in px
                prefDictionary[u"imageTimeMax"] = 60        # in s
                prefDictionary[u"imageTimeMin"] = 100       # in ms
                prefDictionary[u"recording"] = True

                NSUserDefaultsController.sharedUserDefaultsController().setInitialValues_(prefDictionary)

                mask = (NSKeyDownMask
                        # | NSKeyUpMask
                        | NSLeftMouseDownMask
                        # | NSLeftMouseUpMask
                        | NSRightMouseDownMask
                        # | NSRightMouseUpMask
                        | NSMouseMovedMask
                        | NSScrollWheelMask
                        | NSFlagsChangedMask)

                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, sc.handler)

                self.createStatusMenu()

                NSNotificationCenter.defaultCenter().postNotificationName_object_('checkLoops',self)
                NSNotificationCenter.defaultCenter().postNotificationName_object_('noteRecordingState',self)

            def applicationWillTerminate_(self, application):
                # need to release the lock here as when the application terminates it does not run the rest the
                # original main, only the code that has crossed the pyobc bridge.
                NSNotificationCenter.defaultCenter().postNotificationName_object_('closeNotification',self)

                if cfg.LOCK.is_locked():
                    cfg.LOCK.release()
                NSLog("Exiting Selfspy...")

            def toggleLogging_(self, notification):
                NSLog("Toggle Recording")

                recording = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('recording')
                recording = not recording
                NSUserDefaultsController.sharedUserDefaultsController().defaults().setBool_forKey_(recording,'recording')

                NSNotificationCenter.defaultCenter().postNotificationName_object_('checkLoops',self)
                NSNotificationCenter.defaultCenter().postNotificationName_object_('noteRecordingState',self)

                #change text and enabled status of screenshot menu item
                if recording:
                  self.loggingMenuItem.setTitle_("Pause Recording")
                else:
                  self.loggingMenuItem.setTitle_("Start Recording")
                self.changeIcon()

            def changeIcon(self):
                record = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('recording')
                if(record):
                    self.statusitem.setImage_(self.icon)
                else:
                    self.statusitem.setImage_(self.iconGray)

            def bookmark_(self, notification):
                NSLog("Showing Bookmark Window...")
                bookmark.BookmarkController.show()

            def showReview_(self, notification):
                NSLog("Showing Review Window...")
                reviewer.ReviewController.show()

            def showPreferences_(self, notification):
                NSLog("Showing Preference Window...")
                preferences.PreferencesController.show()

            def prepVisualization_(self, notification):
                NSNotificationCenter.defaultCenter().postNotificationName_object_('prepDataForChronoviz',self)

            def createStatusMenu(self):
                NSLog("Creating app menu")
                statusbar = NSStatusBar.systemStatusBar()

                # Create the statusbar item
                self.statusitem = statusbar.statusItemWithLength_(NSVariableStatusItemLength)

                # Load all icon images
                self.icon = NSImage.alloc().initByReferencingFile_('../Resources/eye.png')
                self.icon.setScalesWhenResized_(True)
                self.size_ = self.icon.setSize_((20, 20))
                self.statusitem.setImage_(self.icon)

                self.iconGray = NSImage.alloc().initByReferencingFile_('../Resources/eye_grey.png')
                self.iconGray.setScalesWhenResized_(True)
                self.iconGray.setSize_((20, 20))

                self.changeIcon()

                # Let it highlight upon clicking
                self.statusitem.setHighlightMode_(1)
                # Set a tooltip
                self.statusitem.setToolTip_('Selfspy')

                # Build a very simple menu
                self.menu = NSMenu.alloc().init()
                self.menu.setAutoenablesItems_(False)

                if NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('recording'):
                    menuitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Pause Recording', 'toggleLogging:', '')
                else:
                    menuitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Start Recording', 'toggleLogging:', '')
                self.menu.addItem_(menuitem)
                self.loggingMenuItem = menuitem

                menuitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Preferences...', 'showPreferences:', '')
                self.menu.addItem_(menuitem)

                menuitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Bookmark', 'bookmark:', '')
                self.menu.addItem_(menuitem)

                menuitem = NSMenuItem.separatorItem()
                self.menu.addItem_(menuitem)

                menuitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Select Activity', 'showReview:', '')
                self.menu.addItem_(menuitem)

                menuitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Prepare Visualization', 'prepVisualization:', '')
                self.menu.addItem_(menuitem)

                menuitem = NSMenuItem.separatorItem()
                self.menu.addItem_(menuitem)

                menuitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Quit Selfspy', 'terminate:', '')
                self.menu.addItem_(menuitem)

                # Bind it to the status item
                self.statusitem.setMenu_(self.menu)

                self.statusitem.setEnabled_(TRUE)
                self.statusitem.retain()

        return AppDelegate

    def run(self):
        self.app = NSApplication.sharedApplication()
        self.delegate = self.createAppDelegate().alloc().init()
        self.app.setDelegate_(self.delegate)
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.workspace = NSWorkspace.sharedWorkspace()

        # listen for events thrown by windows
        s = objc.selector(self.makeAppActive_,signature='v@:@')
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(self, s, 'makeAppActive', None)

        AppHelper.runEventLoop()

    def cancel(self):
        AppHelper.stopEventLoop()

    def update_apps_and_windows_(self, event):

        if (time.time() - self.last_app_window_check > self.app_window_check_interval):

            self.regularApps = []
            self.regularWindows = []
            chromeChecked = False
            safariChecked = False

            # get list of apps with regular activation
            activeApps = self.workspace.runningApplications()
            for app in activeApps:
                if app.activationPolicy() == 0: # 0 is the normal activation policy
                    self.regularApps.append(app)

            # get a list of all named windows associated with regular apps
            # also check Chrome and Safari tabs
            windowList = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
            for window in windowList:
                window_name = str(window.get('kCGWindowName', u'').encode('ascii', 'replace'))
                owner = window['kCGWindowOwnerName']
                url = 'NO_URL'
                geometry = window['kCGWindowBounds']

                for app in self.regularApps:
                    if app.localizedName() == owner:
                        if (window_name and window_name not in self.windows_to_ignore):
                            if owner == 'Google Chrome' and not chromeChecked:
                                # get tab info using Applescript
                                s = NSAppleScript.alloc().initWithSource_("tell application \"Google Chrome\" \n set tabs_info to {} \n set window_list to every window \n repeat with win in window_list \n set tab_list to tabs in win \n repeat with t in tab_list \n set the_title to the title of t \n set the_url to the URL of t \n set the_bounds to the bounds of win \n set t_info to {the_title, the_url, the_bounds} \n set end of tabs_info to t_info \n end repeat \n end repeat \n return tabs_info \n end tell")
                                tabs_info = s.executeAndReturnError_(None)

                                if tabs_info[0]:
                                    numItems = tabs_info[0].numberOfItems()
                                    for i in range(1, numItems + 1):
                                        window_name = str(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(1).stringValue().encode('ascii', 'replace'))
                                        if window_name:
                                            url = str(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(2).stringValue())
                                        else:
                                            url = "NO_URL"
                                        x1 = int(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(3).descriptorAtIndex_(1).stringValue())
                                        y1 = int(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(3).descriptorAtIndex_(2).stringValue())
                                        x2 = int(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(3).descriptorAtIndex_(3).stringValue())
                                        y2 = int(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(3).descriptorAtIndex_(4).stringValue())
                                        self.regularWindows.append({'process': 'Google Chrome', 'title': window_name, 'url': url, 'geometry': {'X':x1,'Y':y1,'Width':x2-x1,'Height':y2-y1} })
                                    chromeChecked = True
                            elif owner == 'Safari' and not safariChecked:
                                # get tab info using Applescript
                                s = NSAppleScript.alloc().initWithSource_("tell application \"Safari\" \n set tabs_info to {} \n set winlist to every window \n repeat with win in winlist \n set ok to true \n try \n set tablist to every tab of win \n on error errmsg \n set ok to false \n end try \n if ok then \n repeat with t in tablist \n set thetitle to the name of t \n set theurl to the URL of t \n set thebounds to the bounds of win \n set t_info to {thetitle, theurl, thebounds} \n set end of tabs_info to t_info \n end repeat \n end if \n end repeat \n return tabs_info \n end tell")
                                tabs_info = s.executeAndReturnError_(None)
                                if tabs_info[0]:
                                    numItems = tabs_info[0].numberOfItems()
                                    for i in range(1, numItems + 1):
                                        window_name = str(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(1).stringValue().encode('ascii', 'replace'))
                                        if window_name:
                                            url = str(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(2 ).stringValue())
                                        else:
                                            url = "NO_URL"
                                        x1 = int(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(3).descriptorAtIndex_(1).stringValue())
                                        y1 = int(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(3).descriptorAtIndex_(2).stringValue())
                                        x2 = int(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(3).descriptorAtIndex_(3).stringValue())
                                        y2 = int(tabs_info[0].descriptorAtIndex_(i).descriptorAtIndex_(3).descriptorAtIndex_(4).stringValue())
                                        self.regularWindows.append({'process': 'Safari', 'title': window_name, 'url': url, 'geometry': {'X':x1,'Y':y1,'Width':x2-x1,'Height':y2-y1} })
                                    safariChecked = True
                            else:
                                self.regularWindows.append({'process': owner, 'title': window_name, 'url': url, 'geometry': geometry})


            # get active app, window, url and geometry. only track for regular apps
            for app in self.regularApps:
                if app.isActive():
                    for window in windowList:
                        if (window['kCGWindowNumber'] == event.windowNumber() or (not event.windowNumber() and window['kCGWindowOwnerName'] == app.localizedName())):
                            geometry = window['kCGWindowBounds']

                            # get browser_url
                            browser_url = 'NO_URL'
                            if len(window.get('kCGWindowName', u'').encode('ascii', 'replace')) > 0:
                                if (window.get('kCGWindowOwnerName') == 'Google Chrome'):
                                    s = NSAppleScript.alloc().initWithSource_("tell application \"Google Chrome\" \n return URL of active tab of front window as string \n end tell")
                                    browser_url = s.executeAndReturnError_(None)
                                    browser_url = str(browser_url[0])[33:-3]
                                if (window.get('kCGWindowOwnerName') == 'Safari'):
                                    s = NSAppleScript.alloc().initWithSource_("tell application \"Safari\" \n set theURL to URL of current tab of window 1 \n end tell")
                                    browser_url = s.executeAndReturnError_(None)
                                    browser_url = str(browser_url[0])[33:-3]

                            self.screen_hook(window['kCGWindowOwnerName'],
                                             window.get('kCGWindowName', u'').encode('ascii', 'replace'),
                                             geometry['X'],
                                             geometry['Y'],
                                             geometry['Width'],
                                             geometry['Height'],
                                             browser_url,
                                             self.regularApps,
                                             self.regularWindows)

                            # print "Window Name is " + str(window.get('kCGWindowName', u'').encode('ascii', 'replace'))
                            break
                    break


    def handler(self, event):
        try:
            recording = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('recording')

            if(recording):
                loc = NSEvent.mouseLocation()
                if event.type() == NSLeftMouseDown:
                    self.mouse_button_hook(1, loc.x, loc.y)
                    self.update_apps_and_windows_(event)
                # elif event.type() == NSLeftMouseUp:
                #     self.mouse_button_hook(1, loc.x, loc.y)
                elif event.type() == NSRightMouseDown:
                    self.mouse_button_hook(3, loc.x, loc.y)
                    self.update_apps_and_windows_(event)
                # elif event.type() == NSRightMouseUp:
                    # self.mouse_button_hook(2, loc.x, loc.y)
                elif event.type() == NSScrollWheel:
                    if event.deltaY() > 0:
                        self.mouse_button_hook(4, loc.x, loc.y)
                    elif event.deltaY() < 0:
                        self.mouse_button_hook(5, loc.x, loc.y)
                    if event.deltaX() > 0:
                        self.mouse_button_hook(6, loc.x, loc.y)
                    elif event.deltaX() < 0:
                        self.mouse_button_hook(7, loc.x, loc.y)
                    # if event.deltaZ() > 0:
                        # self.mouse_button_hook(8, loc.x, loc.y)
                    # elif event.deltaZ() < 0:
                        # self.mouse_button_hook(9, loc.x, loc.y)
                elif event.type() == NSKeyDown:
                    self.update_apps_and_windows_(event)
                    flags = event.modifierFlags()
                    modifiers = []  # OS X api doesn't care it if is left or right
                    if flags & NSControlKeyMask:
                        modifiers.append('Ctrl')
                    if flags & NSAlternateKeyMask:
                        modifiers.append('Alt')
                    if flags & NSCommandKeyMask:
                        modifiers.append('Cmd')
                    if flags & (NSShiftKeyMask | NSAlphaShiftKeyMask):
                        modifiers.append('Shift')
                    character = event.charactersIgnoringModifiers()
                    # these two get a special case because I am unsure of
                    # their unicode value
                    if event.keyCode() is 36:
                        character = "Enter"
                        # global hotkey to bookmark
                        if modifiers == ['Cmd', 'Shift']:
                            self.delegate.bookmark_(self)
                    elif event.keyCode() is 51:
                        character = "Backspace"
                    self.key_hook(event.keyCode(),
                                  modifiers,
                                  keycodes.get(character,
                                               character),
                                  event.isARepeat())
                elif event.type() == NSMouseMoved:
                    self.mouse_move_hook(loc.x, loc.y)
        except (SystemExit, KeyboardInterrupt):
            AppHelper.stopEventLoop()
            return
        except:
            AppHelper.stopEventLoop()
            raise

    def makeAppActive_(self, notification):
        self.app.activateIgnoringOtherApps_(True)

    def screenshot(self, path, region = None):
    # https://pythonhosted.org/pyobjc/examples/Quartz/Core%20Graphics/CGRotation/index.html
      try:
        # Set to capture entire screen, including multiple monitors
        if region is None:
          region = CG.CGRectInfinite

        # Create CGImage, composite image of windows in region
        image = CG.CGWindowListCreateImage(
          region,
          CG.kCGWindowListOptionOnScreenOnly,
          CG.kCGNullWindowID,
          CG.kCGWindowImageDefault
        )

        # get min screen coordinates of multiple screens
        scr = NSScreen.screens()
        xmin = 0
        ymin = 0
        for s in scr:
            if s.frame().origin.x < xmin:
                xmin = s.frame().origin.x
            if s.frame().origin.y < ymin:
                ymin = s.frame().origin.y

        # get screen's native size
        nativeHeight = CGImageGetHeight(image)*1.0
        nativeWidth = CGImageGetWidth(image)*1.0
        nativeRatio = nativeWidth/nativeHeight

        # calculate screenshot size based on user preferences
        prefHeight = NSUserDefaultsController.sharedUserDefaultsController().values().valueForKey_('imageSize')
        height = int(prefHeight/scr[0].frame().size.height*nativeHeight)
        width = int(nativeRatio * height)
        heightScaleFactor = height/nativeHeight
        widthScaleFactor = width/nativeWidth

        # get scaled mouse coordinates
        mouseLoc = NSEvent.mouseLocation()
        x = int(mouseLoc.x)
        y = int(mouseLoc.y)
        w = 16
        h = 24
        scale_x = int((x-xmin) * widthScaleFactor)
        scale_y = int((y-h+5-ymin) * heightScaleFactor)
        scale_w = w*widthScaleFactor
        scale_h = h*heightScaleFactor

        #Allocate image data and create context for drawing image
        imageData = LaunchServices.objc.allocateBuffer(int(4 * width * height))
        bitmapContext = Quartz.CGBitmapContextCreate(
          imageData, # image data we just allocated...
          width,
          height,
          8, # 8 bits per component
          4 * width, # bytes per pixel times number of pixels wide
          Quartz.CGImageGetColorSpace(image), # use the same colorspace as the original image
          Quartz.kCGImageAlphaPremultipliedFirst # use premultiplied alpha
        )

        #Draw image on context at new scale
        rect = CG.CGRectMake(0.0,0.0,width,height)
        Quartz.CGContextDrawImage(bitmapContext, rect, image)

        # Add Mouse cursor to the screenshot
        cursorPath = "../Resources/cursor.png"
        cursorPathStr = NSString.stringByExpandingTildeInPath(cursorPath)
        cursorURL = NSURL.fileURLWithPath_(cursorPathStr)

        # Create a CGImageSource object from 'url'.
        cursorImageSource = Quartz.CGImageSourceCreateWithURL(cursorURL, None)

        # Create a CGImage object from the first image in the file. Image
        # indexes are 0 based.
        cursorOverlay = Quartz.CGImageSourceCreateImageAtIndex(cursorImageSource, 0, None)

        Quartz.CGContextDrawImage(bitmapContext,
          CG.CGRectMake(scale_x, scale_y, scale_w, scale_h),
          cursorOverlay)

        #Recreate image from context
        imageOut = Quartz.CGBitmapContextCreateImage(bitmapContext)

        #Image properties dictionary
        dpi = 72 # TODO: Should query this from somewhere, e.g for retina display
        properties = {
          Quartz.kCGImagePropertyDPIWidth: dpi,
          Quartz.kCGImagePropertyDPIHeight: dpi,
          Quartz.kCGImageDestinationLossyCompressionQuality: 0.6,
        }

        #Convert path to url for saving image
        pathWithCursor = path[0:-4] + "_" + str(x) + "_" + str(y) + '.jpg'
        pathStr = NSString.stringByExpandingTildeInPath(pathWithCursor)
        url = NSURL.fileURLWithPath_(pathStr)

        #Set image destination (where it will be saved)
        dest = Quartz.CGImageDestinationCreateWithURL(
          url,
          LaunchServices.kUTTypeJPEG, # file type
          1, # 1 image in file
          None
        )

        # Add the image to the destination, with certain properties
        Quartz.CGImageDestinationAddImage(dest, imageOut, properties)

        # finalize the CGImageDestination object.
        Quartz.CGImageDestinationFinalize(dest)

        # inform of screenshot
        print 'took ' + str(height) + 'px screenshot'

      except KeyboardInterrupt:
          print "Keyboard interrupt"
          AppHelper.stopEventLoop()
      except errno.ENOSPC:
          NSLog("No space left on storage device. Turning off Selfspy recording.")
          self.delegate.toggleLogging_(self)
      except:
          NSLog("couldn't save image")


# Cocoa does not provide a good api to get the keycodes, so we provide our own.
keycodes = {
   u"\u0009": "Tab",
   u"\u001b": "Escape",
   u"\uf700": "Up",
   u"\uF701": "Down",
   u"\uF702": "Left",
   u"\uF703": "Right",
   u"\uF704": "F1",
   u"\uF705": "F2",
   u"\uF706": "F3",
   u"\uF707": "F4",
   u"\uF708": "F5",
   u"\uF709": "F6",
   u"\uF70A": "F7",
   u"\uF70B": "F8",
   u"\uF70C": "F9",
   u"\uF70D": "F10",
   u"\uF70E": "F11",
   u"\uF70F": "F12",
   u"\uF710": "F13",
   u"\uF711": "F14",
   u"\uF712": "F15",
   u"\uF713": "F16",
   u"\uF714": "F17",
   u"\uF715": "F18",
   u"\uF716": "F19",
   u"\uF717": "F20",
   u"\uF718": "F21",
   u"\uF719": "F22",
   u"\uF71A": "F23",
   u"\uF71B": "F24",
   u"\uF71C": "F25",
   u"\uF71D": "F26",
   u"\uF71E": "F27",
   u"\uF71F": "F28",
   u"\uF720": "F29",
   u"\uF721": "F30",
   u"\uF722": "F31",
   u"\uF723": "F32",
   u"\uF724": "F33",
   u"\uF725": "F34",
   u"\uF726": "F35",
   u"\uF727": "Insert",
   u"\uF728": "Delete",
   u"\uF729": "Home",
   u"\uF72A": "Begin",
   u"\uF72B": "End",
   u"\uF72C": "PageUp",
   u"\uF72D": "PageDown",
   u"\uF72E": "PrintScreen",
   u"\uF72F": "ScrollLock",
   u"\uF730": "Pause",
   u"\uF731": "SysReq",
   u"\uF732": "Break",
   u"\uF733": "Reset",
   u"\uF734": "Stop",
   u"\uF735": "Menu",
   u"\uF736": "User",
   u"\uF737": "System",
   u"\uF738": "Print",
   u"\uF739": "ClearLine",
   u"\uF73A": "ClearDisplay",
   u"\uF73B": "InsertLine",
   u"\uF73C": "DeleteLine",
   u"\uF73D": "InsertChar",
   u"\uF73E": "DeleteChar",
   u"\uF73F": "Prev",
   u"\uF740": "Next",
   u"\uF741": "Select",
   u"\uF742": "Execute",
   u"\uF743": "Undo",
   u"\uF744": "Redo",
   u"\uF745": "Find",
   u"\uF746": "Help",
   u"\uF747": "ModeSwitch"}
