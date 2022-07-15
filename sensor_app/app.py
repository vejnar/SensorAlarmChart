#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# Copyright (C) 2022 Charles E. Vejnar
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://www.mozilla.org/MPL/2.0/.
#

import argparse
import asyncio
import collections
import json
import os
import socket
import string
import sys
import time

import aioblescan
import aiohttp.web
import bleparser
import tomli

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        s.connect(('10.255.255.255', 1))
        myip = s.getsockname()[0]
    except Exception:
        myip = '127.0.0.1'
    finally:
        s.close()
    return myip

class BLEScanRequesterUpdater(aioblescan.BLEScanRequester):
    def set_parser(self, sensors):
        self.sensors = {sensor['mac'].replace(':', ''): sensor for sensor in sensors}
        self.parser = bleparser.BleParser(discovery=False,
                                          sensor_whitelist=[bytes.fromhex(k.lower()) for k in self.sensors.keys()],
                                          report_unknown=False)

    def default_process(self, data):
        if hasattr(self, 'parser'):
            # Parse sensor data
            try:
                raw_sensor_data, _ = self.parser.parse_data(data)
            except:
                print('Parsing error')
                raw_sensor_data = None

            if raw_sensor_data:
                mac = raw_sensor_data['mac']
                sensor = self.sensors[mac]

                if self.verbose:
                    if 'battery' in raw_sensor_data:
                        battery = raw_sensor_data['battery']
                    else:
                        battery = 'NA'
                    print(f"{mac}  {sensor['label']:<20}  {raw_sensor_data['firmware']:<16}{raw_sensor_data['temperature']:<8}{battery:>6}{raw_sensor_data['rssi']:>6}")

                # Save data
                now = time.time()
                if mac in self.app['ble_status']['data']['history']:
                    record = self.app['ble_status']['data']['history'][mac]
                    if len(record['time']) == 0 or (sensor['history_seconds'] <= now - record['time'][-1]):
                        # Append new data
                        for p in sensor['parameters']:
                            record[p].append(raw_sensor_data[p])
                        record['time'].append(round(now))
                if 'supp_history' in self.app['ble_status']['data'] and mac in self.app['ble_status']['data']['supp_history']:
                    record = self.app['ble_status']['data']['supp_history'][mac]
                    if len(record['time']) == 0 or (sensor['supp_history_seconds'] <= now - record['time'][-1]):
                        # Append new data
                        for p in sensor['parameters']:
                            record[p].append(raw_sensor_data[p])
                        record['time'].append(round(now))

                # Normal ping
                for reporter in self.reporters:
                    reporter.report(mac, f"{sensor['label']}: {sensor['parameters'][0]}={raw_sensor_data[sensor['parameters'][0]]}", ble_status=self.app['ble_status'])

                # Alarm(s)
                if 'alarms' in self.app['ble_status']['sensors'][mac]:
                    for alarm in self.app['ble_status']['sensors'][mac]['alarms']:
                        alert = None
                        sensor_value = raw_sensor_data[alarm['parameter']]
                        if sensor_value < alarm['min']:
                            alert = f"{sensor['label']}: {alarm['parameter']} too low (min {alarm['min']}) at {sensor_value}"
                        elif sensor_value > alarm['max']:
                            alert = f"{sensor['label']}: {alarm['parameter']} too high (max {alarm['max']}) at {sensor_value}"
                        if alert:
                            if self.verbose:
                                print('> ALERT', alert)
                            alarm['counter'] += 1
                            alarm['status'] = 'alert'
                            if alarm['counter'] >= alarm['confirmation']:
                                if self.verbose:
                                    print('> ALARM', alert)
                                alarm['status'] = 'alarm'
                                for reporter in self.reporters:
                                    reporter.report(mac, alert, 'error', ble_status=self.app['ble_status'])
                        else:
                            if alarm['counter'] >= alarm['confirmation']:
                                normal = f"{sensor['label']}: {alarm['parameter']} back to normal range at {sensor_value}"
                                if self.verbose:
                                    print('> NORMAL', normal)
                                for reporter in self.reporters:
                                    reporter.report(mac, normal, 'back', ble_status=self.app['ble_status'])
                            alarm['counter'] = 0
                            alarm['status'] = 'OK'

class Reporter():
    def __init__(self, name, url, error_header, error_footer, ok_interval, error_interval):
        self.name = name
        self.url = url
        self.error_header = error_header
        self.error_footer = error_footer
        self.interval_reports = {'ok': self.parse_time(ok_interval), 'error': self.parse_time(error_interval)}
        self.last_reports = {}

    def parse_time(self, raw_time):
        if raw_time.endswith('d'):
            return int(raw_time[:-1]) * 60. * 60. * 24.
        elif raw_time.endswith('h'):
            return int(raw_time[:-1]) * 60. * 60.
        elif raw_time.endswith('m'):
            return int(raw_time[:-1]) * 60.
        else:
            raise ValueError(raw_time)

    def get_last_report(self, level, idt):
        if level not in self.last_reports:
            self.last_reports[level] = {}
        return self.last_reports[level].get(idt, 0)

    def update_last_report(self, level, idt, t):
        self.last_reports[level][idt] = t

    def get_interval(self, level):
        if level in self.interval_reports:
            return self.interval_reports[level]
        else:
            return self.interval_reports['ok']

    def report(self, idt, status='', level='', ble_status={}):
        now = time.time()
        # Last report time
        last_report = self.get_last_report(level, idt)
        # If last report is old enough
        if last_report + self.get_interval(level) < now:
            self.update_last_report(level, idt, now)
            msg = self.get_message(idt, status, level, ble_status)
            if msg is not None:
                print('Sending message to', self.name.title())
                asyncio.create_task(self.send(msg))

    async def send(self, msg):
        async with aiohttp.ClientSession() as session:
            async with session.post(self.url, json=msg) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print('ERROR', resp.status, text)

class ConsoleReporter(Reporter):
    def get_last_report(self, level, idt):
        if level not in self.last_reports:
            self.last_reports[level] = 0
        return self.last_reports[level]

    def update_last_report(self, level, idt, t):
        self.last_reports[level] = t

    def get_message(self, idt, msg, level='', ble_status={}):
        margin = 4 + int(time.time()) % 4
        # Header
        print()
        print(' '*margin + '┌' + '─'*(25+15+15+15+3) + '┐')
        print(' '*margin + f"│ Sensors{time.strftime('%Y-%m-%d %H:%M'):>64} │")
        print(' '*margin + f"│ IP: {get_ip():<68}│")
        print(' '*margin + '├' + '─'*25 + '┬' + '─'*15 + '┬' + '─'*15 + '┬' + '─'*15 + '┤')
        print(' '*margin +  f"│ {'Sensor':<24}│ {'Temperature':<14}│ {'Battery':<14}│ {'Status':<14}│")
        print(' '*margin + '├' + '─'*25 + '┼' + '─'*15 + '┼' + '─'*15 + '┼' + '─'*15 + '┤')
        # Sensor(s)
        if 'sensors' in ble_status:
            for mac, sensor in ble_status['sensors'].items():
                if len(ble_status['data']['history'][mac]['temperature']) > 0:
                    temperature = ble_status['data']['history'][mac]['temperature'][-1]
                    if 'battery' in ble_status['data']['history'][mac]:
                        battery = ble_status['data']['history'][mac]['battery'][-1]
                    else:
                        battery = 'NA'
                    status = '\x1b[1;32m' + f"{'OK':<14}" + '\x1b[0m'
                    if 'alarms' in sensor:
                        for alarm in sensor['alarms']:
                            if alarm['status'] != 'OK' and alarm['status'] != 'NA':
                                status = '\x1b[1;31m' + f"{alarm['status'].title():<14}" + '\x1b[0m'
                                break
                    print(' '*margin +  f"│ {sensor['label']:<24}│ {temperature:<14}│ {battery:<14}│ {status}│")
        # Footer
        print(' '*margin + '└' + '─'*25 + '┴' + '─'*15 + '┴' + '─'*15 + '┴' + '─'*15 + '┘\n')
        # Error output
        if level == 'error':
            print(msg, file=sys.stderr)

class MatrixReporter(Reporter):
    def get_message(self, idt, msg='', level='', ble_status={}):
        if level == 'error':
            header = self.error_header
            footer = self.error_footer
        else:
            header = ''
            footer = ''
        if len(header) > 0:
            header += '\n'
        if len(footer) > 0:
            footer = '\n' + footer
        return {'msgtype':'m.text', 'body': f"{header}{time.strftime('%Y-%m-%d %H:%M')}\n{msg}{footer}"}

class SlackReporter(Reporter):
    def get_message(self, idt, msg='', level='', ble_status={}):
        if level == 'error':
            header = self.error_header
            footer = self.error_footer
        else:
            header = ''
            footer = ''
        if len(header) > 0:
            header += '\n'
        if len(footer) > 0:
            footer = '\n' + footer
        return {'text': f"{header}{time.strftime('%Y-%m-%d %H:%M')}\n{msg}{footer}"}

async def run_ble(hci_device=0, sensors=[], verbose=False):
    loop = asyncio.get_running_loop()

    # Init BT socket
    bt_socket = aioblescan.create_bt_socket(hci_device)

    # Init controller
    conn, btctrl = await loop._create_connection_transport(bt_socket, BLEScanRequesterUpdater, None, None)
    assert conn.is_reading(), 'Bluetooth device not ready'
    await btctrl.send_scan_request(isactivescan=True)

    # Add sensors to controller
    btctrl.set_parser(sensors)
    # Set verbose
    btctrl.verbose = verbose

    return btctrl

class DequeEncoder(json.JSONEncoder):
    def default(self, obj):
       if isinstance(obj, collections.deque):
          return list(obj)
       return JSONEncoder.default(self, obj)

async def index(request):
    if request.app['proxy'] and 'X-Forwarded-Proto' in request.headers and 'X-Forwarded-Host' in request.headers and 'X-Request-Redirect' in request.headers:
        status_url = f"{request.headers['X-Forwarded-Proto']}://{request.headers['X-Forwarded-Host']}{request.headers['X-Request-Redirect']}status"
    else:
        if request.app['host'] is None:
            status_url = f"http://{get_ip()}:{request.app['port']}/status"
        else:
            status_url = f"http://{request.app['host']}:{request.app['port']}/status"
    return aiohttp.web.Response(text = request.app['tpl_index'].substitute(status_url=status_url),
                                content_type = 'text/html')

async def status(request):
    return aiohttp.web.Response(body = json.dumps(request.app['ble_status'], cls=DequeEncoder),
                                content_type = 'application/json',
                                headers = {'Access-Control-Allow-Origin':'*', 'Access-Control-Allow-Methods': 'GET'})

def create_app(host, port, proxy):
    app = aiohttp.web.Application()

    # Add variables
    app['host'] = host
    app['port'] = port
    app['proxy'] = proxy

    # Add variable to save BLE status
    app['ble_status'] = {'data': {'history': {}}, 'sensors': {}}

    # Init. template(s)
    app['tpl_index'] = string.Template(open(os.path.join(os.path.dirname(__file__), 'templates', 'index.html')).read())

    # Add routes
    app.router.add_static('/static', path=os.path.join(os.path.dirname(__file__), 'static'), append_version=True)
    app.router.add_routes([aiohttp.web.get('/', index),
                           aiohttp.web.get('/status', status)])

    return app

async def run_http(host=None, port=None, proxy=False):
    app = create_app(host, port, proxy)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, host=host, port=port)
    await site.start()
    return app, site

async def start_app_scanner(config):
    # Start web app
    app, site = await run_http(config['general'].get('host'), config['general'].get('port'), config['general']['proxy'])

    # Start BLE scanner
    btctrl = await run_ble(config['general']['hci_device'],
                           config['sensors'],
                           config['general']['verbose'])

    # Init. alarm reporters
    reporters = []
    for reporter in config['reporters']:
        if reporter['name'] == 'console':
            reporters.append(ConsoleReporter('console', reporter['url'], reporter['error_header'], reporter['error_footer'], reporter['ok_interval'], reporter['error_interval']))
        if reporter['name'] == 'matrix':
            reporters.append(MatrixReporter('matrix', reporter['url'], reporter['error_header'], reporter['error_footer'], reporter['ok_interval'], reporter['error_interval']))
        elif reporter['name'] == 'slack':
            reporters.append(SlackReporter('slack', reporter['url'], reporter['error_header'], reporter['error_footer'], reporter['ok_interval'], reporter['error_interval']))

    # Add sensors to app
    for sensor in config['sensors']:
        rmac = sensor['mac'].replace(':', '')
        # Init. alarm counter
        if 'alarms' in sensor:
            for alarm in sensor['alarms']:
                alarm['counter'] = 0
                alarm['status'] = 'NA'
        # Add status to app
        app['ble_status']['data']['history'][rmac] = {p: collections.deque([], maxlen=sensor['history_records']) for p in sensor['parameters']+['time']}
        if 'supp_history_seconds' in sensor:
            if 'supp_history' not in app['ble_status']['data']:
                app['ble_status']['data']['supp_history'] = {}
            app['ble_status']['data']['supp_history'][rmac] = {p: collections.deque([], maxlen=sensor['supp_history_records']) for p in sensor['parameters']+['time']}
        app['ble_status']['sensors'][rmac] = sensor

    # Attach app to scanner
    btctrl.app = app
    # Attach reporters to scanner
    btctrl.reporters = reporters

    # Wait forever
    await asyncio.Event().wait()

def main(argv=None):
    if argv is None:
        argv = sys.argv
    parser = argparse.ArgumentParser(description='SensorAlarmChart app.')
    parser.add_argument('--path_config', action='store', default='config.toml', help='Config path')
    parser.add_argument('--proxy', action='store_true', default=False, help='Use proxy headers')
    args = parser.parse_args(argv[1:])

    # Open config
    config = tomli.load(open(args.path_config, 'rb'))

    # Add arg(s)
    config['general']['proxy'] = args.proxy

    # Start & Wait
    asyncio.run(start_app_scanner(config))

if __name__ == '__main__':
    sys.exit(main())
