from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import jwt

from backend.app import create_app
from backend.automation import AutomationError, is_export_filename


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def _login(self, driver=None):
        if driver is None:
            driver = MagicMock()
        with patch('backend.app.login_to_orangehrm', return_value=driver) as login:
            response = self.client.post(
                '/api/login',
                json={'username': 'Admin', 'password': 'secret'},
            )
        self.assertEqual(response.status_code, 200)
        login.assert_called_once_with(username='Admin', password='secret')
        return response.json['access_token'], driver

    def test_health_endpoint(self):
        response = self.client.get('/api/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'status': 'ok'})

    def test_login_requires_json(self):
        response = self.client.post('/api/login', data='username=Admin')

        self.assertEqual(response.status_code, 415)

    def test_login_validates_required_credentials(self):
        response = self.client.post('/api/login', json={'username': 'Admin'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('Password is required.', response.json['error'])

    @patch('backend.app.login_to_orangehrm')
    def test_login_returns_signed_token_without_credentials(self, login_to_orangehrm):
        login_to_orangehrm.return_value = MagicMock()

        response = self.client.post(
            '/api/login',
            json={'username': 'Admin', 'password': 'private-password'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['status'], 'authenticated')
        self.assertEqual(response.json['token_type'], 'Bearer')
        claims = jwt.decode(
            response.json['access_token'],
            self.app.config['JWT_SECRET_KEY'],
            algorithms=['HS256'],
        )
        self.assertNotIn('private-password', response.json['access_token'])
        self.assertNotIn('password', claims)
        self.assertEqual(len(self.app.config['ORANGEHRM_SESSIONS']), 1)

    @patch(
        'backend.app.login_to_orangehrm',
        side_effect=AutomationError('Please check your credentials.'),
    )
    def test_login_failure_does_not_issue_token(self, login_to_orangehrm):
        response = self.client.post(
            '/api/login',
            json={'username': 'Admin', 'password': 'wrong'},
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn('access_token', response.json)
        self.assertEqual(self.app.config['ORANGEHRM_SESSIONS'], {})
        login_to_orangehrm.assert_called_once()

    def test_automation_requires_a_valid_bearer_token(self):
        response = self.client.post(
            '/api/automation',
            json={
                'first_name': 'Ada',
                'last_name': 'Lovelace',
                'employee_id': 'EMP-501',
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn('Bearer token', response.json['error'])

    def test_automation_validates_employee_fields(self):
        response = self.client.post(
            '/api/automation',
            json={'first_name': 'Ada'},
            headers={'Authorization': 'Bearer not-a-token'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Last name is required.', response.json['error'])

    @patch('backend.app.run_automation')
    def test_automation_uses_authenticated_session(self, run_automation):
        driver = MagicMock()
        token, _ = self._login(driver)
        run_automation.return_value = {
            'employee': {
                'first_name': 'Ada',
                'last_name': 'Lovelace',
                'employee_id': 'EMP-501',
            },
            'employees': [{'Id': 'EMP-501'}],
            'csv_filename': 'employees_0123456789abcdef0123456789abcdef.csv',
            'csv_path': 'unused',
        }

        response = self.client.post(
            '/api/automation',
            json={
                'first_name': ' Ada ',
                'last_name': ' Lovelace ',
                'employee_id': ' EMP-501 ',
            },
            headers={'Authorization': f'Bearer {token}'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['status'], 'success')
        self.assertEqual(response.json['employee']['employee_id'], 'EMP-501')
        self.assertEqual(
            response.json['csv_url'],
            '/api/exports/employees_0123456789abcdef0123456789abcdef.csv',
        )
        run_automation.assert_called_once_with(
            driver,
            first_name='Ada',
            last_name='Lovelace',
            employee_id='EMP-501',
        )
        self.assertEqual(self.app.config['ORANGEHRM_SESSIONS'], {})
        replay = self.client.post(
            '/api/automation',
            json={
                'first_name': 'Ada',
                'last_name': 'Lovelace',
                'employee_id': 'EMP-501',
            },
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(replay.status_code, 401)

    @patch(
        'backend.app.run_automation',
        side_effect=AutomationError('The employee could not be verified.'),
    )
    def test_automation_failure_closes_authenticated_session(self, run_automation):
        token, _ = self._login()

        response = self.client.post(
            '/api/automation',
            json={
                'first_name': 'Ada',
                'last_name': 'Lovelace',
                'employee_id': 'EMP-501',
            },
            headers={'Authorization': f'Bearer {token}'},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json['error'], 'The employee could not be verified.')
        self.assertEqual(self.app.config['ORANGEHRM_SESSIONS'], {})
        run_automation.assert_called_once()

    @patch('backend.app.logout_and_close', return_value=True)
    def test_logout_revokes_token(self, logout_and_close):
        token, driver = self._login()

        response = self.client.post(
            '/api/logout',
            json={},
            headers={'Authorization': f'Bearer {token}'},
        )
        next_response = self.client.post(
            '/api/logout',
            json={},
            headers={'Authorization': f'Bearer {token}'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['status'], 'logged_out')
        self.assertEqual(next_response.status_code, 401)
        logout_and_close.assert_called_once_with(driver)

    def test_cors_is_restricted_to_frontend_origin(self):
        allowed = self.client.get(
            '/api/health',
            headers={'Origin': 'http://127.0.0.1:5500'},
        )
        rejected = self.client.get(
            '/api/health',
            headers={'Origin': 'https://example.com'},
        )

        self.assertEqual(
            allowed.headers['Access-Control-Allow-Origin'],
            'http://127.0.0.1:5500',
        )
        self.assertNotIn('Access-Control-Allow-Origin', rejected.headers)

    def test_preflight_allows_frontend_authorization_header(self):
        response = self.client.options(
            '/api/automation',
            headers={
                'Origin': 'http://127.0.0.1:5500',
                'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'Content-Type, Authorization',
            },
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers['Access-Control-Allow-Origin'],
            'http://127.0.0.1:5500',
        )
        self.assertIn('Authorization', response.headers['Access-Control-Allow-Headers'])

    def test_export_filename_validation(self):
        self.assertTrue(is_export_filename('employees_0123456789abcdef0123456789abcdef.csv'))
        self.assertFalse(is_export_filename('..\\automation.log'))

    def test_export_route_serves_csv_as_download(self):
        filename = 'employees_0123456789abcdef0123456789abcdef.csv'
        with TemporaryDirectory() as temporary_directory:
            export_directory = Path(temporary_directory) / 'exports'
            export_directory.mkdir()
            (export_directory / filename).write_text('Id,First Name\nEMP-501,Ada\n')
            with patch('backend.app.ROOT_DIR', Path(temporary_directory)):
                response = self.client.get(f'/api/exports/{filename}')
                response.close()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'text/csv')
                self.assertIn('attachment', response.headers['Content-Disposition'])


if __name__ == '__main__':
    unittest.main()
