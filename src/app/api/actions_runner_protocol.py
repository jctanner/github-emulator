"""Serialization helpers for the upstream Actions runner protocol."""

import base64
import json
import uuid

from app.config import settings
from app.models.actions import Runner, WorkflowJob
from app.services.job_token_service import issue_job_token

def _runner_client_id(runner: Runner) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"github-emulator-runner-{runner.id}"))


def _workflow_guid(kind: str, local_id: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"github-emulator-actions-{kind}-{local_id}"))


def _template_mapping(values: dict[str, str]) -> dict:
    def template_value(value):
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("${{") and stripped.endswith("}}"):
                return {"type": 3, "expr": stripped[3:-2].strip()}
        return value

    return {
        "type": 2,
        "map": [
            {
                "Key": key,
                "Value": template_value(value),
            }
            for key, value in values.items()
            if value is not None
        ],
    }

def _context_value(value):
    if isinstance(value, dict):
        return _context_dictionary(value)
    if isinstance(value, list):
        return {"t": 1, "a": [_context_value(item) for item in value]}
    return value


def _context_dictionary(values: dict) -> dict:
    """Serialize values as PipelineContextData DictionaryContextData."""
    return {
        "t": 2,
        "d": [
            {
                "k": key,
                "v": _context_value(value),
            }
            for key, value in values.items()
            if value is not None
        ],
    }


def _variable(value: str, is_secret: bool = False) -> dict:
    return {
        "Value": value,
        "IsSecret": is_secret,
    }


def _base64url_json(value: dict) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


def _job_access_token(job: WorkflowJob) -> str:
    return issue_job_token(job)


def _agent_json(
    runner: Runner,
    token: str | None = None,
    base: str | None = None,
) -> dict:
    base = base or settings.BASE_URL
    labels = [
        {
            "id": index,
            "Id": index,
            "name": label,
            "Name": label,
            "type": "user",
            "Type": "user",
        }
        for index, label in enumerate((runner.labels or []), start=1)
    ]
    payload = {
        "id": runner.id,
        "Id": runner.id,
        "name": runner.name,
        "Name": runner.name,
        "osDescription": runner.os,
        "OSDescription": runner.os,
        "enabled": True,
        "Enabled": True,
        "status": runner.status,
        "Status": runner.status,
        "maxParallelism": 1,
        "MaxParallelism": 1,
        "version": "github-emulator",
        "Version": "github-emulator",
        "ephemeral": False,
        "Ephemeral": False,
        "createdOn": runner.created_at.isoformat() if runner.created_at else None,
        "CreatedOn": runner.created_at.isoformat() if runner.created_at else None,
        "labels": labels,
        "Labels": labels,
        "serverUrl": base,
        "gitServerUrl": base,
        "pipelinesUrl": f"{base}/_services/pipelines",
        "actionsServiceUrl": f"{base}/_apis/distributedtask",
        "authorization": {
            "clientId": _runner_client_id(runner),
            "ClientId": _runner_client_id(runner),
            "authorizationUrl": f"{base}/_apis/oauth2/token?runner_id={runner.id}",
            "AuthorizationUrl": f"{base}/_apis/oauth2/token?runner_id={runner.id}",
        },
        "Authorization": {
            "clientId": _runner_client_id(runner),
            "ClientId": _runner_client_id(runner),
            "authorizationUrl": f"{base}/_apis/oauth2/token?runner_id={runner.id}",
            "AuthorizationUrl": f"{base}/_apis/oauth2/token?runner_id={runner.id}",
        },
        "properties": {},
        "Properties": {},
    }
    if token:
        payload["token"] = token
        payload["authorization"] = {
            "scheme": "Bearer",
            "parameters": {"AccessToken": token},
        }
    return payload


EMULATOR_INSTANCE_ID = "11111111-1111-1111-1111-111111111111"
EMULATOR_USER_ID = "22222222-2222-2222-2222-222222222222"
TASK_AREA_ID = "a85b8835-c1a1-4aac-ae97-1c3d0ba72dbd"
_SERVICE_OWNER = EMULATOR_INSTANCE_ID


def _service_definition(identifier: str, name: str, relative_path: str) -> dict:
    location_mapping = {
        "accessMappingMoniker": "PublicAccessMapping",
        "AccessMappingMoniker": "PublicAccessMapping",
        "location": relative_path,
        "Location": relative_path,
    }
    return {
        "serviceType": "distributedtask",
        "ServiceType": "distributedtask",
        "identifier": identifier,
        "Identifier": identifier,
        "displayName": name,
        "DisplayName": name,
        "relativePath": relative_path,
        "RelativePath": relative_path,
        "relativeToSetting": "Context",
        "RelativeToSetting": 0,
        "description": name,
        "Description": name,
        "serviceOwner": TASK_AREA_ID,
        "ServiceOwner": TASK_AREA_ID,
        "locationMappings": [location_mapping],
        "LocationMappings": [location_mapping],
        "toolId": "GitHub",
        "ToolId": "GitHub",
        "status": "Active",
        "Status": "Active",
        "resourceVersion": 5,
        "ResourceVersion": 5,
        "MinVersion": "1.0",
        "MaxVersion": "7.2",
    }
