from django.utils.translation import gettext as _
from rest_framework.response import Response
from rest_framework import status

from .renders import CusXMLRenderer
from .viewsets import CustomGenericViewSet
from buckets.models import Bucket
from .serializers import BucketListSerializer
from . import exceptions


class MainHostViewSet(CustomGenericViewSet):
    """
    主域名请求视图集
    """
    permission_classes = []

    def list(self, request, *args, **kwargs):
        """
        list buckets

        HTTP/1.1 200
        <?xml version="1.0" encoding="UTF-8"?>
        <ListAllMyBucketsResult>
           <Buckets>
              <Bucket>
                 <CreationDate>timestamp</CreationDate>
                 <Name>string</Name>
              </Bucket>
           </Buckets>
           <Owner>
              <DisplayName>string</DisplayName>
              <ID>string</ID>
           </Owner>
        </ListAllMyBucketsResult>
        """
        user = request.user
        if not user.id:
            return self.exception_response(request, exceptions.S3AccessDenied(message=_('身份未认证')))

        buckets_qs = Bucket.objects.filter(user=user, type=Bucket.TYPE_S3).all()    # user's own
        serializer = BucketListSerializer(buckets_qs, many=True)

        # xml渲染器
        self.set_renderer(request, CusXMLRenderer(root_tag_name='ListAllMyBucketsResult', item_tag_name='Bucket'))
        return Response(data={
            'Buckets': serializer.data,
            'Owner': {'DisplayName': user.username, 'ID': user.id}
        }, status=status.HTTP_200_OK)
