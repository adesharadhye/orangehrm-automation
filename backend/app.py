import logging
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
from flask import Flask, jsonify, request, send_file

from .automation import (
    AutomationError,
    is_export_filename,
    login_to_orangehrm,
    logout_and_close,
    run_automation,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
TOKEN_TTL_SECONDS = 30 * 60
DEFAULT_FRONTEND_ORIGINS = {
    'http://127.0.0.1:5500',
    'http://localhost:5500',
}


def _allowed_origins() -> set[str]:
    """Return configured frontend origins, or the two supported localhost URLs."""
    configured = os.environ.get('FRONTEND_ORIGINS')
    if configured is None:
        return DEFAULT_FRONTEND_ORIGINS
    return {origin.strip() for origin in configured.split(',') if origin.strip()}


def _validate_payload(
    payload: Any,
    fields: tuple[str, ...],
) -> tuple[dict[str, str] | None, str | None]:
    """Check that required JSON fields are nonempty strings within their size limits."""
    if not isinstance(payload, dict):
        return None, 'Send the form as a JSON object.'

    values: dict[str, str] = {}
    for field in fields:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return None, f'{field.replace("_", " ").capitalize()} is required.'
        limit = 20 if field == 'employee_id' else 100
        if len(value) > limit:
            return None, f'{field.replace("_", " ").capitalize()} is too long.'
        values[field] = value if field == 'password' else value.strip()
    return values, None


def create_app() -> Flask:
    """Configure the Flask API, authentication sessions, and dashboard endpoints."""
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024
    secret_key = os.environ.get('JWT_SECRET_KEY')
    if not secret_key:
        secret_key = secrets.token_urlsafe(32)
        logging.getLogger(__name__).warning(
            'JWT_SECRET_KEY is not set; tokens will be invalidated when the backend restarts.'
        )
    app.config['JWT_SECRET_KEY'] = secret_key
    app.config['ORANGEHRM_SESSIONS'] = {}
    app.config['ORANGEHRM_SESSIONS_LOCK'] = threading.Lock()

    logging.basicConfig(
        filename=ROOT_DIR / 'automation.log',
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    logger = logging.getLogger('dashboard.api')

    @app.after_request
    def add_cors_headers(response):
        """Allow browser requests only from the configured frontend origins."""
        origin = request.headers.get('Origin')
        if origin in _allowed_origins():
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            response.headers['Vary'] = 'Origin'
        return response

    def remove_session(session_id: str) -> dict[str, Any] | None:
        """Remove and return a browser session safely under the session lock."""
        with app.config['ORANGEHRM_SESSIONS_LOCK']:
            return app.config['ORANGEHRM_SESSIONS'].pop(session_id, None)

    def close_session(session: dict[str, Any]) -> bool:
        """Log out of OrangeHRM and close the session's Selenium browser."""
        return logout_and_close(session['driver'])

    def clear_expired_sessions() -> None:
        """Close browser sessions whose short-lived authentication token expired."""
        now = time.time()
        with app.config['ORANGEHRM_SESSIONS_LOCK']:
            expired_ids = [
                session_id
                for session_id, session in app.config['ORANGEHRM_SESSIONS'].items()
                if session['expires_at'] <= now
            ]
            expired_sessions = [
                app.config['ORANGEHRM_SESSIONS'].pop(session_id)
                for session_id in expired_ids
            ]
        for session in expired_sessions:
            close_session(session)

    def authenticated_session() -> tuple[str | None, dict[str, Any] | None, Any | None]:
        """Validate the Bearer token and return its active OrangeHRM session."""
        auth_header = request.headers.get('Authorization', '')
        scheme, _, token = auth_header.partition(' ')
        if scheme.casefold() != 'bearer' or not token:
            return None, None, (jsonify(error='A valid Bearer token is required.'), 401)
        try:
            claims = jwt.decode(
                token,
                app.config['JWT_SECRET_KEY'],
                algorithms=['HS256'],
                options={'require': ['exp', 'iat', 'jti']},
            )
        except jwt.InvalidTokenError:
            return None, None, (jsonify(error='Your login token is invalid or expired.'), 401)

        session_id = claims.get('jti')
        if not isinstance(session_id, str):
            return None, None, (jsonify(error='Your login token is invalid.'), 401)
        with app.config['ORANGEHRM_SESSIONS_LOCK']:
            session = app.config['ORANGEHRM_SESSIONS'].get(session_id)
        if session is None or session['expires_at'] <= time.time():
            return None, None, (
                jsonify(error='Your OrangeHRM session has expired. Log in again.'),
                401,
            )
        return session_id, session, None

    @app.after_request
    def prevent_response_caching(response):
        """Mark API responses as non-cacheable, especially authentication responses."""
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/health')
    def health():
        """Report that the backend API is responding."""
        return jsonify(status='ok')

    @app.route('/api/login', methods=['POST', 'OPTIONS'])
    def login():
        """Log in to OrangeHRM and issue a signed token for the browser session."""
        if request.method == 'OPTIONS':
            return '', 204
        if not request.is_json:
            return jsonify(error='Content-Type must be application/json.'), 415
        payload, error = _validate_payload(
            request.get_json(silent=True),
            ('username', 'password'),
        )
        if payload is None:
            return jsonify(error=error or 'Invalid login request.'), 400

        clear_expired_sessions()
        try:
            driver = login_to_orangehrm(**payload)
        except AutomationError as exc:
            logger.warning('OrangeHRM login failed: %s', exc)
            return jsonify(error=str(exc)), 401

        now = int(time.time())
        session_id = uuid.uuid4().hex
        expires_at = now + TOKEN_TTL_SECONDS
        with app.config['ORANGEHRM_SESSIONS_LOCK']:
            app.config['ORANGEHRM_SESSIONS'][session_id] = {
                'driver': driver,
                'expires_at': expires_at,
            }
        token = jwt.encode(
            {'jti': session_id, 'iat': now, 'exp': expires_at},
            app.config['JWT_SECRET_KEY'],
            algorithm='HS256',
        )
        logger.info('OrangeHRM login established')
        return jsonify(
            status='authenticated',
            access_token=token,
            token_type='Bearer',
            expires_in=TOKEN_TTL_SECONDS,
        )

    @app.route('/api/automation', methods=['POST', 'OPTIONS'])
    def automate():
        """Create and verify an employee using the authenticated browser session."""
        if request.method == 'OPTIONS':
            return '', 204
        if not request.is_json:
            return jsonify(error='Content-Type must be application/json.'), 415

        payload, error = _validate_payload(
            request.get_json(silent=True),
            ('first_name', 'last_name', 'employee_id'),
        )
        if payload is None:
            return jsonify(error=error or 'Invalid employee request.'), 400

        session_id, session, auth_error = authenticated_session()
        if auth_error:
            return auth_error
        if session_id is None or session is None:
            return jsonify(error='Your login session has expired. Log in again.'), 401
        session = remove_session(session_id)
        if session is None:
            return jsonify(error='Your login session has already ended. Log in again.'), 401

        logger.info('Automation request received for employee ID %s', payload['employee_id'])
        try:
            result = run_automation(session['driver'], **payload)
        except AutomationError as exc:
            logger.error('Automation failed: %s', exc)
            return jsonify(error=str(exc)), 502
        except Exception:
            logger.exception('Unexpected employee automation failure')
            return jsonify(error='An unexpected automation error occurred.'), 500

        return jsonify(
            status='success',
            employee=result['employee'],
            employees=result['employees'],
            csv_filename=result['csv_filename'],
            csv_url=f"/api/exports/{result['csv_filename']}",
        )

    @app.route('/api/logout', methods=['POST', 'OPTIONS'])
    def logout():
        """Revoke the current token and close its OrangeHRM browser session."""
        if request.method == 'OPTIONS':
            return '', 204
        session_id, _, auth_error = authenticated_session()
        if auth_error:
            return auth_error
        if session_id is None:
            return jsonify(error='Your login session has expired. Log in again.'), 401
        session = remove_session(session_id)
        if session is None:
            return jsonify(error='Your OrangeHRM session has already ended.'), 401
        if not close_session(session):
            return jsonify(error='The browser closed, but OrangeHRM logout could not be verified.'), 502
        logger.info('OrangeHRM session closed by user')
        return jsonify(status='logged_out')

    @app.get('/api/exports/<filename>')
    def download_export(filename: str):
        """Send a valid generated employee CSV file as a download."""
        if not is_export_filename(filename):
            return jsonify(error='Export not found.'), 404
        export_path = ROOT_DIR / 'exports' / filename
        if not export_path.is_file():
            return jsonify(error='Export not found.'), 404
        return send_file(
            export_path,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename,
        )

    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5000, debug=False)
