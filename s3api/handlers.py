import time

from django.utils import timezone
from django.utils.translation import gettext
from django.conf import settings
from rest_framework.response import Response

from utils.md5 import FileMD5Handler, S3ObjectMultipartETagHandler
from utils.oss.pyrados import HarborObject, ObjectPart
from .managers import ObjectPartManager
from .responses import IterResponse
from . import exceptions
from .harbor import HarborManager
from . import renders
from . import paginations
from . import serializers


MULTIPART_UPLOAD_MAX_SIZE = getattr(settings, 'S3_MULTIPART_UPLOAD_MAX_SIZE', 2 * 1024 ** 3)        # default 2GB
MULTIPART_UPLOAD_MIN_SIZE = getattr(settings, 'S3_MULTIPART_UPLOAD_MIN_SIZE', 5 * 1024 ** 2)        # default 5MB


def exception_response(request, exc):
    """
    异常回复

    :param request:
    :param exc: S3Error()
    :return: Response()
    """
    renderer = renders.CommonXMLRenderer(root_tag_name='Error')
    request.accepted_renderer = renderer
    request.accepted_media_type = renderer.media_type
    return Response(data=exc.err_data(), status=exc.status_code)


class MultipartUploadHandler:
    def abort_multipart_upload(self, request, upload, bucket):
        """
        :param request: 请求体对象实例
        :param upload: 多部分上传对象实例
        :param bucket: 存储桶对象实例
        :return:
            Response()
        """
        if upload.is_composing():             # 已经正在组合对象
            return exception_response(request, exceptions.S3CompleteMultipartAlreadyInProgress())

        if upload.is_completed():          # 已完成的上传任务，删除任务记录
            upload.safe_delete()

            return exception_response(request, exceptions.S3NoSuchUpload())

        opm = ObjectPartManager(bucket=bucket)
        upload_parts_qs = opm.get_parts_queryset_by_upload_id_obj_id(upload_id=upload.id, obj_id=0)
        upload_parts = list(upload_parts_qs)
        for failed_parts in self.clear_parts_cache_iter(parts=upload_parts, is_rm_metadata=True):
            if failed_parts is None:
                continue
            elif failed_parts:
                all_len = len(upload_parts)
                failed_len = len(failed_parts)
                if failed_len > (all_len // 2):      # 如果大多数part删除失败，就直接返回500内部错误，让客户端重新请求
                    return exception_response(request, exceptions.S3InternalError())

                # 小部分删除失败，重试清除删除失败的part
                for failed_parts_2 in self.clear_parts_cache_iter(parts=failed_parts, is_rm_metadata=True):
                    if failed_parts_2 is None:
                        continue
                    elif not failed_parts_2:
                        return exception_response(request, exceptions.S3InternalError())
            else:
                break

        if upload.safe_delete():
            return Response(status=204)

        return exception_response(request, exceptions.S3InternalError())

    def complete_multipart_upload_handle(self, request, bucket, upload, key: str, complete_parts, complete_numbers):
        """
        完成多部分上传处理

        :param request:
        :param bucket:
        :param upload: 多部分上传任务实例
        :param key: 对象key, 全路径
        :param complete_parts: 请求要组合的part信息字典
        :param complete_numbers: 请求要组合的所有part的PartNumber list
        :return:
            Response()

        :raises: S3Error
        """
        if upload.is_completed():  # 已完成的上传任务，删除任务记录
            upload.safe_delete()
            return exception_response(request, exceptions.S3NoSuchUpload())

        if upload.is_composing():  # 已经正在组合对象，不能重复组合
            return exception_response(request, exceptions.S3CompleteMultipartAlreadyInProgress())

        if not upload.set_composing():  # 设置正在组合对象
            return exception_response(request, exceptions.S3InternalError())

        hm = HarborManager()
        obj, created = hm.get_or_create_obj(table_name=bucket.get_bucket_table_name(), obj_path_name=key)

        obj_raods_key = obj.get_obj_key(bucket.id)
        obj_rados = HarborObject(pool_name=bucket.pool_name, obj_id=obj_raods_key, obj_size=obj.si)
        if not created and obj.si != 0:  # 已存在的非空对象
            try:
                hm._pre_reset_upload(bucket=bucket, obj=obj, rados=obj_rados)  # 重置对象大小
            except Exception as exc:
                return exception_response(request, exceptions.S3InvalidRequest(f'reset object error, {str(exc)}'))

        # 获取需要组合的所有part元数据和对象ETag，和没有用到的part元数据列表
        used_upload_parts, unused_upload_parts, obj_etag = self.get_upload_parts_and_validate(
            bucket=bucket, upload=upload, complete_parts=complete_parts, complete_numbers=complete_numbers)

        return IterResponse(iter_content=self.complete_iter(
            request=request, bucket=bucket, upload=upload, obj=obj, obj_rados=obj_rados,
            obj_etag=obj_etag, complete_numbers=complete_numbers,
            used_upload_parts=used_upload_parts, unused_upload_parts=unused_upload_parts))

    @staticmethod
    def clear_parts_cache_iter(parts, is_rm_metadata=False):
        """
        清理part缓存，part rados数据或元数据

        :param parts: part元数据实例list或dict
        :param is_rm_metadata: True(删除元数据)；False(不删元数据)
        :return:
            None                # 未结束
            [part]              # 删除失败的part元数据list
        """
        if isinstance(parts, dict):
            parts = parts.values()

        start_time = time.time()
        remove_failed_parts = []  # 删除元数据失败的part
        part_rados = ObjectPart(part_key='', part_size=0)
        for p in parts:
            if is_rm_metadata:
                if not p.safe_delete():
                    if not p.safe_delete():  # 重试一次
                        remove_failed_parts.append(p)

            part_rados.reset_part_key_and_size(part_key=p.get_part_rados_key(), part_size=p.size)
            ok, _ = part_rados.delete()
            if not ok:
                part_rados.delete()  # 重试一次

            # 间隔不断发送空字符防止客户端连接超时
            now_time = time.time()
            if now_time - start_time < 10:
                start_time = now_time
                continue

            yield None

        yield remove_failed_parts

    def complete_iter(self, request, bucket, upload, obj, obj_rados, obj_etag, complete_numbers, used_upload_parts,
                      unused_upload_parts):
        white_space_bytes = b' '
        xml_declaration_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n'
        start_time = time.time()
        yielded_doctype = False
        try:
            # 所有part rados数据组合对象rados
            md5_handler = FileMD5Handler()
            offset = 0
            parts_count = len(complete_numbers)

            part_rados = ObjectPart(part_key='')
            for num in complete_numbers:
                part = used_upload_parts[num]
                for r in self.save_part_to_object_iter(obj=obj, obj_rados=obj_rados, part_rados=part_rados,
                                                       offset=offset, part=part, md5_handler=md5_handler,
                                                       obj_etag=obj_etag, parts_count=parts_count):
                    if r is None:
                        if not yielded_doctype:
                            yielded_doctype = True
                            yield xml_declaration_bytes
                        else:
                            yield white_space_bytes
                    elif r is True:
                        break
                    elif isinstance(r, exceptions.S3Error):
                        raise r

                offset = offset + part.size

                # 间隔不断发送空字符防止客户端连接超时
                now_time = time.time()
                if now_time - start_time < 10:
                    start_time = now_time
                    continue
                if not yielded_doctype:
                    yielded_doctype = True
                    yield xml_declaration_bytes
                else:
                    yield white_space_bytes

            # 更新对象元数据
            if not self.update_obj_metedata(obj=obj, size=offset, hex_md5=md5_handler.hex_md5,
                                            share_code=upload.obj_perms_code):
                raise exceptions.S3InternalError(extend_msg='update object metadata error.')

            # 多部分上传已完成，清理数据
            # 删除无用的part元数据和rados数据
            for r in self.clear_parts_cache_iter(unused_upload_parts, is_rm_metadata=True):
                if r is None:
                    if not yielded_doctype:
                        yielded_doctype = True
                        yield xml_declaration_bytes
                    else:
                        yield white_space_bytes

            # 删除已组合的rados数据, 保留part元数据
            for r in self.clear_parts_cache_iter(used_upload_parts, is_rm_metadata=False):
                if r is None:
                    if not yielded_doctype:
                        yielded_doctype = True
                        yield xml_declaration_bytes
                    else:
                        yield white_space_bytes

            # 删除多部分上传upload任务
            if not upload.safe_delete():
                if not upload.safe_delete():
                    upload.set_completed()  # 删除失败，尝试标记已上传完成

            location = request.build_absolute_uri()
            data = {'Location': location, 'Bucket': bucket.name, 'Key': obj.na, 'ETag': obj_etag}
            content = renders.CommonXMLRenderer(root_tag_name='CompleteMultipartUploadResult',
                                                with_xml_declaration=not yielded_doctype).render(data)
            yield content.encode(encoding='utf-8')          # 合并完成

        except exceptions.S3Error as e:
            upload.set_uploading()  # 发生错误，设置回正在上传
            content = renders.CommonXMLRenderer(root_tag_name='Error',
                                                with_xml_declaration=not yielded_doctype).render(e.err_data())
            yield content.encode(encoding='utf-8')
        except Exception as e:
            upload.set_uploading()  # 发生错误，设置回正在上传
            content = renders.CommonXMLRenderer(root_tag_name='Error', with_xml_declaration=not yielded_doctype
                                                ).render(exceptions.S3InternalError().err_data())
            yield content.encode(encoding='utf-8')

    @staticmethod
    def update_obj_metedata(obj, size, hex_md5: str, share_code):
        """
        :return:
            True
            False
        """
        obj.si = size
        obj.md5 = hex_md5
        obj.upt = timezone.now()
        obj.share = share_code
        obj.stl = False  # 没有共享时间限制
        try:
            obj.save(update_fields=['si', 'md5', 'upt', 'stl', 'share'])
        except Exception as e:
            return False

        return True

    @staticmethod
    def save_part_to_object_iter(obj, obj_rados, part_rados, offset, part, md5_handler, obj_etag: str, parts_count: int):
        """
        把一个part数据写入对象

        :param obj: 对象元数据实例
        :param obj_rados: 对象rados实例
        :param part_rados: 块rados实例
        :param offset: part数据写入对象的偏移量
        :param part: part元数据实例
        :param md5_handler: 对象md5计算
        :param obj_etag: 对象的ETag
        :param parts_count: 对象part总数
        :return:
            yield True          # success
            yield None          # continue
            yield S3Error       # error
        """
        part.obj_offset = offset
        part.obj_etag = obj_etag
        part.obj_id = obj.id
        part.parts_count = parts_count

        start_time = time.time()
        part_rados.reset_part_key_and_size(part_key=part.get_part_rados_key(), part_size=part.size)
        generator = part_rados.read_obj_generator()
        for data in generator:
            if not data:
                break

            ok, msg = obj_rados.write(offset=offset, data_block=data)
            if not ok:
                ok, msg = obj_rados.write(offset=offset, data_block=data)

            if not ok:
                yield exceptions.S3InternalError(extend_msg=msg)

            md5_handler.update(offset=offset, data=data)
            offset = offset + len(data)

            now_time = time.time()
            if now_time - start_time < 10:
                start_time = now_time
                continue

            yield None

        try:
            part.save(update_fields=['obj_offset', 'obj_etag', 'obj_id', 'parts_count'])
        except Exception as e:
            yield exceptions.S3InternalError()

        yield True

    @staticmethod
    def get_upload_parts_and_validate(bucket, upload, complete_parts, complete_numbers):
        """
        多部分上传part元数据获取和验证

        :param bucket: 桶对象
        :param upload: 上传任务实例
        :param complete_parts:  客户端请求组合提交的part信息，dict
        :param complete_numbers: 客户端请求组合提交的所有part的编号list，升序
        :return:
                (
                    used_upload_parts: dict,        # complete_parts对应的part元数据实例字典
                    unused_upload_parts: list,      # 属于同一个多部分上传任务upload的，但不在complete_parts内的part元数据实例列表
                    object_etag: str                # 对象的ETag
                )
        :raises: S3Error
        """
        opm = ObjectPartManager(bucket=bucket)
        upload_parts_qs = opm.get_parts_queryset_by_upload_id(upload_id=upload.id)

        obj_etag_handler = S3ObjectMultipartETagHandler()
        used_upload_parts = {}
        unused_upload_parts = []
        last_part_number = complete_numbers[-1]
        for part in upload_parts_qs:
            num = part.part_num
            if part.part_num in complete_numbers:
                c_part = complete_parts[num]
                if part.size < MULTIPART_UPLOAD_MIN_SIZE and num != last_part_number:  # part最小限制，最后一个part除外
                    raise exceptions.S3EntityTooSmall()

                if 'ETag' not in c_part:
                    raise exceptions.S3InvalidPart(extend_msg=f'PartNumber={num}')
                if c_part["ETag"].strip('"') != part.part_md5:
                    raise exceptions.S3InvalidPart(extend_msg=f'PartNumber={num}')

                obj_etag_handler.update(part.part_md5)
                used_upload_parts[num] = part
            else:
                unused_upload_parts.append(part)

        obj_parts_count = len(used_upload_parts)
        if obj_parts_count != len(complete_parts):
            raise exceptions.S3InvalidPart()

        obj_etag = f'"{obj_etag_handler.hex_md5}-{obj_parts_count}"'
        return used_upload_parts, unused_upload_parts, obj_etag


class ListObjectsHandler:
    def list_objects(self, request, view):
        delimiter = request.query_params.get('delimiter', None)
        prefix = request.query_params.get('prefix', '')
        bucket_name = view.get_bucket_name(request)

        if not delimiter:  # list所有对象和目录
            return self.list_objects_v1_list_prefix(view=view, request=request, prefix=prefix, bucket_name=bucket_name)

        if delimiter != '/':
            return exception_response(request, exceptions.S3InvalidArgument(message=gettext('参数“delimiter”必须是“/”')))

        path = prefix.strip('/')
        if prefix and not path:  # prefix invalid, return no match data
            return self.list_objects_v1_no_match(view=view, request=request, prefix=prefix, delimiter=delimiter,
                                                 bucket_name=bucket_name)

        hm = HarborManager()
        try:
            bucket, obj = hm.get_bucket_and_obj_or_dir(bucket_name=bucket_name, path=path, user=request.user)
        except exceptions.S3Error as e:
            return exception_response(request, e)

        if obj is None:
            return self.list_objects_v1_no_match(view=view, request=request, prefix=prefix, delimiter=delimiter,
                                                 bucket_name=bucket_name)

        paginator = paginations.ListObjectsV1CursorPagination()
        max_keys = paginator.get_page_size(request=request)
        ret_data = {
            'IsTruncated': 'false',  # can not use bool
            'Name': bucket_name,
            'Prefix': prefix,
            'EncodingType': 'url',
            'MaxKeys': max_keys,
            'Delimiter': delimiter
        }

        if prefix == '' or prefix.endswith('/'):  # list dir
            if not obj.is_dir():
                return self.list_objects_v1_no_match(view=view, request=request, prefix=prefix, delimiter=delimiter,
                                                     bucket_name=bucket_name)

            objs_qs = hm.list_dir_queryset(bucket=bucket, dir_obj=obj)
            paginator.paginate_queryset(objs_qs, request=request)
            objs, _ = paginator.get_objects_and_dirs()

            serializer = serializers.ObjectListWithOwnerSerializer(objs, many=True, context={'user': request.user})
            data = paginator.get_paginated_data(common_prefixes=True, delimiter=delimiter)
            ret_data.update(data)
            ret_data['Contents'] = serializer.data
            view.set_renderer(request, renders.ListObjectsV1XMLRenderer())
            return Response(data=ret_data, status=200)

        # list object metadata
        if not obj.is_file():
            return self.list_objects_v1_no_match(view=view, request=request, prefix=prefix, delimiter=delimiter,
                                                 bucket_name=bucket_name)

        serializer = serializers.ObjectListWithOwnerSerializer(obj, context={'user': request.user})

        ret_data['Contents'] = [serializer.data]
        ret_data['KeyCount'] = 1
        view.set_renderer(request, renders.ListObjectsV1XMLRenderer())
        return Response(data=ret_data, status=200)

    @staticmethod
    def list_objects_v1_list_prefix(view, request, prefix, bucket_name):
        """
        列举所有对象和目录
        """
        hm = HarborManager()
        try:
            bucket, objs_qs = hm.get_bucket_objects_dirs_queryset(bucket_name=bucket_name, user=request.user,
                                                                  prefix=prefix)
        except exceptions.S3Error as e:
            return view.exception_response(request, e)

        paginator = paginations.ListObjectsV1CursorPagination()
        objs_dirs = paginator.paginate_queryset(objs_qs, request=request)
        serializer = serializers.ObjectListWithOwnerSerializer(objs_dirs, many=True, context={'user': request.user})

        data = paginator.get_paginated_data(delimiter='')
        data['Contents'] = serializer.data
        data['Name'] = bucket_name
        data['Prefix'] = prefix
        data['EncodingType'] = 'url'

        view.set_renderer(request, renders.ListObjectsV1XMLRenderer())
        return Response(data=data, status=200)

    @staticmethod
    def list_objects_v1_no_match(view, request, prefix, delimiter, bucket_name):
        paginator = paginations.ListObjectsV1CursorPagination()
        max_keys = paginator.get_page_size(request=request)
        ret_data = {
            'IsTruncated': 'false',     # can not use bool True, need use string
            'Name': bucket_name,
            'Prefix': prefix,
            'EncodingType': 'url',
            'MaxKeys': max_keys,
            'KeyCount': 0
        }
        if delimiter:
            ret_data['Delimiter'] = delimiter

        view.set_renderer(request, renders.ListObjectsV1XMLRenderer())
        return Response(data=ret_data, status=200)
