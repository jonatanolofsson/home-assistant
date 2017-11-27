"""
Map components.

For more details on this platform, please refer to the documentation
at https://home-assistant.io/components/light.dcz/
"""
from homeassistant import const as ha_const

DOMAIN = 'dcz'
CONF_DEVICE_CONFIG = 'device_config'


def component(config, dclass, device):
    """Determine device component type."""
    # Config has precedence
    if CONF_DEVICE_CONFIG in config[DOMAIN] and 'uniqueid' in device:
        node_config = config[DOMAIN][CONF_DEVICE_CONFIG].get(
            device['uniqueid'])
        if node_config and ha_const.CONF_TYPE in node_config:
            return node_config[ha_const.CONF_TYPE]

    if dclass == 'sensors':
        if 'modelid' in device:
            if device['modelid'] == 'lumi.sensor_magnet': return 'binary_sensor'

#        if 'modelid' in device:
#            if device['modelid'] == "TRADFRI remote control": return 'remote'

    elif dclass == 'lights':
        if 'type' in device:
            if device['type'] in ['Extended color light',
                                  'Color light',
                                  'Dimmable light',
                                  'Color temperature light']:
                return 'light'
        if 'modelid' in device:
            if device['modelid'].startswith("TRADFRI bulb"): return 'light'
