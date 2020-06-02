class S3Error(Exception):
    default_message = 'We encountered an internal error. Please try again.'
    default_code = 'InternalError'
    default_status_code = 500

    def __init__(self, message: str = '', code: str = '', status_code=None):
        """
        :param message: 错误描述
        :param code: 错误代码
        :param status_code: HTTP状态码
        """
        self.message = message if message else self.default_message
        self.code = code if code else self.default_code
        self.status_code = self.default_status_code if status_code is None else status_code

    def __repr__(self):
        return f'{type(self)}(message={self.message}, code={self.code}, status_code={self.status_code})'

    def __str__(self):
        return self.message

    def detail_str(self):
        return self.__repr__()

    def err_data(self):
        return {
            'Code': self.code,
            'Message': self.message
        }


class S3InternalError(S3Error):
    pass


class S3InvalidRequest(S3Error):
    default_message = 'Invalid Request'
    default_code = 'InvalidRequest'
    default_status_code = 400


class S3NotFound(S3Error):
    default_message = 'Not found'
    default_code = 'Notfound'
    default_status_code = 404


class S3NoSuchKey(S3NotFound):
    default_message = 'The specified key does not exist.'
    default_code = 'NoSuchKey'


class S3NoSuchBucket(S3NotFound):
    default_message = 'The specified bucket does not exist.'
    default_code = 'NoSuchBucket'


class S3MethodNotAllowed(S3Error):
    default_message = 'The specified method is not allowed against this resource.'
    default_code = 'MethodNotAllowed'
    default_status_code = 405


class S3InvalidSuchKey(S3Error):
    default_message = "The specified object key is not valid."
    default_code = 'InvalidSuchKey'
    default_status_code = 400


class S3InvalidRange(S3Error):
    default_message = 'The requested range cannot be satisfied.'
    default_code = 'InvalidRange'
    default_status_code = 416


class S3InvalidBucketName(S3Error):
    default_message = 'The specified bucket is not valid.'
    default_code = 'InvalidBucketName'
    default_status_code = 400


class S3InvalidArgument(S3Error):
    default_message = 'Invalid Argument.'
    default_code = 'InvalidArgument'
    default_status_code = 400


class S3InvalidAccessKeyId(S3Error):
    default_message = 'The AWS access key ID you provided does not exist in our records.'
    default_code = 'InvalidAccessKeyId'
    default_status_code = 403


class S3AccessDenied(S3Error):
    default_message = 'Access Denied.'
    default_code = 'AccessDenied'
    default_status_code = 403


class S3BucketNotEmpty(S3Error):
    default_message = 'The bucket is not empty.'
    default_code = 'BucketNotEmpty'
    default_status_code = 409


class S3BucketAlreadyOwnedByYou(S3Error):
    default_message = 'The bucket you tried to create already exists, and you own it.'
    default_code = 'BucketAlreadyOwnedByYou'
    default_status_code = 409


class S3BucketAlreadyExists(S3Error):
    default_message = 'The requested bucket name is not available. The bucket namespace is shared by all' \
                      ' users of the system. Please select a different name and try again.'
    default_code = 'BucketAlreadyExists'
    default_status_code = 409


class S3EntityTooSmall(S3Error):
    default_message = 'Your proposed upload is smaller than the minimum allowed object size.'
    default_code = 'EntityTooSmall'
    default_status_code = 400


class S3EntityTooLarge(S3Error):
    default_message = 'Your proposed upload exceeds the maximum allowed object size.'
    default_code = 'EntityTooLarge'
    default_status_code = 400


class S3InvalidDigest(S3Error):
    default_message = 'The Content-MD5 you specified is not valid.'
    default_code = 'InvalidDigest'
    default_status_code = 400


class S3InvalidPart(S3Error):
    default_message = "One or more of the specified parts could not be found. The part might not have been uploaded, " \
                      "or the specified entity tag might not have matched the part's entity tag."
    default_code = 'InvalidPart'
    default_status_code = 400


class S3InvalidPartOrder(S3Error):
    default_message = 'The list of parts was not in ascending order. ' \
                      'Parts list must be specified in order by part number.'
    default_code = 'InvalidPartOrder'
    default_status_code = 400


class S3InvalidSecurity(S3Error):
    default_message = 'The provided security credentials are not valid.'
    default_code = 'InvalidSecurity'
    default_status_code = 403


class S3InvalidURI(S3Error):
    default_message = "Couldn't parse the specified URI."
    default_code = 'InvalidURI'
    default_status_code = 400


class S3SignatureDoesNotMatch(S3Error):
    default_message = "The request signature we calculated does not match the signature you provided. " \
                      "Check your AWS secret access key and signing method."
    default_code = 'SignatureDoesNotMatch'
    default_status_code = 403


class S3AuthorizationHeaderMalformed(S3Error):
    default_message = "The authorization header you provided is invalid."
    default_code = 'AuthorizationHeaderMalformed'
    default_status_code = 400


class S3TooManyBuckets(S3Error):
    default_message = "You have attempted to create more buckets than allowed."
    default_code = 'TooManyBuckets'
    default_status_code = 400


class S3CredentialsNotSupported(S3Error):
    default_message = "This request does not support credentials."
    default_code = 'CredentialsNotSupported'
    default_status_code = 400


class DirectoryAlreadyExists(S3Error):
    default_message = "Directory already exists."
    default_code = 'DirectoryAlreadyExists'
    default_status_code = 400


class ObjectKeyAlreadyExists(S3Error):
    default_message = 'The specified key already exists.'
    default_code = 'ObjectKeyAlreadyExists'
    default_status_code = 400


class S3IncompleteBody(S3Error):
    default_message = "You did not provide the number of bytes specified by the Content-Length HTTP header."
    default_code = 'IncompleteBody'
    default_status_code = 400


class S3MissingContentLength(S3Error):
    default_message = "You must provide the Content-Length HTTP header."
    default_code = 'MissingContentLength'
    default_status_code = 411


class S3RequestIsNotMultiPartContent(S3Error):
    default_message = "Bucket POST must be of the enclosure-type multipart/form-data."
    default_code = 'RequestIsNotMultiPartContent'
    default_status_code = 411
