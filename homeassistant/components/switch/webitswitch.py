import time
import threading
import socket
import binascii
import logging

import voluptuous as vol

# Import the device class from the component that you want to support
from homeassistant.components.switch import (SwitchDevice, PLATFORM_SCHEMA)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PIN, CONF_SWITCHES
import homeassistant.helpers.config_validation as cv


_LOGGER = logging.getLogger(__name__)

CONF_MOMENTARY = 'momentary'


_SWITCH_SCHEMA = vol.All(
    vol.Schema({
        vol.Required(CONF_PIN): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_MOMENTARY): cv.positive_int,
    }),
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_SWITCHES): vol.All(
            cv.ensure_list, [_SWITCH_SCHEMA]),
    }, extra=vol.ALLOW_EXTRA,
)


kInput = 0
kInputStateOn = True
kInputStateOff = False
kOutput = 1
kOutputStateOn = True
kOutputStateOff = False



def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Awesome Switch platform."""

    host = config.get(CONF_HOST)
    name = config.get(CONF_NAME)

    webit = WebitDevice(host, name)
    webitLoop = webitThread(webit)
    webitLoop.start()


    pins = config.get(CONF_SWITCHES)

    switches = []
    for i in pins:
        switches.append(WebitSwitch(webit, i))
    add_devices(switches)
    

class WebitSwitch(SwitchDevice):

    def __init__(self, device, data):
        self.device = device
        self.pin = data.get(CONF_PIN)
        self._name = data.get(CONF_NAME)
        self._momentary = data.get(CONF_MOMENTARY)
        self._state = None
        self.device.OutPutPins[data.get(CONF_PIN) - 1] = self
        
        
    @property
    def name(self):
        """Return the display name of this switch"""
        return self._name

    @property
    def should_poll(self):
        """No polling needed."""
        return False    

    @property
    def is_on(self, **kwargs):
        _LOGGER.debug('Webit: is_on() called')
        return self._state

    def turn_on(self, **kwargs):
        _LOGGER.debug('Webit: turn_on() called, momentary = %s' % self._momentary)
        if self._momentary == None:
            self.device.changeOutput('on', self.pin, self._momentary)
        else:
            self.device.changeOutput('pulse', self.pin, self._momentary)


    def turn_off(self, **kwargs):
        _LOGGER.debug('Webit: turn_off() called')
        self.device.changeOutput('off', self.pin, self._momentary)
        

class WebitDevice():
    def __init__(self, host, name):
        self.name = name
        self.ip = host
        self.state = None
        self.socket = None
        self.outputState = None
        self.inputState = None
        self.numOfInputs = 8
        self.numOfOutputs = 8
        self.OutPutPins = [None] * 8
        self.txCmdList = []
        self.data = None
        self.pingTimer = None
        self.holdTimer = None



    def changeOutput(self, action, pin, pulseLength):
        _LOGGER.debug('Webit: Entering actionControlWebit %s' % action)

        act = None
        arg = ''

        if action == 'on':
            act = 'on'
        elif action == 'off':
            act = 'off'
        elif action == 'toggle':
            act = 'toggle'
        elif action == 'pulse':
            act = 'pulse-'
            arg = str(pulseLength)
        else:
            return

        self.txCmdList.append('output' + str(pin) + '=' + act + arg)


class webitThread(threading.Thread):

    def __init__(self, device):
        threading.Thread.__init__(self)
        _LOGGER.debug('Webit: webitThread() init')

        self.States = self.enum(STARTUP=1, HOLD=2, INIT_SELECT=3, RUN=4)
        self.state = self.States.STARTUP

        self.device = device
        self.deviceStates = self.enum(CONNECTING=1, HOLD_INIT=2, HOLD_LOOP=3, POLL=4)
        self.device.state = self.deviceStates.CONNECTING

        self.shutdown = False


    def run(self):
        self.runWebitLoop()


    def enum(self, **enums):
        return type('Enum', (), enums)



    ######################################################################################
    # Communication Routines
    ######################################################################################

    def closePort(self):
        for k,device in self.deviceDict.iteritems():
            if (device.socket != None):
                device.socket.close()
                device.socket = None


    def openPort(self):
        _LOGGER.debug('Webit: Connecting to device %s at %s...' % (self.device.name, self.device.ip))
        host = self.device.ip
        port = 49218

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)

        try:
            s.connect((host, port))
        except:
            _LOGGER.debug('Webit: Error connecting to device %s.' % self.device.name)
            self.device.socket = None
            return False

        _LOGGER.debug('Webit: Connected to device %s.' % self.device.name)
        self.device.socket = s
        return True


    def readPort(self):
        """ returns 2 on error, 1 if no data, 0 with data """
        _LOGGER.debug('Webit: Entering readPort()')
        self.device.data = None
        try:
            self.device.data = self.device.socket.recv(64)
        except socket.timeout:
            pass
        except OSError as err:
            _LOGGER.debug('Webit: Error reading socket: %s' % err)
            return 2
        if(self.device.data):
            self.device.data = self.device.data.decode('ascii')
            _LOGGER.debug('Webit: RX: %s' % self.device.data)
            return 0
        return 1


    def writePort(self, data):
        """ Returns 2 on error, 1 on timeout or invalid command, 0 on success """
        _LOGGER.debug('Webit: Entering writePort() with TX: %s' % data)

        retries = 3
        txRetries = retries
        rxTimeout = 3
        okToExit = False
        while txRetries > 0:
            try:
                self.device.socket.sendall((data + '\r\n').encode('ascii'))
            except socket.error as err:
                _LOGGER.debug('Webit: Connection TX error: %s' % err)
                return 2

            ourTimeout = time.time() + rxTimeout
            txRetries -= 1
            while time.time() < ourTimeout:
                r = self.readPort()
                _LOGGER.debug('Webit: readPort return value = %u' % r)
                if r == 0:
                    if self.device.data.startswith('OK'):
                        okToExit = True
                    elif self.device.data.startswith('ERROR'):
                        _LOGGER.debug('Webit: Device responded with "ERROR" to command "%s"' % data)
                        return 1
                    self.parseResponse()
                elif r == 2:
                    return 2

                if okToExit:
                    return 0

                time.sleep(0.1)
            _LOGGER.debug('Webit: Timed out waiting for response to command "%s" for %u seconds, retrying.' % (data, rxTimeout))
        _LOGGER.debug('Webit: Resent command "%s" %u times with no success, aborting.' % (data, retries))
        return 1

    def parseResponse(self):
        _LOGGER.debug('Webit: Entering parseResonse')

        if not self.device.data:
            return

        lines = self.device.data.split('\n')

        for line in lines:
            if '=' in line:
                (key,val) = line.split('=')
                val = int(val)
                if key == 'inputs':
                    _LOGGER.debug('Webit: Inputs changed to %u' % val)
                    j = 1

                    if self.device.inputState != None:
                        changed = val ^ self.device.inputState
                    else:
                        changed = 0xFFFF

                    for i in range(1, self.device.numOfInputs + 1):
                        if j & changed > 0:
                            if val & j:
                                self.updateIoState(i, kInput, kInputStateOn)
                            else:
                                self.updateIoState(i, kInput, kInputStateOff)
                        j = j << 1

                    self.device.inputState = val

                elif key == 'outputs':
                    _LOGGER.debug('Webit: Outputs changed to %u' % val)
                    j = 1

                    if self.device.outputState != None:
                        changed = val ^ self.device.outputState
                    else:
                        changed = 0xFFFF

                    for i in range (1, self.device.numOfOutputs + 1):
                        if j & changed > 0:
                            if val & j:
                                self.updateIoState(i, kOutput, kOutputStateOn)
                            else:
                                self.updateIoState(i, kOutput, kOutputStateOff)
                        j = j << 1

                    self.device.outputState = val
        self.device.data = None


    def updateIoState(self, ioPin, ioType, newState):
        if (ioType == kOutput) and (self.device.OutPutPins[ioPin - 1] != None):
            self.device.OutPutPins[ioPin - 1]._state = newState
            self.device.OutPutPins[ioPin - 1].schedule_update_ha_state()
            _LOGGER.debug('Webit: updateIoState()')


    def runWebitLoop(self):
        while not self.shutdown:
            timeNow = time.time()

            if self.state == self.States.STARTUP:
                _LOGGER.debug('Webit: MAIN STATE: Startup')
                self.state = self.States.RUN

            elif self.state == self.States.HOLD:
                _LOGGER.debug('Webit: MAIN STATE: Hold')
                self.state = self.States.STARTUP

            elif self.state == self.States.RUN:
                _LOGGER.debug('Webit: MAIN STATE: Run')

                if self.device.state == self.deviceStates.CONNECTING:
                    _LOGGER.debug('Webit: DEVICE STATE: %s = CONNECTING' % self.device.name)
                    if self.openPort() is True:
                        self.device.txCmdList.append('getupdate')
                        self.device.pingTimer = timeNow + 60
                        self.device.state = self.deviceStates.POLL
                    else:
                        self.device.state = self.deviceStates.HOLD_INIT

                elif self.device.state == self.deviceStates.HOLD_INIT:
                    _LOGGER.debug('Webit: DEVICE STATE: %s = HOLD_INIT' % self.device.name)
                    _LOGGER.debug('Webit: Error detected with device "%s", will try to reconnect.' % self.device.name)
                    self.device.holdTimer = timeNow + (1 * 60)
                    self.device.state = self.deviceStates.HOLD_LOOP

                elif self.device.state == self.deviceStates.HOLD_LOOP:
                    _LOGGER.debug('Webit: DEVICE STATE: %s = HOLD_LOOP' % self.device.name)
                    if timeNow > self.device.holdTimer:
                        self.device.state = self.deviceStates.CONNECTING

                elif self.device.state == self.deviceStates.POLL:
                    _LOGGER.debug('Webit: DEVICE STATE: %s = POLL' % self.device.name)

                    if timeNow > self.device.pingTimer:
                        self.device.pingTimer = timeNow + 60
                        self.device.txCmdList.append('ping')

                    if len(self.device.txCmdList) > 0:
                        data = self.device.txCmdList[0]

                        r = self.writePort(data)
                        _LOGGER.debug('Webit: r = %u' % r)
                        if r == 0:
                            del self.device.txCmdList[0]
                        elif r == 1:
                            _LOGGER.debug('Webit: Error sending command "%s".' % data)
                            del self.device.txCmdList[0]
                        elif r == 2:
                            self.device.state = self.deviceStates.CONNECTING

                    r = self.readPort()
                    if r == 0:
                        self.parseResponse()
                    elif r == 2:
                        self.device.state = self.deviceStates.CONNECTING

                time.sleep(0.1)

        self.closePort()
        _LOGGER.debug('Webit: Exiting run loop')
