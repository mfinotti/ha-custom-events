from enum import Enum
from json import dump, dumps, loads
import logging

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ha-custom-events"

SYSTEMCODE_ENTITY = "input_text.system_code"


async def async_setup(hass, config):

    conf = config[DOMAIN]

    if len(conf["events"]) > 0:
        _LOGGER.info("HA-Custom-events events configuration found.")
        _LOGGER.debug("events configuration %s", conf["events"])
    else:
        _LOGGER.warn("HA-Custom-events any 'EVENTS' has provided in the configuration.")

    if len(conf["targets"]) > 0:
        _LOGGER.info("HA-Custom-events target configuration found.")
        _LOGGER.debug("targets configuration %s", conf["targets"])
    else:
        _LOGGER.warn("HA-Custom-events any 'TARGETS' has provided in the configuration.")

    ceh = CustomEventHandler(hass, conf["events"], conf['targets'])

    return True

class CustomEventHandler:

    def __init__(
        self,
        hass,
        events,
        targets):
        """Initialize."""
        self._hass      = hass
        self.event      = {}     # events address book
        self.target     = {}    # targets address book

        eventConfiguration  = loads(dumps(events))
        targetConfiguration = loads(dumps(targets))

        # setting up event own component
        self._hass.bus.async_listen("HA_EVENT", self.executeHAEvent)
        _LOGGER.debug("listing on system event: %s \n Fire HA_EVENT everytime you want invoke the ha-custom-events integration","HA_EVENT")

        for econf in eventConfiguration:
            self.buildEventByConf(econf)

        for tconf in targetConfiguration:
            self.target[tconf['target']] = tconf


    def buildEventByConf(self, econf):

        e       = econf['event']
        etype   = econf['type']

        if "listener" == etype:
            self._hass.bus.async_listen(e, self.executeCallbackOnEvent)
            _LOGGER.debug("listing on system event: %s",e)

        self.event[e] = econf


    def executeHAEvent(self, event):
        if "event" not in event.data and "target" not in event.data:
            _LOGGER.warn("HA-Event not recognized. Please use the standard form: \n [event: Event to fire / target: if the event references one of the 'targets' specified in configuration, data: (Optional) Event data passed. type: (Optional) the type of the target event]")
            return

        eventToFire = event.data['event'] if 'event' in event.data else None
        eventData   = event.data['data'] if 'data' in event.data else None
        eventTarget = event.data['target'] if 'target' in event.data else None
        eventMessage= event.data['message'] if 'message' in event.data else ""
        eventTargetThreshold   = event.data['threshold'] if 'threshold' in event.data else None


        if None != eventTarget and "" != eventTarget:
            targetData = {}

            targetConf  = self.target[eventTarget]
            targetData['entity'] = self.getTargetCurrentState(targetConf['target'])

            for threshold in filter(lambda x : x['event'] == eventTargetThreshold, targetConf['events']):
                _LOGGER.debug("Target threshold found. target: %s, threshold: %s", eventTarget, threshold)

                eventLevel      = threshold['event']
                eventMessage    += " - "+threshold['message'] if "message" in threshold else ""

                # getting threeshold callback
                callbacks = self.getEventCallback(threshold)

                self.handleEventCallback(targetData, callbacks, eventLevel, eventMessage)
        else:
            _LOGGER.debug("firing Event %s with data %s", event.event_type, eventData)
            self._hass.bus.fire(eventToFire,eventData)


    def executeCallbackOnEvent(self, event):

        eventName   = event.event_type
        eventData   = event.data

        _LOGGER.info("Catched event %s with data: %s", eventName, eventData)

        currentEvent = self.event[eventName]
        if not currentEvent :
            raise Exception("Event [%s] not found. ", eventName)

        targetData = {}
        message     = eventData['message'] if 'message' in eventData else None
        sender      = eventData['sender'] if 'sender' in eventData else None
        eventDataAtrributes = None


        if "platform" in eventData :
            targetData['entity'] = self._getEventDataByPlatform(eventData['platform'], eventData)
        else:
            targetData['entity'] = self._getEventDataByPlatform(currentEvent['platform'], eventData)

        if sender :
            targetData['sender'] = sender

        # getting threeshold callback
        callbacks = self.getEventCallback(currentEvent)

        self.handleEventCallback(targetData, callbacks, "NOTICE" , message)


    def _getEventDataByPlatform(self, platform, eventData, eventDataAttributes = None, eventLevel = "NOTICE", eventMessage = ""):

        switcher = {
            'HASSIO_EVENT'  : '_processEventDataEVENT',
            'HASSIO_STATE'  : '_processEventDataGENERIC',
            'HAKAFKA'       : '_processEventDataHAKAFKA'
        }

        try:
            methodName = switcher.get(platform)
            _LOGGER.debug("found process method %s for plaform %s", methodName, platform)
            method = getattr(self, methodName)

            return method(eventData, eventDataAttributes, eventLevel, eventMessage)

        except Exception as e:
            _LOGGER.debug(e)
            _LOGGER.warn("No function found for the plarform: %s! falling back to GENERIC", platform)
            return self._processEventDataGENERIC(eventData, eventDataAttributes)


    def _processEventDataHAKAFKA(self, eventData = {}, eventDataAttributes = None, eventLevel = "NOTICE", eventMessage = ""):

        try:

            message                         = {}
            message['message']              = {}
            message['message']["eventType"] = eventLevel
            message['message']["message"]   = eventMessage
            message['message']["systemCode"]= self.systemCode
            message['message']["platform"]  = "HASSIO_EVENT"

            if eventData:
                if 'sender' in eventData:
                    message['message']['sender'] = eventData['sender']
                    del eventData["sender"]

                if 'topic' in eventData:
                    message["topic"] = eventData['topic']
                    del eventData['topic']

                if isinstance(eventData['entity'], list) :
                    message['message']['entity'] = eventData['entity']
                else:
                    message['message']['entity'] = list(eventData['entity'])

            if eventDataAttributes:
                message['message']['command'] = eventDataAttributes

            return message

        except Exception as e:
            _LOGGER.warn("eventData: %s", eventData)
            _LOGGER.error(e)


    def _processEventDataGENERIC(self, eventData, eventDataAttributes = None, eventLevel = "NOTICE", eventMessage = ""):

        if 'entity' in eventData:
            if 'targetId' in eventData['entity']:
                eventData = self.getTargetCurrentState(eventData['entity']['targetId'])

        return eventData


    def _processEventDataEVENT(self, eventData, eventDataAttributes = None, eventLevel = "NOTICE", eventMessage = ""):

        if 'entity' in eventData:
            if 'targetId' in eventData['entity']:
                _LOGGER.info("Firing HASSIO event [%s]", eventData['entity']['targetId'])
                self._hass.bus.fire(eventData['entity']['targetId'], eventData)

        return {}


    def handleEventCallback(self, targetData, callbacks,  eventLevel = "NOTICE", eventMessage = ""):

        if not hasattr(self, "systemCode"):
            self.systemCode = self._hass.states.get(SYSTEMCODE_ENTITY).state
            _LOGGER.info("Setting up the System code: %s", self.systemCode)

        try:
            _LOGGER.debug("Found %s callbacks for the given event", len(callbacks))
            for callback in callbacks:
                cevent      = callback['event']

                ctype           = None
                cplatform       = None
                cdata           = None
                cDataAttributes = None

                # finding the given event in the address book..
                if cevent in self.event:
                    # event found in the address book then retrieve the event type
                    ctype       = self.event[cevent]['type']
                    cplatform   = self.event[cevent]['platform']
                else:
                    _LOGGER.warn("Event %s not found in the address book. Falling back to into type [dispatcher]", cevent)
                    ctype = "dispatcher"

                if "data" in callback:
                    cDataAttributes = callback['data'] # get optional callback event data

                if "topic" in callback:
                    _LOGGER.debug("found topic in callback properties..Injecting in main event data..")
                    _LOGGER.debug("target %s", targetData)
                    _LOGGER.debug("callback %s", callback)
                    targetData['topic'] = callback['topic']

                ###### HANDLE EVENT BY HIS OWN TYPE
                cdata = self._getEventDataByPlatform(cplatform, targetData, cDataAttributes, eventLevel, eventMessage)

                if "dispatcher" == ctype:
                    _LOGGER.debug("Firing callback event %s with data %s", cevent, cdata)
                    self._hass.bus.fire(
                        cevent,
                        cdata,
                    )
                else:
                    _LOGGER.debug("listening on event: %s",cevent)
                    self._hass.bus.async_listen(cevent, self.executeCallbackOnEvent)

        except Exception as e:
            _LOGGER.error(e)


    def getEventCallback(self, event):

        if not event:
            raise Exception("The given event not exists [%s]", event['event'])

        if "callback" in event and len(event["callback"]) > 0:
            return event['callback']
        else:
            return []


    def getTargetCurrentState(self, target):

        data = []
        if "group." in target:
            groupMetadata = self._hass.states.get(target)
            if groupMetadata:
                for entity in groupMetadata.attributes['entity_id']:
                    data.append(self._hassioEntityState(entity))
        else:
           data.append(self._hassioEntityState(target))


        _LOGGER.debug("target [%s] entity state: %s ", target, data)

        return data

    def _hassioEntityState(self, entityId):

        targetState             = self._hass.states.get(entityId)
        targetDict              = {}
        targetDict['state']     = targetState.state
        targetDict['entityId']  = targetState.entity_id
        targetDict['domain']    = targetState.domain
        targetDict['label']     = targetState.name

        return targetDict