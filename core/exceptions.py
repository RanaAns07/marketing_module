from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response


class ApplicationError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "A domain error occurred."
    default_code = "application_error"

    def __init__(self, detail=None, code=None, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail=detail, code=code)


class TenantContextRequired(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Active tenant context is required."
    default_code = "tenant_context_required"


class CrossTenantAccessError(ApplicationError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Cross-tenant resource access is forbidden."
    default_code = "cross_tenant_access_denied"


def custom_exception_handler(exc, context):
    from rest_framework.views import exception_handler

    if isinstance(exc, DjangoValidationError):
        details = exc.message_dict if hasattr(exc, "message_dict") else {"non_field_errors": exc.messages}
        return Response(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Validation failed.",
                    "details": details,
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, IntegrityError):
        return Response(
            {
                "error": {
                    "code": "conflict",
                    "message": "Database integrity constraint violated.",
                    "details": {},
                }
            },
            status=status.HTTP_409_CONFLICT,
        )

    response = exception_handler(exc, context)
    if response is None:
        return None

    default_code = getattr(exc, "default_code", "error")
    data = response.data
    message = "Request failed."
    details = {}

    if isinstance(data, dict):
        if "detail" in data and len(data) == 1:
            detail_val = data["detail"]
            message = str(detail_val)
            code = getattr(detail_val, "code", default_code)
        else:
            details = data
            code = default_code
            first_val = next(iter(data.values()), None)
            if isinstance(first_val, list) and first_val:
                message = str(first_val[0])
            elif first_val is not None:
                message = str(first_val)
    elif isinstance(data, list):
        details = {"errors": data}
        code = default_code
        if data:
            message = str(data[0])
    else:
        message = str(data)
        code = default_code

    response.data = {
        "error": {
            "code": str(code),
            "message": message,
            "details": details,
        }
    }
    return response
