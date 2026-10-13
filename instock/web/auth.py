"""Persistent users and source-aware web authentication."""
import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import tornado.web


BOOTSTRAP_HASH = 'dff00675cced1b9865ecac7e0c81090d:21caa2b910a58ae745db98b7c2e46b0231d15e1612b680f0b97be2ffdc3583e7'


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()


class UserStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, password TEXT NOT NULL, admin INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1)')
            db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            # Initialize only once; never restore a deleted or changed account.
            if not db.execute("SELECT 1 FROM settings WHERE name='initialized'").fetchone():
                db.execute('INSERT INTO users(name,password,admin) VALUES (?,?,1)', ('johnson', BOOTSTRAP_HASH))
                db.execute("INSERT INTO settings VALUES ('initialized','1')")
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('cookie_secret', secrets.token_hex(32)))
            self.secret = db.execute("SELECT value FROM settings WHERE name='cookie_secret'").fetchone()[0]

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        if os.name != 'nt':
            os.chmod(self.path, 0o600)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, name):
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
            return dict(row) if row else None

    def verify(self, name, password):
        user = self.get(name)
        stored = user['password'] if user else BOOTSTRAP_HASH
        valid = hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)
        return user if user and valid else None

    def add(self, name, password, admin=False):
        if not name or len(name) > 64 or any(c.isspace() for c in name):
            raise ValueError('用户名须为 1–64 个非空白字符')
        if not 8 <= len(password) <= 256:
            raise ValueError('密码长度须为 8–256 位')
        try:
            with self.connect() as db:
                db.execute('INSERT INTO users(name,password,admin) VALUES (?,?,?)', (name, password_hash(password), int(admin)))
        except sqlite3.IntegrityError:
            raise ValueError('用户名已存在') from None

    def change_password(self, name, password):
        if not 8 <= len(password) <= 256:
            raise ValueError('密码长度须为 8–256 位')
        with self.connect() as db:
            if not db.execute('UPDATE users SET password=?, version=version+1 WHERE name=?', (password_hash(password), name)).rowcount:
                raise ValueError('用户不存在')


def needs_login(request):
    domains = os.environ.get('INSTOCK_AUTH_PUBLIC_HOSTS', 'johnsonhongyi.asuscomm.com').lower().split(',')
    host = (urlsplit('//' + request.host).hostname or '').rstrip('.')
    if host in [d.strip() for d in domains]:
        return True
    networks = os.environ.get('INSTOCK_AUTH_LAN_CIDRS', '127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16')
    proxies = os.environ.get('INSTOCK_AUTH_TRUSTED_PROXIES', '')
    try:
        address = ipaddress.ip_address(request.remote_ip)
        # The site's WAN port mapping SNATs clients to this router address.
        # Never let a client-controlled Host turn that source into LAN access.
        external_peers = os.environ.get('INSTOCK_AUTH_EXTERNAL_PEERS', '192.168.50.1/32')
        if any(address in ipaddress.ip_network(n.strip()) for n in external_peers.split(',') if n.strip()):
            return True
        trusted = [ipaddress.ip_network(n.strip()) for n in proxies.split(',') if n.strip()]
        if (any(h in request.headers for h in ('X-Forwarded-For', 'X-Forwarded-Host', 'Forwarded'))
                and not any(address in n for n in trusted)):
            return True
        if any(address in n for n in trusted):
            chain = request.headers.get('X-Forwarded-For', '').split(',')
            if not chain[0].strip():
                return True
            for item in reversed(chain):
                address = ipaddress.ip_address(item.strip())
                if not any(address in n for n in trusted):
                    break
        return not any(address in ipaddress.ip_network(n.strip()) for n in networks.split(',') if n.strip())
    except ValueError:
        return True


class AuthMixin:
    def get_current_user(self):
        cookie = self.get_secure_cookie('instock_user', max_age_days=1)
        if cookie:
            try:
                name, version = cookie.decode().rsplit(':', 1)
                user = self.application.users.get(name)
                if user and str(user['version']) == version:
                    return user
            except (ValueError, UnicodeError):
                pass
        return None

    def prepare(self):
        self.set_header('Cache-Control', 'no-store')
        if needs_login(self.request) and not self.current_user:
            if self.request.method in ('GET', 'HEAD') and '/api' not in self.request.path:
                self.redirect('/login')
            else:
                self.set_status(401)
                self.finish({'error': '请先登录', 'login_url': '/login'})
            return
        # Protect existing JSON POST endpoints without changing their clients.
        if self.request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = self.request.headers.get('Origin')
            if (origin and urlsplit(origin).netloc != self.request.host) or self.request.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise tornado.web.HTTPError(403)


class AccountHandler(AuthMixin, tornado.web.RequestHandler):
    def prepare(self):
        self.set_header('Cache-Control', 'no-store')
        if self.request.method == 'POST':
            self.check_xsrf_cookie()

    def page(self, error=''):
        users = []
        if self.current_user and self.current_user['admin']:
            with self.application.users.connect() as db:
                users = db.execute('SELECT name,admin FROM users ORDER BY name').fetchall()
        self.render('account.html', login=self.request.path == '/login', error=error, users=users)

    def get(self):
        if self.request.path != '/login' and not self.current_user:
            self.redirect('/login')
            return
        self.page()

    async def post(self):
        import asyncio
        store = self.application.users
        action = self.get_body_argument('action', '')
        try:
            if self.request.path == '/login':
                # Bound brute-force work and memory per source.
                attempts = self.application.login_attempts
                now = time.monotonic()
                for key in list(attempts):
                    if attempts[key][1] < now - 300:
                        del attempts[key]
                key = self.request.remote_ip
                count, start = attempts.get(key, (0, now))
                if count >= 10 or len(attempts) >= 10000:
                    self.set_status(429)
                    self.page('尝试过多，请 5 分钟后重试')
                    return
                attempts[key] = (count + 1, start)
                password = self.get_body_argument('password')
                if len(password) > 256:
                    raise ValueError('用户名或密码错误')
                user = await asyncio.to_thread(store.verify, self.get_body_argument('username'), password)
                if not user:
                    raise ValueError('用户名或密码错误')
                attempts.pop(key, None)
                self.set_secure_cookie('instock_user', user['name'] + ':' + str(user['version']), httponly=True, secure=self.request.protocol == 'https', samesite='Lax', expires_days=1)
                self.redirect('/instock/')
                return
            if not self.current_user:
                raise tornado.web.HTTPError(401)
            if action == 'logout':
                self.clear_cookie('instock_user')
                self.redirect('/login')
                return
            if action == 'add':
                if not self.current_user['admin']:
                    raise tornado.web.HTTPError(403)
                await asyncio.to_thread(store.add, self.get_body_argument('username'), self.get_body_argument('password'), self.get_body_argument('admin', '') == '1')
            elif action == 'password':
                target = self.get_body_argument('username', self.current_user['name'])
                if target != self.current_user['name'] and not self.current_user['admin']:
                    raise tornado.web.HTTPError(403)
                if target == self.current_user['name'] and not await asyncio.to_thread(store.verify, target, self.get_body_argument('old_password', '')):
                    raise ValueError('原密码错误')
                await asyncio.to_thread(store.change_password, target, self.get_body_argument('password'))
                if target == self.current_user['name']:
                    self.clear_cookie('instock_user')
                    self.redirect('/login')
                    return
            else:
                raise ValueError('无效操作')
            self.redirect('/instock/users')
        except ValueError as exc:
            self.page(str(exc))
