#!/usr/bin/env python

import os
import sys
import time
import serial
import threading
import logging
import json
import subprocess
import binascii

from optparse import OptionParser

import modbus_tk
import modbus_tk.defines as cst
import modbus_tk.modbus as modbus
import modbus_tk.modbus_tcp as modbus_tcp

from xmodem import YMODEM

import urllib
import urllib2

import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)

UART_PORT = '/dev/ttyAMA0'  # Raspberry Pi3
UART_TIMEOUT = 3
EXT_BOARD_RST = 26  # pin 38, enable extra board
EXT_BOARD_STS = 22 # light the LED
PIN_4G_EN = 13
PIN_4G_RST = 6

ALL_SENSOR_IDS =[
#    (1, "wind direction"),
#    (2, "wind speed"),
#    (3, "temp"),
    (1, "test"),
]

# register map of device


# upload JSON format, from device to server.
# service should response with download_message when necessary.
upload_message_default = {
   "device": "dst18011234",  # device ID, 'dst' + 'year/month' + 'HHHH'
   "bus": 1,  # modbus number, valid value: [1, 2]
   "node": 2,  # sensor node id, valid value [1, 247], other value means differently
   "command": "read",  # valid value: ["read", "write"] for now
   "data": [0x54, 0x48]  # byte array, maxlen=256, [HH, HH, ...]
}
# download JSON format, from server to device.
# device will response with {"status": 0/err_cod} in addition with original download_message,
# server needs to check the status and retry when necessary.
download_message_default = {
   "device": "dst18011234",  # device ID, 'dst' + 'year/month' + 'HHHH'
   "bus": 1,  # modbus number, valid value: [1, 2]
   "node": 2,  # sensor node id, valid value [1, 247], other value means differently
   "command": "write",  # valid value: ["read", "write"] for now
   "data": [0x54, 0x48]  # byte array, maxlen=256,[HH, HH, ...]
}

# Hold Register Definition
# offset, name, bus, node, addr, size
# offset: offset of the modbus server
# name: sensor description
# bus: bus number, valid in [1,2]
# node: node id
# addr: register address in real sensor
# size: register size in real sensor
SENSORMAP = [
#    reg, description,      bus, node, addr, size
    [0,   'wind speed',     1,   1,    0,    2],
    [2,   'wind direction', 1,   2,    0,    2],
    [4,   'temp and herm',  1,   3,    0,    4],
    [8,   'window',         1,   4,    0,    6],
]


# Upgrade configuration file name
UPGRADE_CONF_FILENAME = "upgrade.conf"
# Default image for ext board
DEFAULT_IMAGE = "default_image.bin"

# setup logger according to running method
parser = OptionParser()
parser.add_option("-q", "--quiet",
                  action="store_true", dest="quiet", default=False,
                  help="write message to log file instead of printing to stdout")
parser.add_option("-n", "--no-4g",
                  action="store_true", dest="no_4g", default=False,
                  help="no 4G modem connection")
parser.add_option("-p", "--no-openvpn",
                  action="store_true", dest="no_openvpn", default=False,
                  help="no openvpn connection")
parser.add_option("-v", "--verbose",
                  action="store_true", dest="verbose", default=False,
                  help="verbose logging")
(options, args) = parser.parse_args()

debug_level = logging.DEBUG if options.verbose else logging.INFO
logging.basicConfig(level=debug_level,
                    format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                    datefmt='%m-%d %H:%M',
                    filename='/var/log/sensorhub.log', # TODO: avoid overwritten
                    filemode='w')
if not options.quiet:
    # define a Handler which writes DEBUG messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(debug_level)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(levelname)-8s %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)


headers = {
    "Content-type": "application/x-www-form-urlencoded",
    "Accept": "text/plain"
}
url = "http://10.0.0.121:8080/ZhiHuiNongYe/uploaddata.do"
def upload(msg = None):
    conn = None
    try:
        if not msg:
            msg = {"device":"dst18014322",
                   "timestamp":"YYMMDDHHMMSS",
                   "bus1":{"raws":["HH","HH","",""]},
                   "bus2":{"raws":["HH","HH"]}}
        msg2 = {'uploaddata': str(msg)}
        data = urllib.urlencode(msg2)
        req = urllib2.Request(url, data)
        response = urllib2.urlopen(req, timeout=2)
        logging.info('upload response=%s' % response.read())
    except Exception as e:
        logging.error('Upload error: %s' % e)
    finally:
        if conn:
            conn.close()

class SensorManager(object):
    def __init__(self, device, baudrate):
        self.device = device
        self.baudrate = baudrate
        self.cpuid = None
        self.sensormap = SENSORMAP
        self._dev = serial.Serial(port=device,
                                  baudrate=baudrate,
                                  parity=serial.PARITY_NONE,
                                  stopbits=serial.STOPBITS_ONE,
                                  bytesize=serial.EIGHTBITS,
                                  timeout=1)
        if not self._dev:
            logging.error('Cannot open device %s' % device)
            raise
        self._running = False
        self._data_received = b''
        self._data_to_write = b''
        self.sensor_data = None
        self._mdbus = None
        self._slave = None
        self._reg_size = 256
        self._mdbus1 = None
        self._mdbus2 = None
        self._updating = False
        self._xmodem_sending = False
        self._modem = YMODEM(self.modem_read, self.modem_write)

    def load_config(self):
        if not self.cpuid:
            return

        cfg = 'dst%s.conf' % self.cpuid
        if not os.path.exists(cfg):
            logging.info('device config file %s not found' % cfg)
            cfg = 'dstcommon.conf'
            if not os.path.exists(cfg):
                logging.info('common config file %s not found' % cfg)
                return
         #    reg, description,      bus, node, addr, size, default
         self.sensormap = []
         for line in file(cfg):
            if line.startswith('#') or not line.strip():
                continue  # comments or empty line
            data = line.split(',').strip()
            if len(data) != 6:
                logging.info('ignore illegal line %s' % line)
                continue
            # TODO: check the correctness
            self.sensormap.append(data)

        if self.sensormap:
            #last offset + size + extra 4 bytes
            len = self.sensormap[-1][0] + self.sensormap[-1][5] + 4
            if len > self._reg_size:
                self._reg_size = len

        logging.info('updated sensor map from file %s' % cfg)
        for d in self.sensormap:
            logging.info(str(d))

    def write_data(self, data):
        logging.debug('<send>: %s' % data)
        if type(data) != type(bytes()):
            data = data.encode()
        self.on_uart_write(data)

    def on_uart_write(self, data):
        self._dev.write(data)
        self._dev.flush()

    def uart_write_thread(self):
        while self._running:
            # Nothing to do right now
            time.sleep(1)

    def on_uart_read(self, data):
        logging.debug("<recv>: %s" % (data if type(data) != type(b"")
                else str(data)))
        self._data_received += data
        # don't process read data when updating
        if self._updating:
            return
        try:
            self.sensor_data = json.loads(self._data_received.decode())
            self._data_received = b''
        except:
            logging.info('invalid message: [%s]' % self._data_received.decode())
            self._data_received = b''
            return

    def uart_read_thread(self):
        while self._running:
            # Xmodem need handle the serial read/write itself
            if self._xmodem_sending:
                time.sleep(1)
                continue
            data = b''
            count = self._dev.inWaiting()
            while count > 0:
                data += self._dev.read(count)
                time.sleep(0.1)
                count = self._dev.inWaiting()
            if data:
                self.on_uart_read(data)

    def reset_ext_board(self):
        logging.warning('reset ext board for upgrading')
        GPIO.output(EXT_BOARD_RST, False)
        time.sleep(1)
        GPIO.output(EXT_BOARD_RST, True)
        time.sleep(2)

    def modem_write(self, data, timeout=1):
        return self._dev.write(data) or None

    def modem_read(self, size, timeout=1):
        while timeout >= 0:
            time.sleep(0.2)
            count = self._dev.inWaiting()
            if count >= size:
                data = self._dev.read(size)
                return data
            timeout -= 0.2
        return None

    def do_upgrade(self, filename=None):
        # wait until ext board is ready for upgrading
        logging.info('begin to upgrade ext board from %s' % filename)
        self.reset_ext_board()
        if not b'Bootloader is started' in self._data_received:
            logging.error('sensor board cannot be reset')
            return False

        logging.info('sensor board is reset')
        timeout = 10
        while timeout > 0:
            time.sleep(0.5)
            logging.debug('writing C to sensor board')
            self._dev.write('C')
            if b'Select 1 or 2' in self._data_received:
                time.sleep(0.2)
                self._dev.write('1')  # select download image
                break
            timeout -= 0.5
        if timeout <= 0:
            logging.error('Bootloader does not work')
            return False

        time.sleep(1)
        self._xmodem_sending = True
        logging.info('updating sensor board...')
        if not self._modem.send([filename,]):
            logging.error('writing sensor board failed')
            self._xmodem_sending = False
            return False
        self._xmodem_sending = False
        logging.info('write sensor board successfully')

        # wait until ext board is ready again
        self._data_received = b''
        self.reset_ext_board()
        if not b'Bootloader is started' in self._data_received:
            logging.error('sensor board cannot be reset after updating')
            return False

        logging.info('sensor board is reset after updating')
        timeout = 10
        while timeout > 0:
            time.sleep(0.5)
            self._dev.write('C')
            if b'Select 1 or 2' in self._data_received:
                time.sleep(0.2)
                self._dev.write('2')  # select run application
                break
            timeout -= 0.5
        if timeout <=0:
            logging.error('Failed to reset')
            return False

        timeout = 20
        while timeout > 0:
            time.sleep(1)
            if b'Sensor board is ready!' in self._data_received:
                logging.debug('<boot>:%s' % self._data_received.decode())
                self._data_received = b''
                logging.info('updating successfully!')
                return True
            timeout -= 1

        logging.info('sensor board reset after updating timeout')
        self._data_received = b''
        return False

    def upgrade_ext_board(self):
        """Use Ymodem to upgrade ext board"""
        binfilename = None
        with file(UPGRADE_CONF_FILENAME, 'rb') as conf:
            lines = conf.readlines()
        for line in lines:
            if line.startswith('filename='):
                binfilename = line.split('=')[1].strip()
                if not binfilename:
                    logging.warning('empty upgrading file, no action')
                    return
                if not os.path.exists(binfilename):
                    logging.error('upgrade filename %s not found' % binfilename)
                    return
        if not binfilename:
            logging.warning('no filename found in %s' % UPGRADE_CONF_FILENAME)
            return

        if not self.do_upgrade(binfilename):
            if os.path.exists(DEFAULT_IMAGE):
                if not self.do_upgrade(DEFAULT_IMAGE):
                    logging.error('ext upgrade failed from default image')
            else:
                logging.error('ext upgrade failed but no default image found')
                return False

        logging.info('ext board is successfully upgraded')
        return True

    def check_upgrade(self):
        if os.path.exists(UPGRADE_CONF_FILENAME):
            logging.debug('found update config file %s' % UPGRADE_CONF_FILENAME)
            self._updating = True
            self.upgrade_ext_board()

            # TODO: check the signature
            self.write_data('get_version()')
            timeout = 5
            while timeout > 0:
                time.sleep(1)
                if b'date' in self._data_received and b'time' in self._data_received:
                    logging.info('<software info>:%s' % self._data_received.decode())
                    self._data_received = b''
                    break
                timeout -= 1

            self._updating = False
            os.unlink(UPGRADE_CONF_FILENAME)

     def read_cpuid(self):
        timeout = 2
        while timeout > 0:
            self.sensor_data = None
            self._dev.write_data('get_cpuid_code()')
            time.sleep(0.5)
            if self.sensor_data:
                break
            timeout -= 0.5
        if self.sensor_data and 'CPUID' in self.sensor_data:
            self.cpuid = self.sensor_data['CPUID']

    def read_modbus(self, cmdstr, timeout):
        while timeout > 0:
            self.sensor_data = None
            self._dev.write_data(cmdstr)
            time.sleep(0.5)
            if self.sensor_data:
                break
            timeout -= 0.5
        return self.sensor_data

    def do_polling(self, timeout=2.0):
        # reg, description, bus, node, addr, size
        for reg, desc, bus, node, addr, size in self.sensormap:
            cmdstr = 'read_hold_reg(%d,%d,%d,%d)' % (bus, node, addr, size)
            resp = self.read_modbus(cmdstr, timeout)
            if resp and 'data' in resp:
                logging.debug('sensor data %s' % resp)
                self._slave.set_values('0', reg, resp['data'])
            else:
                logging.info('failed to read bus %d, node %d, addr %d, size %d' % (
                             desc, bus, node, addr, size))
            time.sleep(1)

    def start_service(self):
        self._running = True
        self.threadrx = threading.Thread(target = self.uart_read_thread)
        self.threadrx.start()
        self.threadtx = threading.Thread(target = self.uart_write_thread)
        self.threadtx.start()
        self._mdbus = modbus_tcp.TcpServer()
        self._mdbus.start()
        self._mdbus1 = self._mdbus.add_slave(1)
        self._mdbus1.add_block('0', cst.HOLDING_REGISTERS, 0, self._reg_size)
        self._slave = self._mdbus.get_slave(1)
        logging.info('MODBUS service is started')

    def stop_service(self):
        self._data_to_write = b''
        self._data_received = b''
        self._running = False
        self._dev.close()
        self.threadtx.join()
        self.threadrx.join()
        self._mdbus.stop()
        logging.info('MODBUS service is stopped')


def main():
    logging.info('Starting sensor hub service')

    GPIO.setup(PIN_4G_EN, GPIO.OUT)
    GPIO.output(PIN_4G_EN, False)
    GPIO.setup(PIN_4G_RST, GPIO.OUT)
    GPIO.output(PIN_4G_RST, False)
    GPIO.setup(EXT_BOARD_RST, GPIO.OUT)
    GPIO.output(EXT_BOARD_RST, True)
    GPIO.setup(EXT_BOARD_STS, GPIO.OUT)
    GPIO.output(EXT_BOARD_STS, False)
    time.sleep(1)

    sm = SensorManager(UART_PORT, 115200)

    try:
        retry = 3
        while not sm.cpuid and retry > 0:
            sm.read_cpuid()
            retry -= 1
        if sm.cpuid:
            logging.info('CPUID = %s' % sm.cpuid)
            sm.load_config()
        else:
            logging.warning('failed to read CPUID, board may be broken')
        sm.start_service()
        while True:
            sm.do_polling()
            sm.check_upgrade()
    except KeyboardInterrupt:
        logging.info('Exiting sensor hub service...')
        sm.stop_service()

        GPIO.cleanup(PIN_4G_RST)
        GPIO.cleanup(PIN_4G_EN)
        GPIO.cleanup(EXT_BOARD_RST)
        GPIO.cleanup(EXT_BOARD_STS)

        sys.exit(0)

if __name__ == '__main__':
    main()
