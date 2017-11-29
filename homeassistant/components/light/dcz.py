"""
Lights on Zigbee network through the deCONZ server.

For more details on this platform, please refer to the documentation
at https://home-assistant.io/components/light.dcz/
"""
import asyncio
import logging
import random

from homeassistant.components import dcz
from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_COLOR_TEMP, ATTR_EFFECT, ATTR_FLASH, ATTR_RGB_COLOR,
    ATTR_TRANSITION, ATTR_XY_COLOR, EFFECT_COLORLOOP, EFFECT_RANDOM,
    FLASH_LONG, FLASH_SHORT, SUPPORT_BRIGHTNESS, SUPPORT_COLOR_TEMP,
    SUPPORT_EFFECT, SUPPORT_FLASH, SUPPORT_RGB_COLOR, SUPPORT_TRANSITION,
    SUPPORT_XY_COLOR)
from homeassistant.components import light
from homeassistant.components.light import DOMAIN
import homeassistant.util.color as color_util
from homeassistant.const import STATE_ON, STATE_OFF

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ['dcz']
CONF_DEVICE_CONFIG = 'device_config'


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the lights from deCONZ Zigbee network."""
    async_add_devices([Light(hass, config, *discovery_info)],
                      update_before_add=True)


SUPPORT_ON_OFF = (SUPPORT_FLASH | SUPPORT_TRANSITION)
SUPPORT_DIMMABLE = (SUPPORT_ON_OFF | SUPPORT_BRIGHTNESS)
SUPPORT_COLOR_TEMP = (SUPPORT_DIMMABLE | SUPPORT_COLOR_TEMP)
SUPPORT_COLOR = (SUPPORT_DIMMABLE | SUPPORT_EFFECT |
                 SUPPORT_RGB_COLOR | SUPPORT_XY_COLOR)
SUPPORT_EXTENDED = (SUPPORT_COLOR_TEMP | SUPPORT_COLOR)

SUPPORTED_TYPES = {
    'Extended color light': SUPPORT_EXTENDED,
    'Color temperature light': SUPPORT_COLOR_TEMP,
    'Color light': SUPPORT_COLOR,
    'Dimmable light': SUPPORT_DIMMABLE,
    'On/Off plug-in unit': SUPPORT_ON_OFF,
    'Color temperature light': SUPPORT_COLOR_TEMP
}


class Light(dcz.Entity, light.Light):
    """deCONZ light entity."""

    _domain = DOMAIN

    def __init__(self, hass, config, dclass, device_id, device):
        """Init deCONZ light."""
        super().__init__(hass, config, dclass, device_id, device)

        dtype = None
        uid = device.get('uniqueid')
        dtype = config.get(DOMAIN, {}).get(CONF_DEVICE_CONFIG, {}) \
            .get(uid, {}).get('type')
        if dtype is None:
            dtype = device.get('type')

        if dtype in SUPPORTED_TYPES:
            self._supported_features = SUPPORTED_TYPES[dtype]
        else:
            self._supported_features |= SUPPORT_BRIGHTNESS
            self._supported_features |= SUPPORT_TRANSITION
            if device.get('hascolor'):
                self._supported_features |= SUPPORT_COLOR_TEMP

    @asyncio.coroutine
    def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        _LOGGER.info("Turning light on")
        command = {'on': True}

        if ATTR_TRANSITION in kwargs:
            command['transitiontime'] = int(kwargs[ATTR_TRANSITION] * 10)

        if ATTR_XY_COLOR in kwargs:
            if self._device.get('manufacturername') == "OSRAM":
                hue, sat = color_util.color_xy_to_hs(*kwargs[ATTR_XY_COLOR])
                command['hue'] = hue
                command['sat'] = sat
            else:
                command['xy'] = kwargs[ATTR_XY_COLOR]
        elif ATTR_RGB_COLOR in kwargs:
            if self._device.get('manufacturername') == "OSRAM":
                hsv = color_util.color_RGB_to_hsv(
                    *(int(val) for val in kwargs[ATTR_RGB_COLOR]))
                command['hue'] = hsv[0]
                command['sat'] = hsv[1]
                command['bri'] = hsv[2]
            else:
                xyb = color_util.color_RGB_to_xy(
                    *(int(val) for val in kwargs[ATTR_RGB_COLOR]))
                command['xy'] = xyb[0], xyb[1]
                command['bri'] = xyb[2]
        elif ATTR_COLOR_TEMP in kwargs:
            temp = kwargs[ATTR_COLOR_TEMP]
            command['ct'] = max(self.min_mireds, min(temp, self.max_mireds))

        if ATTR_BRIGHTNESS in kwargs:
            command['bri'] = kwargs[ATTR_BRIGHTNESS]

        flash = kwargs.get(ATTR_FLASH)

        if flash == FLASH_LONG:
            command['alert'] = 'lselect'
            del command['on']
        elif flash == FLASH_SHORT:
            command['alert'] = 'select'
            del command['on']

        effect = kwargs.get(ATTR_EFFECT)

        if effect == EFFECT_COLORLOOP:
            command['effect'] = 'colorloop'
        elif effect == EFFECT_RANDOM:
            command['hue'] = random.randrange(0, 65535)
            command['sat'] = random.randrange(150, 254)

        yield from self._async_put_state(command)

    @asyncio.coroutine
    def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        data = {'on': False}
        yield from self._async_put_state(data)

    @property
    def state(self):
        """Return light state."""
        return STATE_ON if self.is_on else STATE_OFF

    @property
    def is_on(self):
        """Return true if light is on."""
        return self._state.get('on')

    @property
    def brightness(self):
        """Return the brightness of this light between 0..255."""
        return self._state.get('bri')

    @property
    def xy_color(self):
        """Return the XY color value [float, float]."""
        return self._state.get('xy')

    @property
    def color_temp(self):
        """Return the CT color value in mireds."""
        return self._state.get('ct')

    @property
    def min_mireds(self):
        """Return the coldest color_temp that this light supports."""
        return self._device['ctmin']

    @property
    def max_mireds(self):
        """Return the warmest color_temp that this light supports."""
        return self._device['ctmax']
