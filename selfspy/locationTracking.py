# -*- coding: utf-8 -*-
"""
This file is a translation and heavy modification from Objective-C.
The original copy-right:

//  Copyright 2009 Matt Gallagher. All rights reserved.
//
//  Permission is given to use this source code file, free of charge, in any
//  project, commercial or otherwise, entirely at your risk, with the condition
//  that any redistribution (in part or whole) of source code must retain
//  this copyright and permission notice. Attribution in compiled projects is
//  appreciated but not required.
"""

import objc
import CoreLocation
import WebKit
from CoreLocation import *
from AppKit import NSLog

import math
import datetime

class LocationTracking:
    locationManager = objc.ivar()
    lastSave = datetime.datetime.now()


    def __init__(self):
        self.locationchange_hook = lambda x: True
        self.locationManager = CoreLocation.CLLocationManager.alloc().init()
        # About the CoreLocation Delegates
        # https://developer.apple.com/library/mac/documentation/CoreLocation/Reference/CLLocationManagerDelegate_Protocol/index.html
        self.locationManager.setDelegate_(self)

    def startTracking(self):
        print "start tracking location "
        self.locationManager.startUpdatingLocation()
        # self.locationManager.startMonitoringSignificantLocationChanges()

    #     currentLocation = self.locationManager.location()
    #     print currentLocation


    def getLocation(self):
        # print "location ", self.locationManager._.location
        # print self.locationManager._.location.description()
        currentLocation = self.locationManager.location()
        print currentLocation
    #     # print currentLocation.coordinate.latitude
    #     # print currentLocation.coordinate.longitude


    @classmethod
    def latitudeRangeForLocation_(self, aLocation):
        M = 6367000.0 # approximate average meridional radius of curvature of earth
        metersToLatitude = 1.0 / ((math.pi / 180.0) * M)
        accuracyToWindowScale = 2.0

        return aLocation.horizontalAccuracy() * metersToLatitude * accuracyToWindowScale

    @classmethod
    def longitudeRangeForLocation_(self, aLocation):
        latitudeRange = LocationTracking.latitudeRangeForLocation_(aLocation)

        return latitudeRange * math.cos(aLocation.coordinate().latitude * math.pi / 180.0)

    def distanceMeter(self, lat1, lon1, lat2, lon2):
        R = 6371000  # radius of the earth in m
        x = (lon2 - lon1) * math.cos( 0.5*(lat2+lat1) )
        y = lat2 - lat1
        d = R * math.sqrt( x*x + y*y )
        return d

    def locationManager_didUpdateToLocation_fromLocation_(self,
            manager, newLocation, oldLocation):

        now = datetime.datetime.now()
        
        # Ignore updates where nothing we care about changed
        if newLocation is None:
            return
        if oldLocation is None:
            pass
        else:
            dist = self.distanceMeter(newLocation.coordinate().latitude, newLocation.coordinate().longitude, oldLocation.coordinate().latitude, oldLocation.coordinate().longitude)
            # print "location ", newLocation.coordinate().latitude, newLocation.coordinate().longitude, dist
            if (dist > 50 or ((now - self.lastSave).total_seconds() > 900) ) :
                # print "new location distance : ", dist
                self.locationchange_hook(newLocation.coordinate().latitude,
                    newLocation.coordinate().longitude)
                self.lastSave = now
                # print "record location"

        # TODO what happens in case of new location.
        # newLocation.coordinate().latitude,
        # newLocation.coordinate().longitude,
        # LocationTracking.latitudeRangeForLocation_(newLocation),
        # LocationTracking.longitudeRangeForLocation_(newLocation))

    def locationManager_didFailWithError_(self, manager, error):
        NSLog("location error" + str(error.localizedDescription()))

    def stopTracking(self, aNotification):
        self.locationManager.stopUpdatingLocation()
