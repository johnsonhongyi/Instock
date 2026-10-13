import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from instock.web.auth import AccountHandler, AuthMixin, UserStore, needs_login

ADMIN_PASSWORD = 'test-admin-password'


class ProtectedHandler(AuthMixin, tornado.web.RequestHandler):
    def get(self):
        self.write('protected')

    def post(self):
        self.write('updated')


class AuthTests(AsyncHTTPTestCase):
    def get_app(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserStore(os.path.join(self.tmp.name, 'auth.db'))
        self.store.change_password('johnson', ADMIN_PASSWORD)
        app = tornado.web.Application([
            ('/login', AccountHandler), ('/instock/users', AccountHandler),
            ('/instock/', ProtectedHandler), ('/instock/api_data', ProtectedHandler),
        ], cookie_secret=self.store.secret,
            template_path=os.path.abspath('instock/web/templates'))
        app.users = self.store
        app.login_attempts = {}
        return app

    def tearDown(self):
        super().tearDown()
        self.tmp.cleanup()

    def login(self, name='johnson', password=ADMIN_PASSWORD):
        page = self.fetch('/login')
        xsrf = page.headers.get_list('Set-Cookie')[0].split(';')[0]
        response = self.fetch('/login', method='POST', follow_redirects=False,
            headers={'Cookie': xsrf}, body=urlencode({
                '_xsrf': xsrf.split('=', 1)[1], 'username': name, 'password': password}))
        cookies = response.headers.get_list('Set-Cookie')
        return response, xsrf, next((c.split(';')[0] for c in cookies if c.startswith('instock_user=')), '')

    def test_access_and_spoofed_forwarding(self):
        self.assertEqual(self.fetch('/instock/').code, 200)
        headers = {'Host': 'johnsonhongyi.asuscomm.com', 'X-Forwarded-For': '192.168.1.5'}
        self.assertEqual(self.fetch('/instock/', headers=headers, follow_redirects=False).code, 302)
        self.assertEqual(self.fetch('/instock/api_data', headers=headers).code, 401)
        self.assertEqual(self.fetch('/instock/users', follow_redirects=False).code, 302)

    def test_login_csrf_password_and_persistence(self):
        self.assertEqual(self.fetch('/login', method='POST', body='username=johnson').code, 403)
        response, xsrf, cookie = self.login()
        self.assertEqual(response.code, 302)
        headers = {'Host': 'johnsonhongyi.asuscomm.com', 'Cookie': cookie}
        self.assertEqual(self.fetch('/instock/', headers=headers).code, 200)
        self.assertIn('添加用户'.encode(), self.fetch('/instock/users', headers=headers).body)
        self.store.change_password('johnson', 'new-password')
        self.assertEqual(self.fetch('/instock/', headers=headers, follow_redirects=False).code, 302)
        reopened = UserStore(self.store.path)
        self.assertIsNone(reopened.verify('johnson', ADMIN_PASSWORD))
        self.assertIsNotNone(reopened.verify('johnson', 'new-password'))
        self.assertEqual(reopened.secret, self.store.secret)

    def test_regular_user_cannot_add_or_reset_others(self):
        self.store.add('reader', 'reader-password')
        response, xsrf, cookie = self.login('reader', 'reader-password')
        self.assertEqual(response.code, 302)
        for action in ('add', 'password'):
            response = self.fetch('/instock/users', method='POST',
                headers={'Cookie': xsrf + '; ' + cookie}, body=urlencode({
                    '_xsrf': xsrf.split('=', 1)[1], 'action': action,
                    'username': 'johnson', 'password': 'replacement-password'}))
            self.assertEqual(response.code, 403)

    def test_admin_add_and_password_forms(self):
        _, xsrf, cookie = self.login()
        headers = {'Cookie': xsrf + '; ' + cookie}
        for fields in [
            {'action': 'add', 'username': 'newuser', 'password': 'initial-password'},
            {'action': 'password', 'username': 'newuser', 'password': 'changed-password'},
        ]:
            fields['_xsrf'] = xsrf.split('=', 1)[1]
            response = self.fetch('/instock/users', method='POST', headers=headers,
                follow_redirects=False, body=urlencode(fields))
            self.assertEqual(response.code, 302)
        self.assertIsNotNone(self.store.verify('newuser', 'changed-password'))
        fields = {'_xsrf': xsrf.split('=', 1)[1], 'action': 'password',
            'old_password': 'wrong-password', 'password': 'replacement-password'}
        response = self.fetch('/instock/users', method='POST', headers=headers, body=urlencode(fields))
        self.assertIn('原密码错误'.encode(), response.body)
        fields['old_password'] = ADMIN_PASSWORD
        self.assertEqual(self.fetch('/instock/users', method='POST', headers=headers,
            follow_redirects=False, body=urlencode(fields)).code, 302)
        self.assertIsNotNone(self.store.verify('johnson', 'replacement-password'))

    def test_cross_origin_post_rejected(self):
        response = self.fetch('/instock/', method='POST', body='', headers={'Origin': 'https://evil.example'})
        self.assertEqual(response.code, 403)


class NetworkTests(unittest.TestCase):
    def test_known_snat_gateway_always_requires_auth(self):
        with patch.dict(os.environ, {}, clear=True):
            for host in ['192.168.50.60:9988', 'localhost', 'johnsonhongyi.asuscomm.com:39988']:
                request = SimpleNamespace(host=host, remote_ip='192.168.50.1',
                    headers={'X-Forwarded-For': '192.168.50.10'})
                self.assertTrue(needs_login(request))
                request.headers = {}
                self.assertTrue(needs_login(request))

    def test_network_policy(self):
        with patch.dict(os.environ, {'INSTOCK_AUTH_TRUSTED_PROXIES': '192.168.1.2/32'}, clear=True):
            for address, forwarded, expected in [
                ('192.168.1.10', '8.8.8.8', True),
                ('8.8.8.8', '192.168.1.10', True),
                ('192.168.1.2', '8.8.8.8', True),
                ('192.168.1.2', '', True),
                ('192.168.1.2', '192.168.1.10', False),
                ('192.168.1.2', 'invalid', True),
            ]:
                request = SimpleNamespace(host='192.168.1.3:9988', remote_ip=address,
                    headers={'X-Forwarded-For': forwarded})
                self.assertEqual(needs_login(request), expected)
            self.assertFalse(needs_login(SimpleNamespace(host='192.168.1.3:9988',
                remote_ip='192.168.1.10', headers={})))
            self.assertTrue(needs_login(SimpleNamespace(host='johnsonhongyi.asuscomm.com.:9988',
                remote_ip='192.168.1.10', headers={})))


if __name__ == '__main__':
    unittest.main()
