# OrangeHRM automation dashboard

The frontend and backend are separate local services:

- `frontend/`: plain HTML, CSS, and JavaScript.
- `backend/`: Flask JSON API and Selenium automation.

## Setup

From the project directory, create and activate a virtual environment and
install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Install Google Chrome if it is not already installed. Selenium Manager obtains
the compatible ChromeDriver when the automation is first run.

## Start the backend

Open a PowerShell terminal in the project directory:

```powershell
.\.venv\Scripts\Activate.ps1
$env:JWT_SECRET_KEY = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
python -m backend.app
```

The API listens at `http://127.0.0.1:5000`. Its health endpoint is
`http://127.0.0.1:5000/api/health`.

## Start the frontend

Open a second PowerShell terminal in the project directory:

```powershell
python -m http.server 5500 --directory frontend --bind 127.0.0.1
```

Open <http://127.0.0.1:5500/>. Enter your OrangeHRM credentials and select
**Sign in**. The backend verifies the login and returns a short-lived JWT.
Only after that succeeds does the employee form appear. The JWT is kept in
browser memory and sent as a Bearer token when the employee form is submitted.
The backend runs Selenium, returns the verified employee list, and provides a
CSV download link. The OrangeHRM session is logged out after the employee
operation or when you select **Log out**.

The assignment demo credentials are `Admin` / `admin123`. Credentials are sent
only to the login endpoint and are not written to logs or stored in the JWT.
The JWT expires after 30 minutes and is invalidated on logout or after the
employee operation. The authenticated Selenium browser session is held in
backend memory; restarting the backend ends all sessions.

## Configuration and outputs

- Set `ORANGEHRM_BASE_URL` to use a compatible OrangeHRM environment.
- Set `FRONTEND_ORIGINS` to a comma-separated list of allowed frontend origins
  if you use a different local frontend URL. The default allows ports 5500 on
  `localhost` and `127.0.0.1`.
- `automation.log` records backend workflow steps.
- Successful runs save CSV files in `exports/`.
- `JWT_SECRET_KEY` should be set before starting the backend. A random
  process-local key is used if it is omitted, which invalidates tokens on
  restart.

This development setup binds the API to localhost; do not expose it publicly.
