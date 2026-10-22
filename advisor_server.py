from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        print(self.rfile.read(n).decode('utf-8', 'replace'), flush=True)
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a):
        pass

HTTPServer(('127.0.0.1', 8765), H).serve_forever()
