from enum import Enum
from json import dump, dumps, loads
import logging

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ha-custom-events"

SYSTEMCODE_ENTITY = "input_text.system_code"

SCRIPTS_TARGET = "scripts.ececution"


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
        _LOGGER.warn(
            "HA-Custom-events any 'TARGETS' has provided in the configuration."
        )

    ceh = CustomEventHandler(hass, conf["events"], conf["targets"])

    return True


class CustomEventHandler:
    def __init__(self, hass, events, targets):
        """Initialize."""
        self._hass = hass
        self.event = {}  # events address book
        self.target = {}  # targets address book

        eventConfiguration = loads(dumps(events))
        targetConfiguration = loads(dumps(targets))

        # setting up event own component
        self._hass.bus.async_listen("HA_EVENT", self.executeHAEvent)
        _LOGGER.debug(
            "listing on system event: %s \n Fire HA_EVENT everytime you want invoke the ha-custom-events integration",
            "HA_EVENT",
        )

        self._hass.bus.async_listen(
            "HA_SCRIPT_EVENT", self.executeHAEventOnScritptReturn
        )
        _LOGGER.debug(
            "listing on system event: %s \n Fire HA_SCRIPT_EVENT everytime you want invoke the ha-custom-events integration",
            "HA_SCRIPT_EVENT",
        )

        for econf in eventConfiguration:
            self.buildEventByConf(econf)

        for tconf in targetConfiguration:
            self.target[tconf["target"]] = tconf

    def buildEventByConf(self, econf):

        e = econf["event"]
        etype = econf["type"]

        if "listener" == etype:
            self._hass.bus.async_listen(e, self.onEvent)
            _LOGGER.debug("listing on system event: %s", e)

        self.event[e] = econf

    def executeHAEventOnScritptReturn(self, event):

        _LOGGER.debug("Catched event %s with data %s", "HA_SCRIPT_EVENT", event.data)
        if "id" not in event.data["payload"] and "data" not in event.data["payload"]:
            _LOGGER.warn(
                "HA-Script-Event not recognized. Please use the standard form: \n [id: if the event references one of the 'targets' specified in configuration, data: (Optional) Event data passed."
            )
            return

        payload = event.data["payload"]
        eventData = payload["data"]
        eventId = payload["id"]
        # injecting eventData into message data
        messageData = {}
        messageData["target"] = eventData

        scriptExecutionConf = self.target[SCRIPTS_TARGET]

        if None != scriptExecutionConf and "" != scriptExecutionConf:
            for event in filter(
                lambda x: x["event"] == eventId, scriptExecutionConf["events"]
            ):
                _LOGGER.debug(
                    "Target event found. target: %s, event: %s", SCRIPTS_TARGET, eventId
                )

                # getting threeshold callback
                callbacks = self.getEventCallback(event)

                self.handleEventCallback(messageData, callbacks)
        else:
            _LOGGER.warn(
                "SCRIPT TARGET not configured! Please add [%s] under 'target' configuration collection",
                SCRIPTS_TARGET,
            )

    def executeHAEvent(self, event):
        if "event" not in event.data and "target" not in event.data:
            _LOGGER.warn(
                "HA-Event not recognized. Please use the standard form: \n [event: Event to fire / target: if the event references one of the 'targets' specified in configuration, data: (Optional) Event data passed. type: (Optional) the type of the target event]"
            )
            return

        eventToFire = event.data["event"] if "event" in event.data else None
        eventData = event.data["data"] if "data" in event.data else None
        eventTarget = event.data["target"] if "target" in event.data else None
        eventMessage = event.data["message"] if "message" in event.data else ""
        eventTargetThreshold = (
            event.data["threshold"] if "threshold" in event.data else None
        )

        if None != eventTarget and "" != eventTarget:
            targetData = {}

            targetConf = self.target[eventTarget]
            targetData["target"] = self.getTargetCurrentState(targetConf["target"])

            for threshold in filter(
                lambda x: x["event"] == eventTargetThreshold, targetConf["events"]
            ):
                _LOGGER.debug(
                    "Target threshold found. target: %s, threshold: %s",
                    eventTarget,
                    threshold,
                )

                eventLevel = (
                    threshold["level"] if "level" in threshold else threshold["event"]
                )
                eventMessage += (
                    " - " + threshold["message"] if "message" in threshold else ""
                )

                # getting threeshold callback
                callbacks = self.getEventCallback(threshold)

                self.handleEventCallback(
                    targetData, callbacks, eventLevel, eventMessage
                )
        else:
            _LOGGER.debug("firing Event %s with data %s", eventToFire, eventData)
            self._hass.bus.fire(eventToFire, eventData)

    def onEvent(self, event):

        eventName = event.event_type
        eventData = event.data

        _LOGGER.info("Catched event %s with data: %s", eventName, eventData)

        currentEvent = self.event[eventName]
        if not currentEvent:
            raise Exception("Event [%s] not found. ", eventName)

        targetData = {}
        message = eventData["message"] if "message" in eventData else None
        sender = eventData["sender"] if "sender" in eventData else None

        if "platform" in eventData:
            targetData["target"] = self._getEventDataByPlatform(
                eventData["platform"], eventData
            )
        else:
            targetData["target"] = self._getEventDataByPlatform(
                currentEvent["platform"], eventData
            )

        if sender:
            targetData["sender"] = sender

        # getting threeshold callback
        callbacks = self.getEventCallback(currentEvent)

        self.handleEventCallback(targetData, callbacks, "NOTICE", message)

    def _getEventDataByPlatform(
        self,
        platform,
        eventData,
        eventDataAttributes=None,
        eventLevel="NOTICE",
        eventMessage="",
    ):

        switcher = {
            "HASSIO_EVENT": "_processEventDataEVENT",
            "HASSIO_STATE": "_processEventDataGENERIC",
            "HAKAFKA": "_processEventDataHAKAFKA",
        }

        try:
            methodName = switcher.get(platform)
            _LOGGER.debug(
                "found process method %s for plaform %s", methodName, platform
            )
            method = getattr(self, methodName)

            return method(eventData, eventDataAttributes, eventLevel, eventMessage)

        except Exception as e:
            _LOGGER.debug(e)
            _LOGGER.warn(
                "No function found for the plarform: %s! falling back to GENERIC",
                platform,
            )
            return self._processEventDataGENERIC(eventData, eventDataAttributes)

    def _processEventDataHAKAFKA(
        self,
        eventData={},
        eventDataAttributes=None,
        eventLevel="NOTICE",
        eventMessage="",
    ):

        try:
            message = {}
            message["message"] = {}
            message["message"]["eventType"] = eventLevel
            message["message"]["message"] = eventMessage
            message["message"]["systemCode"] = self.systemCode
            message["message"]["platform"] = "HASSIO_EVENT"

            if eventData:
                if "sender" in eventData:
                    message["message"]["sender"] = eventData["sender"]
                    del eventData["sender"]

                if "topic" in eventData:
                    message["topic"] = eventData["topic"]
                    del eventData["topic"]

                if isinstance(eventData["target"], list):
                    message["message"]["target"] = eventData["target"]
                else:
                    message["message"]["target"] = list(eventData["target"])

            if eventDataAttributes:
                message["message"]["command"] = eventDataAttributes

            return message

        except Exception as e:
            _LOGGER.warn("eventData: %s", eventData)
            _LOGGER.error(e)

    def _processEventDataGENERIC(
        self, eventData, eventDataAttributes=None, eventLevel="NOTICE", eventMessage=""
    ):

        data = []
        if "target" in eventData:
            if "targetId" in eventData["target"]:
                if isinstance(
                    eventData["target"]["targetId"], list
                ):  # only for internal purpose
                    for target in eventData["target"]["targetId"]:
                        data.extend(self.getTargetCurrentState(target, True))
                else:
                    data.extend(
                        self.getTargetCurrentState(eventData["target"]["targetId"])
                    )

        return data

    def _processEventDataEVENT(
        self, eventData, eventDataAttributes=None, eventLevel="NOTICE", eventMessage=""
    ):

        if "target" in eventData:
            if "targetId" in eventData["target"]:
                _LOGGER.info(
                    "Firing HASSIO event [%s]", eventData["target"]["targetId"]
                )
                self._hass.bus.fire(eventData["target"]["targetId"], eventData)

                dataToReturn = []
                dataToReturn.append({"targetId": eventData["target"]["targetId"]})

                return dataToReturn

        return {}

    def handleEventCallback(
        self, targetData, callbacks, eventLevel="NOTICE", eventMessage=""
    ):

        if not hasattr(self, "systemCode"):
            self.systemCode = self._hass.states.get(SYSTEMCODE_ENTITY).state
            _LOGGER.info("Setting up the System code: %s", self.systemCode)

        try:
            _LOGGER.debug("Found %s callbacks for the given event", len(callbacks))
            for callback in callbacks:
                cevent = callback["event"]

                ctype = None
                cplatform = None
                cdata = None
                cDataAttributes = None

                # finding the given event in the address book..
                if cevent in self.event:
                    # event found in the address book then retrieve the event type
                    ctype = self.event[cevent]["type"]
                    cplatform = self.event[cevent]["platform"]
                else:
                    _LOGGER.warn(
                        "Event %s not found in the address book. Falling back to into type [dispatcher]",
                        cevent,
                    )
                    ctype = "dispatcher"

                if "data" in callback:
                    cDataAttributes = self.getAdditionalAttributesFromData(
                        callback["data"]
                    )  # get optional callback event data

                if "topic" in callback:
                    _LOGGER.debug(
                        "found topic in callback properties..Injecting in main event data.."
                    )
                    targetData["topic"] = callback["topic"]

                ###### HANDLE EVENT BY HIS OWN TYPE
                cdata = self._getEventDataByPlatform(
                    cplatform, targetData, cDataAttributes, eventLevel, eventMessage
                )

                if "dispatcher" == ctype:
                    _LOGGER.debug(
                        "Firing callback event %s with data %s", cevent, cdata
                    )
                    self._hass.bus.fire(
                        cevent,
                        cdata,
                    )
                else:
                    _LOGGER.debug("listening on event: %s", cevent)
                    self._hass.bus.async_listen(cevent, self.executeCallbackOnEvent)

        except Exception as e:
            _LOGGER.error(e)

    def getAdditionalAttributesFromData(self, data):
        attributes = []

        _LOGGER.debug("getAdditionalAttributesFromData data: [%s]", data)
        if isinstance(data, list):
            for eventData in data:
                # check if the event exists in the configuration
                eventToAdd = self._getEventConfigurationByData(eventData)

                attributes.append(eventToAdd)
        elif "event" in data:
            eventToAdd = self._getEventConfigurationByData(data)

            attributes.append(eventToAdd)
        else:
            attributes.append(data)

        _LOGGER.debug("getAdditionalAttributesFromData attribues: [%s]", attributes)

        return attributes

    def _getEventConfigurationByData(self, data):

        eventConfiguration = data
        try:
            eventConfiguration = self.event[data["event"]]
        except Exception as e:
            _LOGGER.warn("Event not found in configuration [%s]", data)

        return eventConfiguration

    def getEventCallback(self, event):

        if not event:
            raise Exception("The given event not exists [%s]", event["event"])

        if "callback" in event and len(event["callback"]) > 0:
            return event["callback"]
        else:
            return []

    def getTargetCurrentState(self, target, includeGroups=False):

        data = []
        if "group." in target:
            groupMetadata = self._hass.states.get(target)
            if groupMetadata:

                if includeGroups:
                    data.append(self._hassioGroupMetadata(groupMetadata))

                for entity in groupMetadata.attributes["entity_id"]:
                    currentEntityState = self._hassioEntityState(entity)
                    if None != currentEntityState: 
                        data.append(currentEntityState)
        else:
            currentEntityState = self._hassioEntityState(target)
            if None != currentEntityState: 
                data.append(currentEntityState)

        _LOGGER.debug("target [%s] entity state: %s ", target, data)

        return data

    def _hassioEntityState(self, entityId):

        targetState = self._hass.states.get(entityId)
        _LOGGER.debug("entity: [%s] state: [%s]", entityId, targetState)

        if None == targetState:
            return None

        targetDict = {}
        targetDict["targetId"] = targetState.entity_id
        targetDict["domain"] = targetState.domain
        targetDict["label"] = targetState.attributes["friendly_name"]

        if "unit_of_measurement" in targetState.attributes:
            targetDict["state"] = targetState.state+" "+ targetState.attributes["unit_of_measurement"]
        else:
            targetDict["state"] = targetState.state

        return targetDict

    def _hassioGroupMetadata(self, group):

        metadata = {}
        metadata["targetId"] = group.entity_id
        metadata["domain"] = "group"
        metadata["label"] = group.attributes["friendly_name"]

        return metadata