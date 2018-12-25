#!/usr/bin/env python
#
#   radiosonde_auto_rx - Web Interface
#
#   Copyright (C) 2018  Mark Jessop <vk5qi@rfhead.net>
#   Released under MIT License
#
import datetime
import json
import logging
import random
import requests
import time
import traceback
import autorx
import autorx.config
import autorx.scan
from threading import Thread
import flask
from flask_socketio import SocketIO
try:
    # Python 2
    from Queue import Queue
except ImportError:
    # Python 3
    from queue import Queue


# Instantiate our Flask app.
app = flask.Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
# This thread will hold the currently running flask application thread.
flask_app_thread = None
# A key that needs to be matched to allow shutdown.
flask_shutdown_key = "temp"

# SocketIO instance
socketio = SocketIO(app)

# Global store of telemetry data, which we will add data to and manage.
# Under each key (which will be the sonde ID), we will have a dictionary containing:
#   'latest_timestamp': timestamp (unix timestamp) of when the last packet was received.
#   'latest_telem': telemetry dictionary.
#   'path': list of [lat,lon,alt] pairs
#
flask_telemetry_store = {}

#
# Globally called 'emit' function
#
def flask_emit_event(event_name="none", data={}):
    """ Emit a socketio event to any clients. """
    socketio.emit(event_name, data, namespace='/update_status')

#
#   Flask Routes
#

@app.route("/")
def flask_index():
    """ Render main index page """
    return flask.render_template('index.html')

@app.route("/sondehub")
def flask_sondehub():
    """ Render sondehub page """
    return flask.render_template('sondehub.html')


@app.route("/get_version")
def flask_get_version():
    """ Return auto_rx version to client """
    return autorx.__version__


@app.route("/get_task_list")
def flask_get_task_list():
    """ Return the current list of active SDRs, and their active task names """

    # Read in the task list, index by SDR ID. 
    _task_list = {}
    for _task in autorx.task_list.keys():
        _task_list[str(autorx.task_list[_task]['device_idx'])] = _task

    # Now, for each configured SDR, determine what task it is currently performing
    _sdr_list = {}

    for _sdr in autorx.sdr_list.keys():
        _sdr_list[str(_sdr)] = 'Not Tasked'
        if str(_sdr) in _task_list:
            if _task_list[str(_sdr)] == 'SCAN':
                _sdr_list[str(_sdr)] = 'Scanning'
            else:
                try:
                    _sdr_list[str(_sdr)] = "Decoding (%.3f MHz)" % (_task_list[str(_sdr)]/1e6)
                except:
                    _sdr_list[str(_sdr)] = "Decoding (?? MHz)"

    # Convert the task list to a JSON blob, and return.
    return json.dumps(_sdr_list)


@app.route("/get_config")
def flask_get_config():
    """ Return a copy of the current auto_rx configuration """
    # Grab a copy of the config
    _config = autorx.config.global_config

    # TODO: Sanitise config output a bit?
    return json.dumps(_config)


@app.route("/get_scan_data")
def flask_get_scan_data():
    """ Return a copy of the latest scan results """
    return json.dumps(autorx.scan.scan_result)


@app.route("/get_telemetry_archive")
def flask_get_telemetry_archive():
    """ Return a copy of the telemetry archive """
    return json.dumps(flask_telemetry_store)


@app.route("/shutdown/<shutdown_key>")
def shutdown_flask(shutdown_key):
    """ Shutdown the Flask Server """
    global flask_shutdown_key
    # Only shutdown if the supplied key matches our shutdown key
    if shutdown_key == flask_shutdown_key:
        flask.request.environ.get('werkzeug.server.shutdown')()

    return ""


#
# SocketIO Events
#

@socketio.on('client_connected', namespace='/update_status')
def refresh_client(arg1):
    """ A client has connected, let them know to grab data."""
    logging.info("Flask - New Web Client connected!")
    # Tell them to get a copy of the latest scan results.
    flask_emit_event('scan_event')
    flask_emit_event('task_event')
    # TODO: Send last few log entries?


#
#   Flask Startup & Shutdown Helper Scripts
#

def flask_thread(host='0.0.0.0', port=5000):
    """ Flask Server Thread"""
    socketio.run(app, host=host, port=port)


def start_flask(host='0.0.0.0', port=5000):
    """ Start up the Flask Server """
    global flask_app_thread, flask_shutdown_key
    # Generate the shutdown key
    flask_shutdown_key = str(random.randint(10000,100000000))

    # Start up Flask
    flask_app_thread = Thread(target=flask_thread, kwargs={'host':host, 'port':port})
    flask_app_thread.start()
    logging.info("Started Flask server on http://%s:%d" % (host,port))


def stop_flask(host='0.0.0.0', port=5000):
    """ Shutdown the Flask Server by submmitting a shutdown request """
    global flask_shutdown_key
    try:
        r = requests.get('http://%s:%d/shutdown/%s' % (host,port, flask_shutdown_key))
        logging.info("Web - Flask Server Shutdown.")
    except:
        # TODO: Cleanup errors
        traceback.print_exc()



class WebHandler(logging.Handler):
    """ Logging Handler for sending log messages via Socket.IO to a Web Client """

    def emit(self, record):
        """ Emit a log message via SocketIO """
        if 'socket.io' not in record.msg:
            # Convert log record into a dictionary
            log_data = {
                'level': record.levelname,
                'timestamp': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                'msg': record.msg
            }
            # Emit to all socket.io clients
            socketio.emit('log_event', log_data, namespace='/update_status')


class WebExporter(object):
    """ Push Radiosonde Telemetry Data to a web client """

    # We require the following fields to be present in the incoming telemetry dictionary data
    REQUIRED_FIELDS = ['frame', 'id', 'datetime', 'lat', 'lon', 'alt', 'temp', 'type', 'freq', 'freq_float', 'datetime_dt']

    def __init__(self,
        max_age = 120):
        """ Initialise a WebExporter object.

        Args:
            max_age: Store telemetry data up to X hours old
        """

        self.max_age = max_age*60
        self.input_queue = Queue()

        # Start the input queue processing thread.
        self.input_processing_running = True
        self.input_thread = Thread(target=self.process_queue)
        self.input_thread.start()
        

    def process_queue(self):
        """ Process data from the input queue.
        """
        while self.input_processing_running:
            # Read in all queue items and handle them.
            while not self.input_queue.empty():
                self.handle_telemetry(self.input_queue.get())

            # Check the telemetry store for old data.
            self.clean_telemetry_store()

            # Wait a short time before processing new data
            time.sleep(0.1)

        logging.debug("WebExporter - Closed Processing thread.")


    def handle_telemetry(self,telemetry):
        """ Send incoming telemetry to clients, and add it to the telemetry store. """
        global flask_telemetry_store

        if telemetry == None:
            logging.error("WebExporter - Passed NoneType instead of Telemetry.")
            return

        for _field in self.REQUIRED_FIELDS:
            if _field not in telemetry:
                logging.error("WebExporter - JSON object missing required field %s" % _field)
                return
        
        _telem = telemetry.copy()
        # Remove the datetime object that is part of the telemetry, if it exists.
        # (it might not be present in test data)
        if 'datetime_dt' in _telem:
            _telem.pop('datetime_dt')

        socketio.emit('telemetry_event', _telem, namespace='/update_status')

        # Add the telemetry information to the global telemetry store
        if _telem['id'] not in flask_telemetry_store:
            flask_telemetry_store[_telem['id']] = {'timestamp':time.time(), 'latest_telem':_telem, 'path':[]}

        flask_telemetry_store[_telem['id']]['path'].append([_telem['lat'],_telem['lon'],_telem['alt']])
        flask_telemetry_store[_telem['id']]['latest_telem'] = _telem
        flask_telemetry_store[_telem['id']]['timestamp'] = time.time()


    def clean_telemetry_store(self):
        """ Remove any old data from the telemetry store """
        global flask_telemetry_store

        _now = time.time()
        _telem_ids = list(flask_telemetry_store.keys())
        for _id in _telem_ids:
            # If the most recently telemetry is older than self.max_age, remove all data for
            # that sonde from the archive.
            if (_now - flask_telemetry_store[_id]['timestamp']) > self.max_age:
                flask_telemetry_store.pop(_id)
                logging.debug("WebExporter - Removed Sonde #%s from archive." % _id)
        


    def add(self, telemetry):
        # Add it to the queue if we are running.
        if self.input_processing_running:
            self.input_queue.put(telemetry)
        else:
            logging.error("WebExporter - Processing not running, discarding.")


    def close(self):
        """ Shutdown """
        self.input_processing_running = False


#
# Testing Functions, for easier web development.
#

def test_web_log_to_dict(log_line):
    """ Convert a line read from a sonde log to a 'fake' telemetery dictionary """

    # ['frame', 'id', 'datetime', 'lat', 'lon', 'alt', 'temp', 'type', 'freq', 'freq_float', 'datetime_dt']
    # ('2017-12-29T23:20:47.420', 'M2913212', 1563, -34.94541, 138.52819, 761.7, -273., 'RS92', 401.52)
    try:
        _telem = {
            'frame': log_line[2],
            'id': log_line[1],
            'datetime': log_line[0],
            'lat': log_line[3],
            'lon': log_line[4],
            'alt': log_line[5],
            'temp': log_line[6],
            'type': log_line[7],
            'freq': str(log_line[8])+" MHz",
            'freq_float': log_line[8],
            'vel_v': 0.0,
            'datetime_dt': None,
            'sdr_device_idx': '00000001'
        }
        return _telem
    except:
        return None


def test_web_interface(file_list, delay=1.0):
    """ Test the web interface map functions by injecting a large amount of sonde telemetry data from sonde log files. """
    import numpy as np
    global _web

    print(file_list)

    _sondes = []
    # Minimum number of data points in a file
    _min_data = 10000

    # Read in files and add data to _sondes.
    for _file_name in file_list:
        try:
            _data = np.genfromtxt(_file_name, delimiter=',', dtype=None)
            _sondes.append(_data)
            print("Read %d records from %s" % (len(_data), _file_name))
            if len(_data) < _min_data:
                _min_data = len(_data)
        except:
            print("Could not read %s" % _file_name)

    # Number of data points to feed in initially. (10%)
    _i = _min_data//10

    # Start up a WebExporter instance
    _web = WebExporter()

    # Feed in the first 10% of data points from each sonde.
    print("Injecting %d initial data points." % _i)
    for _sonde in _sondes:
        for _j in range(0,_i):
            _web.add(test_web_log_to_dict(_sonde[_j]))

    # Now add in new data every second until CTRL-C
    for _k in range(_i,_min_data):
        for _sonde in _sondes:
            _web.add(test_web_log_to_dict(_sonde[_k]))

        logging.info("Added new telemetry data: %d/%d" % (_k,_min_data))
        time.sleep(delay)



if __name__ == "__main__":
    # Test script to start up the flask server and show some dummy log data
    # This script should be called from the auto_rx directory with:
    # python -m autorx.web filename1_sonde.log filename2_sonde.log ..etc
    # 
    import time, sys
    from autorx.config import read_auto_rx_config
    logging.basicConfig(format='%(asctime)s %(levelname)s:%(message)s', level=logging.DEBUG)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    logging.getLogger('socketio').setLevel(logging.ERROR)
    logging.getLogger('engineio').setLevel(logging.ERROR)

    # Read in config, as the web interface now uses a lot of config data during startup.
    # TODO: Make this actually work... it doesnt seem to be writing into the global_config store
    #_temp_cfg = read_auto_rx_config('station.cfg')

    web_handler = WebHandler()
    logging.getLogger().addHandler(web_handler)
    start_flask()

    try:
        # If we have been provided some sonde logs as an argument, read them in.
        if len(sys.argv) > 1:
            test_web_interface(sys.argv[1:], delay=1.0)
        else:
            while flask_app_thread.isAlive():
                time.sleep(1)
                logging.info("This is a test message.")
    except:
        stop_flask()

    
