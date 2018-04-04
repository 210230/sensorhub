#!/usr/bin/env python

import os
import sys
import time
import serial
import threading
import logging
import json
import subprocess

from optparse import OptionParser

import urllib
import urllib2

import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)

EXT_BOARD_RST = 26  # pin 38, reset extra board
EXT_BOARD_STS = 22 # light the LED
PIN_4G_EN = 13
PIN_4G_RST = 6

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
                    filename='/var/log/dialup.log', # TODO: avoid overwritten
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


class ConnectionManager(object):
    def __init__(self):
        self._p_modem = None
        self._p_openvpn = None

    def connect_modem(self, timeout=0):
        logging.info('connecting to 4G ...')
        timeout = 18  # 18*5=90s
        while not os.path.exists('/dev/cdc-wdm0') and timeout > 0:
            logging.info('waiting while enabling 4G module ...')
            time.sleep(5)
            timeout -= 1
        if timeout <= 0:
            logging.erro('failed to connect 4G due to hardware problem')
            return False
        time.sleep(1)
        self._p_modem = subprocess.Popen('/home/dst/ec20', stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, shell=True)
        while True:
            for line in iter(self._p_modem.stdout.readline, ''):
                print line,
                if 'obtained, lease time' in line:
                    logging.info('4G is connected')
                    time.sleep(3)
                    return True
                if 'QMUXError = 0xe' in line:  # No sim card
                    logging.error('failed to connect 4G due to no SIM card found')
                    time.sleep(3)
                    return False

    def disconnect_modem(self):
        if self._p_modem:
            self._p_modem.terminate()
            logging.info('4G is disconnected')

    def connect_openvpn(self, timeout=0):
        logging.info('connecting to openvpn ...')
        cmd = "cd /etc/openvpn && openvpn --config /etc/openvpn/client.conf --verb 3"
        self._p_openvpn = subprocess.Popen(cmd,
                                           stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT,
                                           shell=True)
        while True:
            for line in iter(self._p_openvpn.stdout.readline, ''):
                print line,
                if 'Initialization Sequence Completed' in line:
                    logging.info('openvpn is connected')
                    time.sleep(3)
                    return True

    def disconnect_openvpn(self):
        if self._p_openvpn:
            self._p_openvpn.terminate()
            logging.info('openvpn is disconnected')

    def start_service(self):
        if not options.no_4g:
            self.connect_modem()
        else:
            logging.info('4G connection is disabled')

        if not options.no_openvpn:
            self.connect_openvpn()
        else:
            logging.info('openvpn connection is disabled')

    def stop_service(self):
        self.disconnect_openvpn()
        self.disconnect_modem()

    def is_alive(self):
        return True


def main():
    logging.info('Starting connection manager')

    # Hardware pins
    GPIO.setup(PIN_4G_EN, GPIO.OUT)
    GPIO.output(PIN_4G_EN, False)
    GPIO.setup(PIN_4G_RST, GPIO.OUT)
    GPIO.output(PIN_4G_RST, False)
    GPIO.setup(EXT_BOARD_RST, GPIO.OUT)
    GPIO.output(EXT_BOARD_RST, True)  # low enable
    GPIO.setup(EXT_BOARD_STS, GPIO.OUT)
    GPIO.output(EXT_BOARD_STS, False)
    time.sleep(1)

    cm = ConnectionManager()

    try:
        cm.start_service()
        while True:
            # Nothing to do
            time.sleep(3)
    except KeyboardInterrupt:
        logging.info('Disconnecting connection manager...')
        cm.stop_service()

        GPIO.cleanup(PIN_4G_RST)
        GPIO.cleanup(PIN_4G_EN)
        GPIO.cleanup(EXT_BOARD_RST)
        GPIO.cleanup(EXT_BOARD_STS)

        sys.exit(0)

if __name__ == '__main__':
    main()
