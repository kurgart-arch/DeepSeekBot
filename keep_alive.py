#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Keep-alive: HTTP-сервер /health + self-ping каждые 5 минут (Replit).
Импорт модуля НЕ имеет побочных эффектов — сервер запускается явно
через start_keep_alive_server() из main.py.
"""

import json
import logging
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

logger = logging.getLogger(__name__)

# Живое состояние бота — main.py обновляет через update_status(),
# /health отдаёт его наружу (полезно для UptimeRobot и Replit Autoscale).
STATUS = {
    "started_at": time.time(),
    "last_update": None,
}


def update_status(**kwargs) -> None:
    STATUS.update(kwargs)


def _resolve_replit_url() -> Optional[str]:
    """Публичный URL реплита или None (локальный запуск / не Replit)."""
    domain = os.getenv('REPLIT_DEV_DOMAIN') or os.getenv('REPL_URL')
    if not domain:
        return None
    if domain.startswith('http'):
        return domain.rstrip('/')
    return f"https://{domain}"


class HealthHandler(BaseHTTPRequestHandler):
    # BaseHTTPRequestHandler, а НЕ SimpleHTTPRequestHandler:
    # последний по умолчанию раздаёт файлы текущей директории
    def do_GET(self):
        if self.path == '/health':
            body = json.dumps(STATUS).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()

    def log_message(self, format, *args):
        pass  # не спамим логами


_server_started = False
_server_lock = threading.Lock()


def start_keep_alive_server(port: Optional[int] = None) -> None:
    """Запускает /health-сервер один раз. Порт: env HEALTH_PORT или 5000."""
    global _server_started
    if port is None:
        port = int(os.getenv('HEALTH_PORT', '5000'))
    with _server_lock:
        if _server_started:
            return
        try:
            httpd = HTTPServer(('0.0.0.0', port), HealthHandler)
            threading.Thread(
                target=httpd.serve_forever, daemon=True, name='health-server'
            ).start()
            _server_started = True
            logger.info(f"Health-сервер запущен: порт {port}")
        except OSError as e:
            logger.warning(f"Health-сервер не запущен (порт {port} занят?): {e}")


def keep_alive_thread():
    """Self-ping каждые 5 минут — держит Replit-реплит активным."""
    logger.info("Keep-alive поток запущен")
    url = _resolve_replit_url()

    if not url:
        logger.info(
            "Replit-URL не найден — self-ping выключен "
            "(обычный локальный запуск или VPS)"
        )

    while True:
        time.sleep(300)  # 5 минут
        try:
            if url:
                try:
                    with urllib.request.urlopen(f"{url}/health", timeout=10) as r:
                        logger.debug(f"Keep-alive ping: HTTP {r.status}")
                except OSError as e:
                    logger.debug(f"Keep-ping не прошёл (не критично): {e}")
            logger.info("Keep-alive heartbeat")
        except Exception as e:
            logger.error(f"Ошибка в keep-alive: {e}")
            time.sleep(60)
