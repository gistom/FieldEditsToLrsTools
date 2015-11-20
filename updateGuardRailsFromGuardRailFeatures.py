#-------------------------------------------------------------------------------
# Name:        updateGuardRailsFromGuardRailFeatures.py
# Purpose: This script takes a line feature that has a relationship with a route
#           and creates a corresponding event using an Esri Roads and Highways
#           web service.
#
# Author:      Tom Brenneman
#
# Created:     10/01/2015
#-------------------------------------------------------------------------------
import arcpy, json, urllib, operator, uuid, calendar, datetime, os, arcpyEditor, sys

class LrsLocation:
    def __init__(self, oid, routeID, eventID, effectiveDate, editUser, editDate, side):
        self.oid = oid
        self.routeID = str(routeID)
        self.eventID = eventID
        self.effectiveDate = effectiveDate
        self.editUser = editUser
        self.editDate = editDate
        self.side = str(side)
        self.measure = None
        self.locationStatus = ''
        self.additionalAttrbutes = {}

class EventLocation:
    def __init__(self, lrsLocation):
        self.fromLocation = lrsLocation
        self.toLocation = None
        self.eventID = str(uuid.uuid1())
    def addLocation(self, location):
        if self.fromLocation is None:
            self.fromLocation = location
        elif self.fromLocation.measure > location.measure:
            self.toLocation = self.fromLocation
            self.fromLocation = location
        else:
            self.toLocation = location

def getWorkspace(path, ws_type=''):
    ws = os.path.dirname(path)
    desc = arcpy.Describe(ws)
    if not desc.dataType == 'Workspace':
        return find_ws(ws)
    else:
        return ws

def getVersion(workspace, name):
    version = None
    for v in arcpy.da.ListVersions(workspace):
        if v.name.upper() == name.upper():
            version = v
            break
    return version

def main():
    fc = "D:/GIS_DATA/INDOT/dataEditor_InDOT@localhost.sde/InDOT.DBO.GuardRails"
    eventLayerID = 12
    geomToMeasureURL = 'http://dot.esri.com/arcgis/rest/services/INDOT/INDOT_Routes_and_Events/MapServer/exts/LRSServer/networkLayers/15/geometryToMeasure'
    applyEditsURL = 'http://dot.esri.com/arcgis/rest/services/INDOT/INDOT_Routes_and_Events/MapServer/exts/LRSServer/applyEdits'

    #Fields in data collection feature class
    oidField = 'OBJECTID'
    routeIFieldD = 'RouteID'
    eventIDField = 'EventID'
    lastEditUserField = 'last_edited_user'
    lastEditDateField = 'last_edited_date'
    sideField = 'GUARDRAIL_POSITION' #Assumed to be the same field name in the event table
    effectiveDateField = 'EffectiveDateOfChange'
    processedDateField = 'ProcessedDate'

    #Fields in internal Event
    eventRouteID = 'ROUTE_ID'
    eventFromMeasureField = 'FROM_MEASURE'
    eventToMeasureField = 'TO_MEASURE'
    eventFromDate = 'FROM_DATE'
    eventEventIDField = 'EVENT_ID'

    #Attribute fields to map format: [pointField, eventField]
    additionalAttributeFields = ['GUARDRAIL_END_TYPE','GUARDRAIL_TYPE']

    #End of configuration
    processedDate = datetime.datetime.now()
    fields = [oidField, 'SHAPE@', routeIFieldD, eventIDField, effectiveDateField, lastEditUserField, lastEditDateField, sideField, processedDateField]
    fields += additionalAttributeFields
    fieldIndex = {}
    for x in range(len(fields)):
        fieldIndex[fields[x]] = x

    expression = '{0} IS NULL'.format(arcpy.AddFieldDelimiters(fc, processedDateField))
    #expression = "{0} > '{1}'".format(arcpy.AddFieldDelimiters(fc, createdDateField), arcpy.AddFieldDelimiters(fc, lastEditDateField))
    geomToMjson = []
    pointList = []
    with arcpy.da.SearchCursor(fc, fields, where_clause=expression) as cursor:
        for row in cursor:
            polyLine = row[1] #arcpy.Polyline(row[1])
            for point in [polyLine.firstPoint, polyLine.lastPoint]:
                geomToMjson += [{"routeId": row[2], "geometry": {"x": point.X, "y":  point.Y}}]
                #pointList += [{'oid':row[0], 'routeID':row[2], 'eventID':row[3], 'effectiveDate':row[4], 'editUser':row[5],'editDate':row[6], 'side':row[7]}]
                pointLocation = LrsLocation(row[0], row[2], row[3], row[4], row[5], row[6], row[7])
                for field in additionalAttributeFields:
                    val = row[fieldIndex[field]]
                    if isinstance(val, unicode):
                        val = str(val) #Convert unicode to ascii string
                    pointLocation.additionalAttrbutes[field] = val
                pointList += [pointLocation]
    requestJSON = json.dumps(geomToMjson)
    print requestJSON

    params = urllib.urlencode({'f': 'json', 'locations': requestJSON, 'tolerance': 300})
    #geomToMRequest = "{0}?{1}".format(geomToMeasureURL, params)
    #print geomToMRequest
    #f = urllib.urlopen(geomToMRequest) #Get request
    f = urllib.urlopen(geomToMeasureURL, params) #Post
    response = json.loads(f.read())
    #print response
    for x in range(len(pointList)):
        pointList[x].locationStatus = response['locations'][x]['status']
        if pointList[x].locationStatus == 'esriLocatingOK':
            m = response['locations'][x]['results'][0]['measure']
            #pointList[x]['Measure'] = m
            pointList[x].measure = m


    eventLocations = []
    newEventLocation = None
    for pointItem in sorted(pointList, key=operator.attrgetter('routeID', 'side', 'measure')):
        print '{0} at Measure {1}'.format(pointItem.oid, pointItem.editDate)
        if newEventLocation is None or newEventLocation.fromLocation.routeID != pointItem.routeID or newEventLocation.fromLocation.side != pointItem.side:
            newEventLocation = EventLocation(pointItem)
        else:
            newEventLocation.addLocation(pointItem)
            eventLocations += [newEventLocation]
            newEventLocation = None

    #Exit if there are no event locations to update
    if len(eventLocations) == 0:
        print 'No events to update'
        return

    #Make the updates
    updatesList = []
    for eventLocation in eventLocations:
        attributes = {}
        attributes[eventRouteID] = eventLocation.fromLocation.routeID
        attributes[eventEventIDField] = eventLocation.eventID
        attributes[eventFromMeasureField] = eventLocation.fromLocation.measure
        attributes[eventToMeasureField] = eventLocation.toLocation.measure
        if not eventLocation.fromLocation.effectiveDate is None:
            attributes[eventFromDate] = calendar.timegm(eventLocation.fromLocation.effectiveDate.timetuple())
        attributes[sideField] = eventLocation.fromLocation.side
        for attrib in additionalAttributeFields:
            attributes[attrib] = eventLocation.fromLocation.additionalAttrbutes[attrib]
        #updatesList += [{"attributes":{eventRouteID:eventLocation.fromLocation.routeID,eventFromMeasureField:eventLocation.fromLocation.measure,eventToMeasureField:eventLocation.toLocation.measure}}]
        updatesList += [{"attributes": attributes}]
    params = urllib.urlencode({'f': 'json', 'edits': [{'id':eventLayerID, 'adds': updatesList}]})
    #f = urllib.urlopen("{0}?{1}".format(applyEditsURL, params)) #Get Request
    f = urllib.urlopen(applyEditsURL, params) #Post Request
    #print "{0}?{1}".format(applyEditsURL, params)
    #print updatesList
    updateResult = json.loads(f.read())
    if 'success' in updateResult.keys() and updateResult['success']:
        print 'Sucessfully updated events'
        updateVals = {}

        for eventLocation in eventLocations:
            updateVals[eventLocation.fromLocation.oid] = eventLocation.eventID
            updateVals[eventLocation.toLocation.oid] = eventLocation.eventID

        workspace = getWorkspace(fc)
        # Start an edit session. Must provide the workspace.
        edit = arcpy.da.Editor(workspace)
        try:
            # Edit session is started without an undo/redo stack for versioned data
            #  (for second argument, use False for unversioned data)
            # Replace a layer/table view name with a path to a dataset (which can be a layer file) or create the layer/table view within the script
            edit.startEditing(False, False)
            # Start an edit operation
            edit.startOperation()
            #with arcpyEditor.UpdateCursor(fc, [oidField, eventIDField, processedDateField], expression) as rows:
            with arcpy.da.UpdateCursor(fc, [oidField, eventIDField, processedDateField], expression) as rows:
                for row in rows:
                    if row[0] in updateVals.keys():
                        row[1] = updateVals[row[0]]
                        row[2] = processedDate
                        rows.updateRow(row)
            edit.stopOperation()
        except:
            print "*****************ERROR*****************"
            print 'Unable to update points feature class'
            e = sys.exc_info()[1]
            print(e.args[0])

        finally:
            if edit.isEditing:
                edit.stopEditing(True)


    else:
        print 'fail'
        print updateResult

if __name__ == '__main__':
    main()
