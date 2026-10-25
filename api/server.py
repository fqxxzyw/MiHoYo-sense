"""在本机同时提供公共预测 API 和网页，无需另外安装 Web 框架。"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import traceback
from urllib.parse import urlsplit

from hoyo_sense.config import PROJECT_ROOT
from api.prediction_service import PredictionService


MAX_BODY_BYTES = 256000
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def create_server(port=8765, *, service=None, app_dir=None):
    service = service or PredictionService()
    app_dir = app_dir or PROJECT_ROOT / "app"

    class Handler(BaseHTTPRequestHandler):
        def send_content(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def json_response(self, status, data):
            self.send_content(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                              "application/json; charset=utf-8")

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/models":
                self.json_response(200, service.catalog())
            elif path == "/api/health":
                self.json_response(200, {"status": "ok", "service": "HoYoSense"})
            elif path in ASSETS:
                filename, content_type = ASSETS[path]
                try:
                    self.send_content(200, (app_dir / filename).read_bytes(), content_type)
                except FileNotFoundError:
                    self.json_response(404, {"error": "界面文件不存在"})
            else:
                self.json_response(404, {"error": "路径不存在"})

        def do_POST(self):
            if urlsplit(self.path).path != "/api/predict":
                self.json_response(404, {"error": "接口不存在"})
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    self.json_response(415, {"error": "请求正文必须是 JSON"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY_BYTES:
                    self.json_response(413, {"error": "请求正文为空或过大"})
                    return
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("请求必须是 JSON 对象")
                result = service.predict(data.get("model_id"), data.get("texts"))
                self.json_response(200, result)
            except (ValueError, UnicodeDecodeError) as error:
                self.json_response(400, {"error": str(error)})
            except Exception:
                traceback.print_exc()
                self.json_response(500, {"error": "模型预测失败，请查看后端终端日志"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = create_server(args.port)
    print(f"HoYoSense 已启动：http://127.0.0.1:{server.server_port}", flush=True)
    print("按 Ctrl+C 停止；此服务只加载模型进行预测，不会训练或修改数据。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
