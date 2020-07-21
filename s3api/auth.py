import hmac
from hashlib import sha256
from urllib.parse import quote
from datetime import datetime

from django.utils.translation import gettext as _
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from . import exceptions


AWS4_HMAC_SHA256 = 'AWS4-HMAC-SHA256'
ISO8601 = '%Y-%m-%dT%H:%M:%SZ'
SIGV4_TIMESTAMP = '%Y%m%dT%H%M%SZ'
GMT_FORMAT = '%a, %d %b %Y %H:%M:%S GMT'


class S3V4Authentication(BaseAuthentication):
    """
    S3 v4 based authentication.

    Clients should authenticate by passing the token key in the "Authorization"
    HTTP header, prepended with the string "AWS4-HMAC-SHA256 ".  For example:

        Authorization: AWS4-HMAC-SHA256 Credential=xxx,SignedHeaders=xxx,Signature=xxx
    """

    keyword = AWS4_HMAC_SHA256
    model = None

    def get_model(self):
        if self.model is not None:
            return self.model
        from users.models import AuthKey
        return AuthKey

    def authenticate(self, request):
        credentials = self.get_credentials_from_header(request)
        if credentials is None:
            credentials = self.get_timestamp_from_query(request)

        if credentials is None:
            return None

        return self.authenticate_credentials(request, credentials)

    def get_credentials_from_header(self, request):
        """
        :return:
            None
            {'Credential': x, 'SignedHeaders': x, 'Signature': x}
        """
        auth = get_authorization_header(request).split(maxsplit=1)

        if not auth or auth[0].lower() != self.keyword.lower().encode():
            return None

        if len(auth) == 1:
            msg = _('Invalid auth header. No credentials provided.')
            raise exceptions.S3AuthorizationHeaderMalformed(msg)

        try:
            auth_key_str = auth[1].decode()
        except UnicodeError:
            msg = _('Invalid auth header. Auth string should not contain invalid characters.')
            raise exceptions.S3AuthorizationHeaderMalformed(msg)

        self.s3_timestamp = self.get_timestamp_from_header(request)
        return self.parse_auth_key_string(auth_key_str)

    def get_credentials_from_query(self, request):
        """
        :return:
            None
            {'Credential': x, 'SignedHeaders': x, 'Signature': x, 'Expires': x}
        """
        algorithm = request.query_params.get('X-Amz-Algorithm', None)
        if algorithm is None:
            return None

        if algorithm != 'AWS4-HMAC-SHA256':
            raise exceptions.S3AuthorizationHeaderMalformed()

        self.s3_timestamp = self.get_timestamp_from_query(request)

        return {
            'Credential': request.query_params.get('X-Amz-Credential', ''),
            'SignedHeaders': request.query_params.get('X-Amz-SignedHeaders', ''),
            'Signature': request.query_params.get('X-Amz-Signature', ''),
            'Expires': int(request.query_params.get('X-Amz-Expires', )),    # 86400(24h), 最大604800(7days)
        }

    def authenticate_credentials(self, request, credentials):
        credential = credentials.get('Credential')
        signed_headers = credentials.get('SignedHeaders')
        signature = credentials.get('Signature')

        self.s3_credential = credential
        self.s3_signed_headers = signed_headers

        access_key, date, self._region_name, self._service_name, _ = credential.split('/')
        model = self.get_model()
        try:
            auth_key = model.objects.select_related('user').get(id=access_key)
        except model.DoesNotExist:
            raise exceptions.S3InvalidAccessKeyId()

        if not auth_key.user.is_active:
            raise exceptions.S3InvalidAccessKeyId(_('User inactive or deleted.'))

        # 是否未激活暂停使用
        if not auth_key.is_key_active():
            raise exceptions.S3InvalidAccessKeyId(_('Invalid access_key. Key is inactive and unavailable'))

        # 验证加密signature
        sig = self.generate_signature(request=request, signed_headers=signed_headers, secret_key=auth_key.secret_key)
        if sig != signature:
            raise exceptions.S3AuthorizationHeaderMalformed()

        return auth_key.user, auth_key  # request.user, request.auth

    @staticmethod
    def parse_auth_key_string(auth_key):
        auth = auth_key.split(',')
        if len(auth) != 3:
            raise exceptions.S3AuthorizationHeaderMalformed()

        ret = {}
        for a in auth:
            a = a.strip(' ')
            name, val = a.split('=', maxsplit=1)
            if name not in ['Credential', 'SignedHeaders', 'Signature']:
                raise exceptions.S3AuthorizationHeaderMalformed()
            ret[name] = val

        return ret

    def authenticate_header(self, request):
        return self.keyword

    def generate_signature(self, request, signed_headers: str, secret_key: str):
        try:
            canonical_request = self.canonical_request(request, signed_headers)
            string_to_sign = self.string_to_sign(canonical_request)
            return self.signature(string_to_sign, secret_key)
        except Exception as e:
            raise exceptions.S3AuthorizationHeaderMalformed()

    def canonical_headers(self, request, signed_headers: str):
        """
        Return the headers that need to be included in the StringToSign
        in their canonical form by converting all header keys to lower
        case, sorting them in alphabetical order and then joining
        them into a string, separated by newlines.
        """
        headers = []
        sorted_header_names = signed_headers.split(';')
        for key in sorted_header_names:
            v = request.headers.get(key)
            value = ','.join([self._header_value(v)])
            headers.append('%s:%s' % (key, value))
        return '\n'.join(headers)

    def canonical_request(self, request, signed_headers: str):
        cr = [request.method.upper()]
        path = request.path
        cr.append(quote(path))
        cr.append(self.canonical_query_string(request))
        cr.append(self.canonical_headers(request, signed_headers) + '\n')
        cr.append(signed_headers)
        body_checksum = request.headers['X-Amz-Content-SHA256']
        cr.append(body_checksum)
        return '\n'.join(cr)

    def canonical_query_string(self, request):
        # The query string can come from two parts.  One is the
        # params attribute of the request.  The other is from the request
        # url (in which case we have to re-split the url into its components
        # and parse out the query string component).
        l = []
        params = request.query_params
        names = params.keys()
        for name in sorted(names):
            value = str(params[name])
            l.append('%s=%s' % (quote(name, safe='-_.~'), quote(value, safe='-_.~')))
        cqs = '&'.join(l)
        return cqs

    def _header_value(self, value):
        # From the sigv4 docs:
        # Lowercase(HeaderName) + ':' + Trimall(HeaderValue)
        #
        # The Trimall function removes excess white space before and after
        # values, and converts sequential spaces to a single space.
        return ' '.join(value.split())

    def string_to_sign(self, canonical_request):
        """
        Return the canonical StringToSign as well as a dict
        containing the original version of all headers that
        were included in the StringToSign.
        """
        sts = ['AWS4-HMAC-SHA256']
        sts.append(self.s3_timestamp)
        sts.append(self.credential_scope())
        sts.append(sha256(canonical_request.encode('utf-8')).hexdigest())
        return '\n'.join(sts)

    def scope(self):
        return self.s3_credential

    def credential_scope(self):
        return self.s3_credential.split('/', maxsplit=1)[-1]        # 不包含access_key

    def get_timestamp_from_header(self, request):
        headers = request.headers
        t = headers.get('Date', None)
        if t is not None:
            dt = datetime.strptime(t, GMT_FORMAT)
            self.s3_datetime = dt
            return dt.strftime(SIGV4_TIMESTAMP)

        t = headers.get('X-Amz-Date', None)
        if t is not None:
            dt = datetime.strptime(t, SIGV4_TIMESTAMP)
            self.s3_datetime = dt
            return t

        return ''

    def get_timestamp_from_query(self, request):
        t = request.query_params.get('X-Amz-Date', None)
        if t is None:
            return ''

        dt = datetime.strptime(t, SIGV4_TIMESTAMP)
        self.s3_datetime = dt
        return t

    def signature(self, string_to_sign, secret_key):
        key = secret_key
        k_date = self._sign(('AWS4' + key).encode('utf-8'), self.s3_timestamp[0:8])
        k_region = self._sign(k_date, self._region_name)
        k_service = self._sign(k_region, self._service_name)
        k_signing = self._sign(k_service, 'aws4_request')
        return self._sign(k_signing, string_to_sign, hex=True)

    def _sign(self, key, msg, hex=False):
        if hex:
            sig = hmac.new(key, msg.encode('utf-8'), sha256).hexdigest()
        else:
            sig = hmac.new(key, msg.encode('utf-8'), sha256).digest()
        return sig
