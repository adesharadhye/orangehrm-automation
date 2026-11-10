const API_BASE_URL = 'http://127.0.0.1:5000';

const loginCard = document.getElementById('login-card');
const employeeCard = document.getElementById('employee-card');
const loginForm = document.getElementById('login-form');
const employeeForm = document.getElementById('automation-form');
const successNotice = document.getElementById('success-notice');
const errorNotice = document.getElementById('error-notice');
let accessToken = null;

// Show an API or automation error and hide any previous success notice.
function showError(message) {
  document.getElementById('error-message').textContent = message;
  errorNotice.hidden = false;
  successNotice.hidden = true;
}

// Render the employee records returned by the backend as a safe HTML table.
function showEmployeeTable(rows) {
  const table = document.getElementById('results-card');
  const head = document.getElementById('employee-head');
  const body = document.getElementById('employee-body');
  head.replaceChildren();
  body.replaceChildren();

  if (!rows.length) {
    table.hidden = true;
    return;
  }

  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const headerRow = document.createElement('tr');
  for (const column of columns) {
    const header = document.createElement('th');
    header.textContent = column;
    headerRow.append(header);
  }
  head.append(headerRow);

  for (const row of rows) {
    const tableRow = document.createElement('tr');
    for (const column of columns) {
      const cell = document.createElement('td');
      cell.textContent = row[column] || '—';
      tableRow.append(cell);
    }
    body.append(tableRow);
  }

  document.getElementById('result-count').textContent =
    `${rows.length} record${rows.length === 1 ? '' : 's'}`;
  table.hidden = false;
}

// Clear the in-memory token and return the dashboard to its login form.
function returnToLogin(message) {
  accessToken = null;
  employeeCard.hidden = true;
  loginCard.hidden = false;
  if (message) showError(message);
}

// Send a JSON POST request and include the token when an endpoint requires it.
async function postJson(path, payload, token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });
}

// Authenticate with OrangeHRM before revealing the employee form.
loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = loginForm.querySelector('button[type="submit"]');
  const username = loginForm.elements.username.value.trim();
  const password = loginForm.elements.password.value;
  button.disabled = true;
  button.querySelector('.button-label').textContent = 'Signing in…';
  document.getElementById('login-status').textContent =
    'Connecting to OrangeHRM. This may take a moment.';
  errorNotice.hidden = true;

  try {
    const response = await postJson('/api/login', { username, password });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'OrangeHRM login failed.');

    accessToken = result.access_token;
    loginForm.reset();
    loginCard.hidden = true;
    employeeCard.hidden = false;
    successNotice.hidden = true;
    errorNotice.hidden = true;
  } catch (error) {
    showError(
      error instanceof TypeError
        ? 'Cannot reach the backend. Start the backend server and try again.'
        : error.message,
    );
  } finally {
    loginForm.elements.password.value = '';
    button.disabled = false;
    button.querySelector('.button-label').textContent = 'Sign in';
    document.getElementById('login-status').textContent = '';
  }
});

// Submit employee details, show the result, and return to login when finished.
employeeForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!accessToken) {
    returnToLogin('Please sign in before adding an employee.');
    return;
  }

  const button = employeeForm.querySelector('button[type="submit"]');
  button.disabled = true;
  button.querySelector('.button-label').textContent = 'Automation in progress…';
  document.getElementById('run-status').textContent =
    'This can take a minute. Keep this page open.';
  errorNotice.hidden = true;
  successNotice.hidden = true;
  document.getElementById('results-card').hidden = true;

  const payload = Object.fromEntries(new FormData(employeeForm).entries());
  try {
    const response = await postJson('/api/automation', payload, accessToken);
    const result = await response.json();
    if (!response.ok) {
      if (response.status === 401 || response.status >= 500) {
        returnToLogin(result.error || 'Your OrangeHRM session ended. Please sign in again.');
      } else {
        showError(result.error || 'The backend could not complete the request.');
      }
      return;
    }

    document.getElementById('success-message').textContent =
      `${result.employee.first_name} ${result.employee.last_name} · ID ${result.employee.employee_id}`;
    document.getElementById('csv-link').href = `${API_BASE_URL}${result.csv_url}`;
    successNotice.hidden = false;
    showEmployeeTable(result.employees);
    employeeForm.reset();
    returnToLogin();
  } catch (error) {
    showError(
      error instanceof TypeError
        ? 'Cannot reach the backend. Check that the backend is running.'
        : error.message,
    );
  } finally {
    button.disabled = false;
    button.querySelector('.button-label').textContent = 'Add employee';
    document.getElementById('run-status').textContent = '';
  }
});

// Revoke the current backend session and clear employee form state.
document.getElementById('logout-button').addEventListener('click', async () => {
  const token = accessToken;
  const button = document.getElementById('logout-button');
  button.disabled = true;
  try {
    if (token) {
      const response = await postJson('/api/logout', {}, token);
      if (!response.ok && response.status !== 401) {
        const result = await response.json();
        showError(result.error || 'The backend could not log out cleanly.');
      }
    }
  } catch (error) {
    showError('Could not reach the backend to confirm logout.');
  } finally {
    employeeForm.reset();
    returnToLogin();
    button.disabled = false;
  }
});
