"""
Support for Zigbee devices through the deCONZ websocket and REST interface

For more details on this platform, please refer to the documentation
at https://home-assistant.io/components/light.dcz/
"""
import asyncio
import logging
import json

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant import const as ha_const
from homeassistant.const import (CONF_HOST, CONF_PORT, CONF_API_KEY)
from homeassistant.helpers import discovery, entity
from .components import component

REQUIREMENTS = ['websockets>=4', 'aiohttp>=2.3', 'async_timeout>=2']

DOMAIN = 'dcz'

CONF_DEVICE_CONFIG = 'device_config'
DATA_API = 'dcz_api'
ATTR_DURATION = 'duration'
ATTR_ON = 'on'

DEVICE_CONFIG_SCHEMA_ENTRY = vol.Schema({
    vol.Optional(ha_const.CONF_TYPE): cv.string,
})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=80): cv.positive_int,
        vol.Required(CONF_API_KEY): cv.string,
        vol.Optional(CONF_DEVICE_CONFIG, default={}):
            vol.Schema({cv.string: DEVICE_CONFIG_SCHEMA_ENTRY}),
    }),
}, extra=vol.ALLOW_EXTRA)

SERVICE_PERMIT = 'permit'
SERVICE_OTAU = 'otau'
SERVICE_DESCRIPTIONS = {
    SERVICE_PERMIT: {
        "description": "Allow nodes to join the ZigBee network",
        "fields": {
            ATTR_DURATION: {
                "description": "Time to permit joins, in seconds",
                "example": "60",
            },
        },
    },
    SERVICE_OTAU: {
        "description": "Enable OTA updates."
    },
}
SERVICE_SCHEMAS = {
    SERVICE_PERMIT: vol.Schema({
        vol.Optional(ATTR_DURATION, default=60):
            vol.All(vol.Coerce(int), vol.Range(1, 254)),
    }),
    SERVICE_OTAU: vol.Schema({
        vol.Optional(ATTR_ON, default=True): cv.boolean
    }),
}

_LOGGER = logging.getLogger(__name__)


@asyncio.coroutine
def async_setup(hass, config):
    """Setup platform."""
    api = DeCONZApi(hass, config)
    hass.data[DATA_API] = api

    asyncio.ensure_future(api.refresher_task(hass, config))

    @asyncio.coroutine
    def permit(service):
        """Allow devices to join this network."""
        duration = service.data.get(ATTR_DURATION)
        _LOGGER.info("Permitting joins for %ss", duration)
        yield from api.permit(duration)

    @asyncio.coroutine
    def otau(service):
        """Enable OTA updates."""
        enable = service.data.get(ATTR_ON)
        _LOGGER.debug("%sabling OTA updates.", 'En' if enable else 'Dis')
        yield from api.otau(enable)

    hass.services.async_register(DOMAIN, SERVICE_PERMIT, permit,
                                 SERVICE_DESCRIPTIONS[SERVICE_PERMIT],
                                 SERVICE_SCHEMAS[SERVICE_PERMIT])

    hass.services.async_register(DOMAIN, SERVICE_OTAU, otau,
                                 SERVICE_DESCRIPTIONS[SERVICE_OTAU],
                                 SERVICE_SCHEMAS[SERVICE_OTAU])

    hass.bus.async_listen_once(
        ha_const.EVENT_HOMEASSISTANT_STOP, api.kill())

    return True


class DeCONZApi:
    """deCONZ interface."""

    def __init__(self, hass, config):
        """Init."""
        import async_timeout
        import aiohttp
        self._aiohttp = aiohttp
        self._async_timeout = async_timeout

        self._hass = hass
        self._config = config
        self._devices = {}
        self._ws_task = asyncio.ensure_future(self._ws_listener())
        self._dying = False
        self._http = aiohttp.ClientSession()
        self._futures = {}

    @asyncio.coroutine
    def permit(self, duration):
        """Permit joins for <duration> seconds."""
        yield from self.put('config', {'permitjoin': duration})

    @asyncio.coroutine
    def otau(self, enable=True):
        """Enable OTA updates."""
        yield from self.put('config', {'otauactive': enable})

    @asyncio.coroutine
    def kill(self):
        """Kill API."""
        self._dying = True
        self._ws_task.cancel()

    def register_device(self, dclass, obj):
        """Register device entity."""
        assert dclass
        assert obj

        if dclass not in self._devices:
            self._devices[dclass] = {}
        if self._devices[dclass].get(obj.id) is not None:
            _LOGGER.warning("Duplicate init")
            return
        self._devices[dclass][obj.id] = obj
        _LOGGER.debug("Stored device: [%s][%s]", dclass, obj.id)
        if obj.uid in self._futures:
            self._futures[obj.uid].set_result(obj)
            del self._futures[obj.uid]

    @asyncio.coroutine
    def unregister_device(self, dclass, obj):
        """Unregister device."""
        assert dclass
        assert obj

        del self._devices[dclass][obj.id]

    @asyncio.coroutine
    def _find_unknown_device(self, dclass, device_id):
        """Find unknown device."""
        _LOGGER.debug("Finding unknown device: [%s][%s]", dclass, device_id)
        if not dclass or not device_id:
            return None
        if dclass not in self._devices:
            self._devices[dclass] = {}
        self._devices[dclass][device_id] = None
        device = yield from self.get('{}/{}'.format(dclass, device_id))
        comp = component(self._config, dclass, device)
        result = False
        if comp:
            uid = device['uniqueid']
            if uid not in self._futures:
                future = self._hass.loop.create_future()
                self._futures[uid] = future
                self._hass.async_add_job(discovery.async_load_platform(
                    self._hass, comp, DOMAIN,
                    ('sensors', device_id, device), self._config))
            try:
                result = yield from asyncio.wait_for(self._futures[uid], 10)
            except asyncio.TimeoutError:
                result = False
        return result

    @asyncio.coroutine
    def _ws_handle_message(self, message):
        """Handle message."""
        try:
            if 'r' not in message:
                _LOGGER.warning("Message without dclass: %s",
                                json.dumps(message))
                return
            dclass = message['r']
            if 'id' not in message:
                _LOGGER.warning("Message without destination: %s",
                                json.dumps(message))
                return
            device_id = message['id']
            _LOGGER.debug("Finding device: [%s][%s]", dclass, device_id)
            if dclass in self._devices and device_id in self._devices[dclass]:
                device = self._devices[dclass][device_id]
            else:
                device = yield from self._find_unknown_device(dclass,
                                                              device_id)
            if not device:
                _LOGGER.info("Unable to determine device: (%s : %s).", dclass, device_id)
                return

            yield from device.handle_message(message)
        except Exception as e:
            _LOGGER.error("Handle message exception: %s", repr(e))

    @asyncio.coroutine
    def _ws_listener(self):
        """Read and parse websocket."""
        import websockets

        conf = yield from self.get('config')
        if conf:
            ws_port = conf.get('websocketport', 443)
        else:
            ws_port = 443

        ws_url = 'ws://{}:{}'.format(self._config[DOMAIN][CONF_HOST], ws_port)

        _LOGGER.info("Connecting to websocket on: %s", ws_url)

        while not self._dying:
            try:
                socket = yield from websockets.connect(ws_url)
                while True:
                    raw = None
                    try:
                        raw = yield from asyncio.wait_for(
                            socket.recv(), timeout=20)
                    except asyncio.TimeoutError:
                        try:
                            pong = yield from socket.ping()
                            yield from asyncio.wait_for(pong, timeout=10)
                            continue
                        except asyncio.TimeoutError:
                            break
                    if not raw:
                        _LOGGER.warning("Invalid data received.")
                        continue
                    data = json.loads(raw)
                    _LOGGER.debug("Got websocket message: %s", raw)
                    if not data:
                        _LOGGER.warning("Invalid json data received.")
                        continue

                    asyncio.ensure_future(self._ws_handle_message(data))

            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.warning("Websocket exception: %s",
                                getattr(e, 'message', repr(e)))
                _LOGGER.warning("Websocket exception."
                                "Sleeping 5s and retrying.")
                asyncio.sleep(5)
            finally:

                yield from socket.close()

    def _http_address(self, node):
        """Return a http address to given REST node."""
        return "http://{}:{}/api/{}/{}".format(
            self._config[DOMAIN][CONF_HOST],
            self._config[DOMAIN][CONF_PORT],
            self._config[DOMAIN][CONF_API_KEY],
            node)

    @asyncio.coroutine
    def _req(self, action, node, data=None):
        try:
            response = None
            raw = False
            url = self._http_address(node)
            with self._async_timeout.timeout(10, loop=self._hass.loop):
                response = yield from action(url, json=data)
                if response.status != 200:
                    _LOGGER.error("deCONZ returned http status "
                                  "%d, response %s",
                                  response.status,
                                  (yield from response.text()))
                    return False
            raw = yield from response.json()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout getting deCONZ data from %s.", url)
            return False
        except self._aiohttp.ClientError:
            _LOGGER.exception("Error getting deCONZ data from %s.", url)
            return False
        finally:
            if response:
                yield from response.release()
        return raw

    @asyncio.coroutine
    def get(self, node):
        """HTTP GET request."""
        return (yield from self._req(self._http.get, node))

    @asyncio.coroutine
    def put(self, node, data):
        """HTTP PUT request."""
        return (yield from self._req(self._http.put, node, data))

    @asyncio.coroutine
    def post(self, node, data):
        """HTTP POST request."""
        return (yield from self._req(self._http.post, node, data))

    @asyncio.coroutine
    def refresher_task(self, hass, config):
        """Refresh connected devices."""
        while True:
            for dclass in ['sensors', 'lights']:
                if dclass not in self._devices:
                    self._devices[dclass] = {}
                devices = yield from self.get(dclass)
                for device_id, device in devices.items():
                    if device_id not in self._devices[dclass]:
                        self._devices[dclass][device_id] = None
                        comp = component(config, dclass, device)
                        if comp:
                            hass.async_add_job(discovery.async_load_platform(
                                hass, comp, DOMAIN,
                                (dclass, device_id, device), config))
            asyncio.sleep(5)


class Entity(entity.Entity):
    """deCONZ entity class."""

    _domain = None

    def __init__(self, hass, config, dclass, device_id, device):
        """Init."""
        self.dclass = dclass
        self.id = device_id
        self.uid = device.get('uniqueid')
        self.entity_id = "{}.{}_{}".format(self._domain, dclass, self.id)
        self._device = device
        self._api = hass.data[DATA_API]
        self._state = device['state'] if 'state' in device else {}
        self._supported_features = 0
        self._device_state_attributes = {}
        self._api.register_device(dclass, self)

    @property
    def name(self):
        """Return name."""
        return self._device['name']

    @asyncio.coroutine
    def _async_put_state(self, data, endpoint='/state'):
        """Set properties."""
        _LOGGER.debug("Setting state: %s", json.dumps(data))
        res = yield from self._api.put('{}/{}{}'.format(
            self.dclass, self.id, endpoint), data)
        if res:
            self.schedule_update_ha_state()

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.

        False if entity pushes its state to HA.
        """
        return False

    @property
    def supported_features(self):
        """Flag supported features."""
        return self._supported_features

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        return self._device_state_attributes

    @asyncio.coroutine
    def handle_message(self, message):
        """Default message handler."""
        _LOGGER.debug("Default message handler.")
        if 'config' in message:
            if 'battery' in message['config']:
                self._state['battery_level'] = message['config']['battery']
        elif 'e' in message:
            if message['e'] == 'changed':
                _LOGGER.debug("Updating state.")
                self._state.update(message['state'])
        self.schedule_update_ha_state()
