import http.client
import os
import pathlib
import socket
import sys
import urllib.request

endpoint_file = pathlib.Path(os.environ.get('XDG_CACHE_HOME', '/cache')) / 'runtime_endpoint'
endpoint = endpoint_file.read_text().strip() if endpoint_file.is_file() else '127.0.0.1:8080'

if endpoint.startswith('unix:'):

    connection = http.client.HTTPConnection('localhost', timeout=3)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(3)
    connection.sock.connect(endpoint[len('unix:'):])
    connection.request('GET', '/healthz')

    sys.exit(0 if connection.getresponse().status == 200 else 1)

urllib.request.urlopen(f'http://{endpoint}/healthz', timeout=3)
