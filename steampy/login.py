import base64
import time
import requests
from steampy import guard
import rsa

from steampy.exceptions import InvalidCredentials, CaptchaRequired

from typing import Callable


class LoginExecutor:
    COMMUNITY_URL = "https://steamcommunity.com"
    STORE_URL = 'https://store.steampowered.com'

    def __init__(self, username: str, password: str, shared_secret: str, authCallback: Callable, saveCallback: Callable, steamAuth: dict, session: requests.Session) -> None:
        self.username = username
        self.password = password
        self.one_time_code = ''
        self.email_auth = ''
        self.email_callback = authCallback
        self.saveCallback = saveCallback
        self.steamAuth = steamAuth
        self.shared_secret = shared_secret
        self.session = session

    def login(self) -> requests.Session:
        self._set_steamauth_cookies()
        login_response = self._send_login_request()
        self._check_for_captcha(login_response)
        login_response = self._enter_steam_guard_if_necessary(login_response)
        self._assert_valid_credentials(login_response)
        self._save_steam_auth(login_response)
        self._perform_redirects(login_response.json())
        self.set_sessionid_cookies()
        return self.session

    def _send_login_request(self) -> requests.Response:
        rsa_params = self._fetch_rsa_params()
        encrypted_password = self._encrypt_password(rsa_params)
        rsa_timestamp = rsa_params['rsa_timestamp']
        request_data = self._prepare_login_request_data(encrypted_password, rsa_timestamp)
        return self.session.post(self.STORE_URL + '/login/dologin', data=request_data)

    def set_sessionid_cookies(self):
        sessionid = self.session.cookies.get_dict()['sessionid']
        community_domain = self.COMMUNITY_URL[8:]
        store_domain = self.STORE_URL[8:]
        community_cookie = self._create_session_id_cookie(sessionid, community_domain)
        store_cookie = self._create_session_id_cookie(sessionid, store_domain)
        self.session.cookies.set(**community_cookie)
        self.session.cookies.set(**store_cookie)

    def _set_steamauth_cookies(self) -> None:
        for k, v in self.steamAuth.items():
            self.session.cookies.set(k, v)

    @staticmethod
    def _create_session_id_cookie(sessionid: str, domain: str) -> dict:
        return {"name": "sessionid",
                "value": sessionid,
                "domain": domain}

    def _fetch_rsa_params(self, current_number_of_repetitions: int = 0) -> dict:
        maximal_number_of_repetitions = 5
        key_response = self.session.post(self.STORE_URL + '/login/getrsakey/',
                                         data={'username': self.username}).json()
        try:
            rsa_mod = int(key_response['publickey_mod'], 16)
            rsa_exp = int(key_response['publickey_exp'], 16)
            rsa_timestamp = key_response['timestamp']
            return {'rsa_key': rsa.PublicKey(rsa_mod, rsa_exp),
                    'rsa_timestamp': rsa_timestamp}
        except KeyError:
            if current_number_of_repetitions < maximal_number_of_repetitions:
                return self._fetch_rsa_params(current_number_of_repetitions + 1)
            else:
                raise ValueError('Could not obtain rsa-key')

    def _encrypt_password(self, rsa_params: dict) -> str:
        return base64.b64encode(rsa.encrypt(self.password.encode('utf-8'), rsa_params['rsa_key']))

    def _prepare_login_request_data(self, encrypted_password: str, rsa_timestamp: str) -> dict:
        return {
            'password': encrypted_password,
            'username': self.username,
            'twofactorcode': self.one_time_code,
            'emailauth': self.email_auth,
            'loginfriendlyname': 'krystal',
            'loginfriendlyname': '',
            'captchagid': '-1',
            'captcha_text': '',
            'emailsteamid': '',
            'rsatimestamp': rsa_timestamp,
            'remember_login': 'false',
            'donotcache': str(int(time.time() * 1000))
        }

    @staticmethod
    def _check_for_captcha(login_response: requests.Response) -> None:
        if login_response.json().get('captcha_needed', False):
            raise CaptchaRequired('Captcha required')

    def _enter_steam_guard_if_necessary(self, login_response: requests.Response) -> requests.Response:

        response = login_response.json()
        if response['success']:
            return login_response

        if response['requires_twofactor']:
            self.one_time_code = guard.generate_one_time_code(self.shared_secret)
            return self._send_login_request()
        elif response['emailauth_needed']:
            self.email_auth = self.email_callback()
            return self._send_login_request()
            

        return login_response

    @staticmethod
    def _assert_valid_credentials(login_response: requests.Response) -> None:
        if not login_response.json()['success']:
            raise InvalidCredentials(login_response.json()['message'])

    def _save_steam_auth(self, login_response: requests.Response) -> None:
        if callable(self.saveCallback):
            self.saveCallback(login_response.json()['transfer_parameters'])

    def _perform_redirects(self, response_dict: dict) -> None:
        parameters = response_dict.get('transfer_parameters')
        if parameters is None:
            raise Exception('Cannot perform redirects after login, no parameters fetched')
        for url in response_dict['transfer_urls']:
            self.session.post(url, parameters)

    def _fetch_home_page(self, session: requests.Session) -> requests.Response:
        return session.post(self.COMMUNITY_URL + '/my/home/')
