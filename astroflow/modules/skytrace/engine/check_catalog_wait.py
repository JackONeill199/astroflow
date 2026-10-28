"""Exercise real HTTP stalls, hard timeout, cancellation and lock waiting."""
import json
from pathlib import Path
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import requests
from filelock import FileLock
import catalog_matching as cm


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path=='/slow':time.sleep(8)
        if self.path=='/progress':time.sleep(5.2)
        try:
            self.send_response(200);self.end_headers();self.wfile.write(b'{"answer":42}')
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass


server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}'
try:
    assert cm._bounded_jpl(url,{},False,{},time.monotonic()+5)=={'answer':42}
    began=time.monotonic()
    try:cm._bounded_jpl(url+'/slow',{},False,{},began+.7)
    except requests.Timeout:pass
    else:raise AssertionError('Stalled request escaped deadline')
    assert time.monotonic()-began<2
    with tempfile.TemporaryDirectory(dir=cm.BASE) as temp:
        stop=Path(temp)/'STOP';began=time.monotonic()
        threading.Timer(.4,lambda:stop.touch()).start()
        try:cm._bounded_jpl(url+'/slow',{},False,{'_stop_file':str(stop)},began+10)
        except InterruptedError:pass
        else:raise AssertionError('Cancellation did not interrupt HTTP')
        assert time.monotonic()-began<2
        stop.unlink();threading.Timer(.4,lambda:stop.touch()).start();began=time.monotonic()
        with FileLock(str(Path(temp)/'jpl-service.lock')),patch.object(cm,'CACHE',Path(temp)):
            try:cm.retrieve('https://ssd-api.jpl.nasa.gov/sb_ident.api',settings={'_stop_file':str(stop)})
            except InterruptedError:pass
            else:raise AssertionError('Cancellation did not interrupt lock wait')
        assert time.monotonic()-began<2
    with patch('analyse.event') as notify:
        assert cm._bounded_jpl(url+'/progress',{},False,{'_catalog_position':'Kandidat 3/8'},time.monotonic()+10)=={'answer':42}
        assert any('Kandidat 3/8' in call.kwargs.get('text','') and 'warte auf Antwort' in call.kwargs.get('text','') for call in notify.call_args_list)
    print('OK: real HTTP success, hard deadline, immediate stop, cancelled lock wait and progress heartbeat')
finally:server.shutdown();server.server_close()
