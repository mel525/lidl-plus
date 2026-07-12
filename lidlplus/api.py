"""
Lidl Plus api
"""

import base64
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import requests

from lidlplus.exceptions import (
    WebBrowserException,
    LoginError,
    LegalTermsException,
    MissingLogin,
)

try:
    from getuseragent import UserAgent
    from oic.oic import Client
    from oic.utils.authn.client import CLIENT_AUTHN_METHOD
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium import webdriver as selenium_webdriver
    from seleniumwire import webdriver
    from seleniumwire.utils import decode
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
except ImportError as import_error:
    logging.debug("Auth dependencies not installed: %s", import_error)


class LidlPlusApi:
    """Lidl Plus api connector"""

    _CLIENT_ID = "LidlPlusNativeClient"
    _AUTH_API = "https://accounts.lidl.com"
    _TICKET_API = "https://tickets.lidlplus.com/api/v2"
    _TICKET_API_V3 = "https://tickets.lidlplus.com/api/v3"
    _COUPONS_API = "https://coupons.lidlplus.com/app/api"
    _PROFILE_API = "https://profile.lidlplus.com/profile/api"
    _APP = "com.lidlplus.app"
    _OS = "iOs"
    _TIMEOUT = 10

    def __init__(self, language, country, refresh_token=""):
        self._login_url = ""
        self._code_verifier = ""
        self._refresh_token = refresh_token
        self._expires = None
        self._token = ""
        self._country = country.upper()
        self._language = language.lower()

    @property
    def refresh_token(self):
        """Lidl Plus api refresh token"""
        return self._refresh_token

    @property
    def token(self):
        """Current token to query api"""
        return self._token

    def _register_oauth_client(self):
        if self._login_url:
            return self._login_url
        client = Client(client_authn_method=CLIENT_AUTHN_METHOD, client_id=self._CLIENT_ID)
        client.provider_config(self._AUTH_API)
        code_challenge, self._code_verifier = client.add_code_challenge()
        args = {
            "client_id": client.client_id,
            "response_type": "code",
            "scope": ["openid profile offline_access lpprofile lpapis"],
            "redirect_uri": f"{self._APP}://callback",
            **code_challenge,
        }
        auth_req = client.construct_AuthorizationRequest(request_args=args)
        self._login_url = auth_req.request(client.authorization_endpoint)
        return self._login_url

    def _init_chrome(self, headless=True, spoof_user_agent=True):
        logging.getLogger("WDM").setLevel(logging.NOTSET)
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("headless")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        # A spoofed mobile user agent on a desktop Chrome engine is an obvious bot
        # signal to reCAPTCHA. Only spoof it for the headless automated login; a
        # visible manual login scores far better with Chrome's own user agent.
        if spoof_user_agent:
            user_agent = UserAgent(self._OS.lower()).Random()
            options.add_experimental_option("mobileEmulation", {"userAgent": user_agent})
        for chrome_type in [ChromeType.GOOGLE, ChromeType.MSEDGE, ChromeType.CHROMIUM]:
            try:
                service = Service(ChromeDriverManager(chrome_type=chrome_type).install())
                return webdriver.Chrome(service=service, options=options)
            except AttributeError:
                continue
        raise WebBrowserException("Unable to find a suitable Chrome driver")

    def _init_firefox(self, headless=True, spoof_user_agent=True):
        logging.getLogger("WDM").setLevel(logging.NOTSET)
        options = webdriver.FirefoxOptions()
        if headless:
            options.add_argument("-headless")
        if spoof_user_agent:
            user_agent = UserAgent(self._OS.lower()).Random()
            options.set_preference("general.useragent.override", user_agent)
        return webdriver.Firefox(options=options)

    def _get_browser(self, headless=True, spoof_user_agent=True):
        try:
            return self._init_chrome(headless=headless, spoof_user_agent=spoof_user_agent)
        # pylint: disable=broad-except
        except Exception as exc1:
            try:
                return self._init_firefox(headless=headless, spoof_user_agent=spoof_user_agent)
            except Exception as exc2:
                raise WebBrowserException from exc1 and exc2

    def _auth(self, payload):
        default_secret = base64.b64encode(f"{self._CLIENT_ID}:secret".encode()).decode()
        headers = {
            "Authorization": f"Basic {default_secret}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        kwargs = {"headers": headers, "data": payload, "timeout": self._TIMEOUT}
        response = requests.post(f"{self._AUTH_API}/connect/token", **kwargs).json()
        if "error" in response:
            raise LoginError(f"Token request failed: {response.get('error')}")
        self._expires = datetime.now(timezone.utc) + timedelta(seconds=response["expires_in"])
        self._token = response["access_token"]
        self._refresh_token = response["refresh_token"]

    def _renew_token(self):
        payload = {"refresh_token": self._refresh_token, "grant_type": "refresh_token"}
        return self._auth(payload)

    def _authorization_code(self, code):
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{self._APP}://callback",
            "code_verifier": self._code_verifier,
        }
        return self._auth(payload)

    @property
    def _register_link(self):
        args = {
            "Country": self._country,
            "language": f"{self._language}-{self._country}",
        }
        params = "&".join([f"{key}={value}" for key, value in args.items()])
        return f"{self._register_oauth_client()}&{params}"

    @staticmethod
    def _accept_legal_terms(browser, accept=True):
        try:
            checkbox = WebDriverWait(browser, 5).until(
                expected_conditions.visibility_of_element_located((By.ID, "checkbox_Accepted"))
            )
        except TimeoutException:
            return
        checkbox.click()
        if not accept:
            title = browser.find_element(By.TAG_NAME, "h2").text
            raise LegalTermsException(title)
        browser.find_element(By.TAG_NAME, "button").click()

    @staticmethod
    def _extract_code_from_url(url):
        """Extract the OAuth authorization code from a callback url"""
        if not url or "code" not in url:
            return ""
        for candidate in (url, unquote(url)):
            if code := parse_qs(urlparse(candidate).query).get("code"):
                return code[0]
        if code := re.findall("[?&]code=([0-9A-Fa-f-]+)", unquote(url)):
            return code[0]
        return ""

    @staticmethod
    def _collect_page_errors(browser):
        """Grab any visible error messages from the login page for better error reporting"""
        errors = []
        selectors = [
            '[id$="-error"]',
            ".input-error-message",
            '[class*="error-message"]',
            '[role="alert"]',
            '[data-testid*="error"]',
        ]
        for selector in selectors:
            for element in browser.find_elements(By.CSS_SELECTOR, selector):
                if (text := element.text.strip()) and text not in errors:
                    errors.append(text)
        return errors

    @staticmethod
    def _save_login_debug(browser):
        """
        Save a screenshot and the server responses of the login submission,
        so a failed login can be analyzed. Files are written to the current directory.
        """
        debug_files = []
        # pylint: disable=broad-except
        try:
            path = os.path.abspath("lidl-plus-login-debug.png")
            browser.save_screenshot(path)
            debug_files.append(path)
        except Exception as error:
            logging.debug("Could not save debug screenshot: %s", error)
        try:
            parts = [f"<!-- current url: {browser.current_url} -->", browser.page_source]
            for request in browser.requests:
                if "/Account/Login" in request.url and request.method == "POST" and request.response:
                    encoding = request.response.headers.get("Content-Encoding", "identity")
                    body = decode(request.response.body, encoding).decode(errors="replace")
                    status = request.response.status_code
                    location = request.response.headers.get("Location", "")
                    parts.append(f"<!-- POST {request.url} -> {status} location={location} -->")
                    parts.append(body)
            path = os.path.abspath("lidl-plus-login-debug.html")
            with open(path, "w", encoding="utf-8") as file:
                file.write("\n".join(parts))
            debug_files.append(path)
        except Exception as error:
            logging.debug("Could not save debug page source: %s", error)
        return debug_files

    def _parse_code_once(self, browser, accept_legal_terms=True):
        candidate_urls = [browser.current_url]
        for request in reversed(browser.requests):
            candidate_urls.append(request.url)
            if request.response:
                candidate_urls.append(request.response.headers.get("Location") or "")

        for candidate_url in candidate_urls:
            if code := self._extract_code_from_url(candidate_url):
                return code
        for candidate_url in candidate_urls:
            if "legalTerms" in candidate_url:
                self._accept_legal_terms(browser, accept=accept_legal_terms)
                del browser.requests
                break
        return ""

    def _parse_code(self, browser, accept_legal_terms=True, timeout=15):
        """Poll for the authorization code, the redirect can take a moment after login"""
        deadline = time.monotonic() + timeout
        while True:
            if code := self._parse_code_once(browser, accept_legal_terms):
                return code
            if time.monotonic() >= deadline:
                break
            time.sleep(1)
        message = "Unable to parse authorization code from login redirect"
        if errors := self._collect_page_errors(browser):
            message += f" - the login page reports: {' / '.join(errors)}"
        else:
            current = urlparse(browser.current_url)
            message += f" - login never reached the callback (stuck on {current.netloc}{current.path})"
            # pylint: disable=broad-except
            try:
                if app := browser.find_elements(By.ID, "app"):
                    message += f" at step {app[0].get_attribute('app-location')!r}"
                page_text = " ".join(browser.find_element(By.TAG_NAME, "body").text.split())
                message += f' - the page shows: "{page_text[:300]}"'
            except Exception as error:
                logging.debug("Could not read page text: %s", error)
        if debug_files := self._save_login_debug(browser):
            message += f" - debug files saved: {', '.join(debug_files)}"
        raise LoginError(message)

    @staticmethod
    def _parse_app_errors(body):
        """Read the app-errors attribute the login page uses to pass server errors to the frontend"""
        for quote in ("'", '"'):
            if match := re.search(f"app-errors={quote}([^{quote}]*){quote}", body):
                try:
                    if errors := json.loads(match.group(1) or "{}"):
                        return [str(value) for value in errors.values()]
                except json.JSONDecodeError:
                    continue
        return []

    _ERROR_HINTS = {
        "overcapacity": (
            "Lidl's login service is temporarily refusing logins for this account or ip "
            "(rate limiting or high load). Wait 30-60 minutes, then try again once."
        ),
        "locked": "The account seems to be locked. Reset your password via the Lidl app or website.",
    }

    def _add_error_hints(self, errors):
        hints = [hint for needle, hint in self._ERROR_HINTS.items() if any(needle in e.lower() for e in errors)]
        return errors + hints

    def _check_login_error(self, browser):
        try:
            response = browser.wait_for_request(f"{self._AUTH_API}/Account/Login.*", 10).response
        except TimeoutException:
            return
        if response is None:
            return
        body = html.unescape(decode(response.body, response.headers.get("Content-Encoding", "identity")).decode())
        errors = self._parse_app_errors(body) or self._collect_page_errors(browser)
        if errors:
            raise LoginError(" - ".join(self._add_error_hints(errors)))

    _MFA_SEND_BUTTONS = {
        "email": '#sso_2FAvalidation_emailbutton, [data-testid="forgot-password-by-email-button"]',
        "phone": '#sso_2FAvalidation_smsbutton, [data-testid="forgot-password-by-phone-button"]',
    }
    _MFA_CODE_INPUT = '[data-testid="input-verification-code"], input[autocomplete="one-time-code"]'

    def _check_2fa_auth(self, browser, verify_mode="phone", verify_token_func=None):
        """
        Lidl verifies unknown devices with a one time code. After the password a chooser
        appears ("Sicherheitsvalidierung") to receive the code via email or sms, then the
        code input itself. If neither shows up, no verification is needed.
        """
        if verify_mode not in ["phone", "email"]:
            raise ValueError(f'Unknown 2fa-mode "{verify_mode}" - Only "phone" or "email" supported')
        chooser_or_input = (
            f"{self._MFA_SEND_BUTTONS['email']}, {self._MFA_SEND_BUTTONS['phone']}, {self._MFA_CODE_INPUT}"
        )
        try:
            WebDriverWait(browser, 10).until(
                expected_conditions.visibility_of_element_located((By.CSS_SELECTOR, chooser_or_input))
            )
        except TimeoutException:
            return
        if verify_token_func is None:
            raise LoginError("A verification code is required but no verify_token_func was provided")
        other_mode = "email" if verify_mode == "phone" else "phone"
        for mode in (verify_mode, other_mode):
            if buttons := browser.find_elements(By.CSS_SELECTOR, self._MFA_SEND_BUTTONS[mode]):
                buttons[0].click()
                break
        code_input = WebDriverWait(browser, 15).until(
            expected_conditions.visibility_of_element_located((By.CSS_SELECTOR, self._MFA_CODE_INPUT))
        )
        verify_code = verify_token_func()
        code_input.send_keys(verify_code)
        self._submit_form_step(browser, fallback_field=code_input)

    @staticmethod
    def _accept_cookies(browser):
        """Dismiss the cookie consent banner if it shows up, it can block clicks"""
        try:
            WebDriverWait(browser, 3).until(
                expected_conditions.element_to_be_clickable((By.ID, "cookie-consent-accept"))
            ).click()
        except TimeoutException:
            pass

    @staticmethod
    def _submit_form_step(browser, fallback_field=None):
        """Click the primary submit button of the current login step"""
        locators = [
            (By.CSS_SELECTOR, '#duple-button-block button[type="submit"]'),
            (By.CSS_SELECTOR, 'button[data-testid="button-primary"]'),
            (By.XPATH, "/html/body/main/form[1]/div/div/div/div/section/button"),
        ]
        for locator in locators:
            try:
                WebDriverWait(browser, 3).until(expected_conditions.element_to_be_clickable(locator)).click()
                return
            except TimeoutException:
                continue
        if fallback_field is None:
            raise LoginError("Unable to find the submit button of the login form")
        fallback_field.send_keys(Keys.RETURN)

    def login(self, username, password, method="email", **kwargs):
        """
        Simulate app auth.

        :param username: email address or phone number (with country prefix) of your account
        :param password: password of your account
        :param method: "email" or "phone" - which kind of username to log in with
        """
        browser = self._get_browser(headless=kwargs.get("headless", True))
        browser.get(self._register_link)
        wait = WebDriverWait(browser, 15)
        self._accept_cookies(browser)
        if str(method).lower().startswith("p"):
            wait.until(
                expected_conditions.element_to_be_clickable((By.CSS_SELECTOR, '[data-testid="switch-method-button"]'))
            ).click()
            wait.until(expected_conditions.element_to_be_clickable((By.NAME, "input-phone"))).send_keys(username)
        else:
            wait.until(expected_conditions.element_to_be_clickable((By.NAME, "input-email"))).send_keys(username)
        wait.until(
            expected_conditions.element_to_be_clickable(
                (By.CSS_SELECTOR, '[data-testid="login-or-register-submit-button"]')
            )
        ).click()
        password_field = wait.until(expected_conditions.element_to_be_clickable((By.NAME, "Password")))
        password_field.send_keys(password)
        # drop requests captured so far, otherwise the waits below match
        # the initial page load requests instead of the login submission
        del browser.requests
        self._submit_form_step(browser, fallback_field=password_field)
        self._check_login_error(browser)
        verify_mode = kwargs.get("verify_mode", "phone")
        verify_token_func = kwargs.get("verify_token_func")
        self._check_2fa_auth(browser, verify_mode, verify_token_func)
        try:
            browser.wait_for_request(f"{self._AUTH_API}/connect/authorize/callback", 20)
        except TimeoutException:
            # the verification code page may have appeared after the first check
            self._check_2fa_auth(browser, verify_mode, verify_token_func)
            try:
                browser.wait_for_request(f"{self._AUTH_API}/connect/authorize/callback", 15)
            except TimeoutException:
                pass  # _parse_code polls and reports what went wrong
        code = self._parse_code(browser, accept_legal_terms=kwargs.get("accept_legal_terms", True))
        self._authorization_code(code)
        browser.quit()

    def _init_plain_chrome(self):
        """
        A plain Selenium Chrome without selenium-wire's intercepting proxy.

        selenium-wire re-does every TLS handshake in Python, so the connection's
        fingerprint is not Chrome's and reCAPTCHA Enterprise scores it as a bot
        (Lidl then rejects the login as "capacity exceeded"). Plain Chrome keeps
        its real TLS fingerprint; the authorization code is read from Chrome's
        performance log instead of from intercepted requests.
        """
        logging.getLogger("WDM").setLevel(logging.NOTSET)
        options = selenium_webdriver.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        for chrome_type in [ChromeType.GOOGLE, ChromeType.MSEDGE, ChromeType.CHROMIUM]:
            try:
                service = Service(ChromeDriverManager(chrome_type=chrome_type).install())
                return selenium_webdriver.Chrome(service=service, options=options)
            except AttributeError:
                continue
        raise WebBrowserException("Unable to find a suitable Chrome driver")

    @staticmethod
    def _cdp_candidate_urls(browser):
        """Pull every url and redirect Location out of Chrome's performance log"""
        urls = [browser.current_url]
        # pylint: disable=broad-except
        try:
            entries = browser.get_log("performance")
        except Exception as error:
            logging.debug("Could not read performance log: %s", error)
            return urls
        for entry in entries:
            try:
                message = json.loads(entry["message"])["message"]
            except (KeyError, ValueError):
                continue
            params = message.get("params", {})
            urls.append((params.get("request") or {}).get("url", ""))
            urls.append(params.get("documentURL", ""))
            for response in (params.get("redirectResponse"), params.get("response")):
                if not response:
                    continue
                urls.append(response.get("url", ""))
                for key, value in (response.get("headers") or {}).items():
                    if key.lower() == "location":
                        urls.append(value)
            for key, value in (params.get("headers") or {}).items():
                if key.lower() == "location":
                    urls.append(value)
        return urls

    def _parse_code_cdp(self, browser, accept_legal_terms=True, timeout=300):
        """Poll Chrome's performance log for the authorization code while the user logs in"""
        deadline = time.monotonic() + timeout
        while True:
            for candidate_url in self._cdp_candidate_urls(browser):
                if code := self._extract_code_from_url(candidate_url):
                    return code
            if accept_legal_terms and "legalTerms" in browser.current_url:
                self._accept_legal_terms(browser, accept=accept_legal_terms)
            if time.monotonic() >= deadline:
                break
            time.sleep(1)
        message = "Timed out waiting for the manual login to complete"
        if errors := self._collect_page_errors(browser):
            message += f" - the login page reports: {' / '.join(errors)}"
        raise LoginError(message)

    def open_login(self, timeout=300, accept_legal_terms=True):
        """
        Open a visible browser and let the user log in by hand - typing the
        password, solving the reCAPTCHA and entering the one time code Lidl sends.
        Once Lidl redirects back to the app callback, the authorization code is
        captured and exchanged for a token.

        This sidesteps the bot detection that blocks the automated headless login:
        a real person in a real Chrome (with its real TLS fingerprint) passes
        Lidl's reCAPTCHA score check.

        :param timeout: seconds to wait for the user to finish the login
        :param accept_legal_terms: auto-accept updated legal terms if Lidl shows them
        """
        browser = self._init_plain_chrome()
        try:
            browser.get(self._register_link)
            code = self._parse_code_cdp(browser, accept_legal_terms=accept_legal_terms, timeout=timeout)
            self._authorization_code(code)
        finally:
            # pylint: disable=broad-except
            try:
                browser.quit()
            except Exception as error:
                logging.debug("Could not close browser: %s", error)

    def _default_headers(self):
        token_expired = self._expires and datetime.now(timezone.utc) >= self._expires
        if self._refresh_token and (not self._token or token_expired):
            self._renew_token()
        if not self._token:
            raise MissingLogin("You need to login!")
        return {
            "Authorization": f"Bearer {self._token}",
            "App-Version": "999.99.9",
            "Operating-System": self._OS,
            "App": "com.lidl.eci.lidl.plus",
            "Accept-Language": self._language,
        }

    def tickets(self, only_favorite=False):
        """
        Get a list of all tickets.

        :param onlyFavorite: A boolean value indicating whether to only retrieve favorite tickets.
            If set to True, only favorite tickets will be returned.
            If set to False (the default), all tickets will be retrieved.
        :type onlyFavorite: bool
        """
        url = f"{self._TICKET_API}/{self._country}/tickets"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        ticket = requests.get(f"{url}?pageNumber=1&onlyFavorite={only_favorite}", **kwargs).json()
        tickets = ticket["tickets"]
        for i in range(2, int(ticket["totalCount"] / ticket["size"] + 2)):
            tickets += requests.get(f"{url}?pageNumber={i}", **kwargs).json()["tickets"]
        return tickets

    def ticket(self, ticket_id):
        """
        Get full data of single ticket by id.

        Lidl removed the v2 endpoint for single tickets - the v3 response contains the
        receipt as rendered html in the "htmlPrintedReceipt" field instead of itemized json.
        """
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        url = f"{self._TICKET_API_V3}/{self._country}/tickets"
        return requests.get(f"{url}/{ticket_id}", **kwargs).json()

    def _coupons_headers(self):
        return {**self._default_headers(), "Country": self._country}

    def coupon_promotions_v1(self):
        """Get list of all coupons API V1"""
        url = f"{self._COUPONS_API}/v1/promotionslist"
        kwargs = {"headers": self._coupons_headers(), "timeout": self._TIMEOUT}
        return requests.get(url, **kwargs).json()

    def activate_coupon_promotion_v1(self, promotion_id):
        """Activate single coupon by id API V1"""
        url = f"{self._COUPONS_API}/v1/promotions/{promotion_id}/activation"
        kwargs = {"headers": self._coupons_headers(), "timeout": self._TIMEOUT}
        return requests.post(url, **kwargs).text

    def coupons(self):
        """Get list of all coupons"""
        url = f"{self._COUPONS_API}/v2/promotionsList"
        kwargs = {"headers": self._coupons_headers(), "timeout": self._TIMEOUT}
        return requests.get(url, **kwargs).json()

    def activate_coupon(self, coupon_id):
        """Activate single coupon by id"""
        url = f"{self._COUPONS_API}/v1/promotions/{coupon_id}/activation"
        kwargs = {"headers": self._coupons_headers(), "timeout": self._TIMEOUT}
        return requests.post(url, **kwargs).text

    def deactivate_coupon(self, coupon_id):
        """Deactivate single coupon by id"""
        url = f"{self._COUPONS_API}/v1/promotions/{coupon_id}/activation"
        kwargs = {"headers": self._coupons_headers(), "timeout": self._TIMEOUT}
        return requests.delete(url, **kwargs).text

    def loyalty_id(self):
        """
        Get your loyalty ID.

        Warning: Lidl removed this endpoint (it currently returns 404),
        kept here in case it comes back under the same path.
        """
        url = f"{self._PROFILE_API}/v1/{self._country}/loyalty"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        response = requests.get(url, **kwargs)
        response.raise_for_status()
        return response.text
