"""
Binary sensor on Zigbee network through the deCONZ server.

For more details on this platform, please refer to the documentation
at https://home-assistant.io/components/light.dcz/
"""
import asyncio
import logging

from homeassistant.components import dcz
from homeassistant.components import binary_sensor

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ['dcz']


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the lights from deCONZ Zigbee network."""
    async_add_devices([BinarySensor(hass, config, *discovery_info)],
                      update_before_add=True)


class BinarySensor(dcz.Entity, binary_sensor.BinarySensorDevice):
    """deCONZ binary sensor entity."""

    _domain = binary_sensor.DOMAIN

    def __init__(self, hass, config, dclass, device_id, device, future=None):
        """Init deCONZ binary sensor."""
        super().__init__(hass, config, dclass, device_id, device)

        self._device_class = 'opening'

        if future:
            future.set_result(self)

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        return self._state.get('open')

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return self._device_class
