# ha-custom-events
Flexible custom events for HASSIO

Configuration example
ha-custom-events:
  # triggereable entities are in this list
  entities:
    - input_boolean.pump_anomaly:
        # event triggered
        - NOTICE:
            # the actions list (may be empty)
            actions:
              - platform: EVENT
                label: 'Spegni irrigazione'
                value: 'spegniIrrigazioneEvent'

