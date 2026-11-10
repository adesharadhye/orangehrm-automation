import csv
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


logger = logging.getLogger(__name__)
BASE_URL = os.environ.get(
    'ORANGEHRM_BASE_URL',
    'https://opensource-demo.orangehrmlive.com',
).rstrip('/')
WAIT_SECONDS = 20
LOGIN_WAIT_SECONDS = 30


class AutomationError(Exception):
    """Raised when an OrangeHRM automation step fails."""


def _read_employee_rows(driver: webdriver.Chrome) -> list[dict[str, str]]:
    """Convert the visible OrangeHRM employee table into dictionaries."""
    headers = [
        cell.text.strip()
        for cell in driver.find_elements(
            By.CSS_SELECTOR,
            '.oxd-table-header .oxd-table-header-cell',
        )
    ]
    rows: list[dict[str, str]] = []
    for row in driver.find_elements(By.CSS_SELECTOR, '.oxd-table-body .oxd-table-row'):
        values = [
            cell.text.strip()
            for cell in row.find_elements(By.CSS_SELECTOR, '.oxd-table-cell')
        ]
        employee = {
            header: values[index]
            for index, header in enumerate(headers)
            if header and header.casefold() != 'actions' and index < len(values)
        }
        if employee:
            rows.append(employee)
    return rows


def _contains_employee(
    rows: list[dict[str, str]],
    employee_id: str,
    first_name: str,
    last_name: str,
) -> bool:
    """Check whether the table contains the requested employee ID and name."""
    for row in rows:
        normalized = {key.casefold().replace(' ', ''): value for key, value in row.items()}
        row_id = normalized.get('id', normalized.get('employeeid', ''))
        names = ' '.join(row.values()).casefold()
        if (
            row_id == employee_id
            and first_name.casefold() in names
            and last_name.casefold() in names
        ):
            return True
    return False


def _write_csv(rows: list[dict[str, str]]) -> tuple[str, Path]:
    """Save employee rows to a uniquely named CSV in the exports directory."""
    export_dir = Path(__file__).resolve().parents[1] / 'exports'
    export_dir.mkdir(parents=True, exist_ok=True)
    filename = f'employees_{uuid.uuid4().hex}.csv'
    output_path = export_dir / filename
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with output_path.open('w', newline='', encoding='utf-8-sig') as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    return filename, output_path


def _create_driver() -> webdriver.Chrome:
    """Start a headless Chrome browser configured for OrangeHRM's desktop layout."""
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--window-size=1440,1000')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--no-sandbox')
    return webdriver.Chrome(options=options)


def logout_and_close(driver: webdriver.Chrome) -> bool:
    """Log out of OrangeHRM, close Chrome, and report whether logout was confirmed."""
    logged_out = False
    try:
        logger.info('Logging out of OrangeHRM')
        wait = WebDriverWait(driver, WAIT_SECONDS)
        wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'span.oxd-userdropdown-tab'))
        ).click()
        wait.until(
            EC.element_to_be_clickable((By.XPATH, "//a[normalize-space()='Logout']"))
        ).click()
        wait.until(EC.url_contains('/auth/login'))
        logger.info('Logout successful')
        logged_out = True
    except (TimeoutException, WebDriverException):
        logger.exception('OrangeHRM logout could not be verified')
    finally:
        try:
            driver.quit()
        except WebDriverException:
            logger.exception('Could not close the browser cleanly')
    return logged_out


def login_to_orangehrm(username: str, password: str) -> webdriver.Chrome:
    """Authenticate with OrangeHRM and return its live browser session."""
    logger.info('Opening OrangeHRM login page')
    driver = _create_driver()
    try:
        wait = WebDriverWait(driver, WAIT_SECONDS)
        driver.get(f'{BASE_URL}/web/index.php/auth/login')
        wait.until(EC.visibility_of_element_located((By.NAME, 'username'))).send_keys(username)
        driver.find_element(By.NAME, 'password').send_keys(password)
        driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()

        logger.info('Submitting login form')
        login_wait = WebDriverWait(driver, LOGIN_WAIT_SECONDS)
        try:
            login_wait.until(
                lambda browser: bool(
                    browser.find_elements(By.CSS_SELECTOR, '.oxd-alert-content-text')
                )
                or (
                    '/dashboard/index' in browser.current_url
                    and any(
                        element.is_displayed() and element.is_enabled()
                        for element in browser.find_elements(
                            By.CSS_SELECTOR, 'a[href*="/pim/viewPimModule"]'
                        )
                    )
                )
            )
        except TimeoutException as exc:
            logger.exception('OrangeHRM did not respond after login submission')
            raise AutomationError(
                'OrangeHRM did not complete login. Check your credentials and that the '
                'demo site is responsive, then try again.'
            ) from exc

        if '/dashboard/index' not in driver.current_url:
            error_messages = [
                element.text.strip()
                for element in driver.find_elements(
                    By.CSS_SELECTOR,
                    '.oxd-alert-content-text',
                )
                if element.text.strip()
            ]
            message = error_messages[0] if error_messages else 'Please check your credentials.'
            raise AutomationError(f'OrangeHRM login failed: {message}')

        logger.info('OrangeHRM login successful')
        return driver
    except AutomationError:
        try:
            driver.quit()
        except WebDriverException:
            logger.exception('Could not close browser after failed OrangeHRM login')
        raise
    except TimeoutException as exc:
        try:
            driver.quit()
        except WebDriverException:
            logger.exception('Could not close browser after login timeout')
        logger.exception('OrangeHRM login page did not load')
        raise AutomationError(
            'The OrangeHRM login page did not load. Check the demo site and try again.'
        ) from exc
    except WebDriverException as exc:
        try:
            driver.quit()
        except WebDriverException:
            logger.exception('Could not close browser after WebDriver login error')
        logger.exception('WebDriver could not complete OrangeHRM login')
        raise AutomationError(
            'The browser automation could not complete. Check Chrome and Selenium setup.'
        ) from exc


def run_automation(
    driver: webdriver.Chrome,
    first_name: str,
    last_name: str,
    employee_id: str,
) -> dict[str, Any]:
    """Add an employee, verify it in the list, export the list, and log out."""
    logger.info('Automation started for employee ID %s', employee_id)
    wait = WebDriverWait(driver, WAIT_SECONDS)
    result: dict[str, Any] | None = None
    failure: AutomationError | None = None
    step = 'opening PIM navigation'

    try:
        logger.info('Opening PIM')
        wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'a[href*="/pim/viewPimModule"]'))
        ).click()

        step = 'opening the Add Employee form'
        logger.info('Opening Add Employee')
        wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@class, 'oxd-topbar-body-nav-tab-item') "
                    "and normalize-space()='Add Employee']",
                )
            )
        ).click()
        wait.until(EC.visibility_of_element_located((By.NAME, 'firstName'))).send_keys(first_name)
        driver.find_element(By.NAME, 'lastName').send_keys(last_name)
        employee_id_input = wait.until(
            EC.visibility_of_element_located(
                (
                    By.XPATH,
                    "//label[contains(normalize-space(.), 'Employee Id')]/../following-sibling::div//input",
                )
            )
        )
        employee_id_input.clear()
        employee_id_input.send_keys(employee_id)
        driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()

        step = 'verifying employee creation'
        logger.info('Employee form submitted; verifying creation')
        wait.until(
            lambda browser: '/pim/viewPersonalDetails/' in browser.current_url
            or any(
                'successfully saved' in element.text.casefold()
                for element in browser.find_elements(
                    By.CSS_SELECTOR,
                    '.oxd-toast-content-text',
                )
            )
        )

        step = 'opening the employee list'
        logger.info('Opening Employee List')
        wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'a[href*="/pim/viewPimModule"]'))
        ).click()
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.oxd-table-header')))
        rows = _read_employee_rows(driver)

        if not _contains_employee(rows, employee_id, first_name, last_name):
            step = 'searching for the new employee'
            logger.info('Employee not on current list page; filtering by employee ID')
            employee_id_field = driver.find_element(
                By.XPATH,
                "//label[contains(normalize-space(.), 'Employee Id')]/../following-sibling::div//input",
            )
            employee_id_field.clear()
            employee_id_field.send_keys(employee_id)
            driver.find_element(By.XPATH, "//button[normalize-space()='Search']").click()
            wait.until(
                lambda browser: _contains_employee(
                    _read_employee_rows(browser),
                    employee_id,
                    first_name,
                    last_name,
                )
                or bool(
                    browser.find_elements(
                        By.XPATH,
                        "//*[normalize-space()='No Records Found']",
                    )
                )
            )
            rows = _read_employee_rows(driver)

        if not _contains_employee(rows, employee_id, first_name, last_name):
            raise AutomationError(
                'The employee was saved, but could not be found in the employee list.'
            )

        csv_filename, csv_path = _write_csv(rows)
        result = {
            'employee': {
                'first_name': first_name,
                'last_name': last_name,
                'employee_id': employee_id,
            },
            'employees': rows,
            'csv_filename': csv_filename,
            'csv_path': csv_path,
        }
        logger.info('Employee verified and list exported to %s', csv_filename)
    except AutomationError as exc:
        failure = exc
        logger.error('Automation failed: %s', exc)
    except TimeoutException as exc:
        failure = AutomationError(
            f'Automation timed out while {step}. Check the demo site and try again.'
        )
        logger.exception('Automation timed out while %s', step)
    except WebDriverException as exc:
        failure = AutomationError(
            'The browser automation could not complete. Check Chrome and Selenium setup.'
        )
        logger.exception('WebDriver error')
    except (OSError, csv.Error) as exc:
        failure = AutomationError('The employee CSV export could not be written.')
        logger.exception('CSV export failed')
    finally:
        if not logout_and_close(driver) and failure is None:
            failure = AutomationError(
                'The employee operation completed, but logout could not be verified.'
            )

    if failure is not None:
        raise failure
    if result is None:
        raise AutomationError('Automation ended without a result.')
    logger.info('Automation finished successfully')
    return result


def is_export_filename(filename: str) -> bool:
    """Accept only filenames matching the generated employee CSV naming pattern."""
    return re.fullmatch(r'employees_[0-9a-f]{32}\.csv', filename) is not None
