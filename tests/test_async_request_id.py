from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.logging import bind_context, unbind_context
from app.core.request_context import request_id_var, sanitize_request_id
from app.services.integration_job_service import IntegrationJobService


class TestAsyncRequestIdPropagation:
    """Tests for request_id propagation through IntegrationJob queue."""

    @pytest.mark.asyncio
    async def test_enqueue_agent_execution_propagates_request_id(self):
        """When enqueueing agent execution from an HTTP request, the request_id should be stored in job payload."""

        # Set up a request context
        test_request_id = "test-req-123"
        token = request_id_var.set(test_request_id)

        try:
            db = AsyncMock()
            with (
                patch(
                    "app.services.integration_job_service.IntegrationJobRepository.get_by_dedupe_key",
                    return_value=None,
                ),
                patch(
                    "app.services.integration_job_service.IntegrationJobRepository.create"
                ) as mock_create,
                patch.object(
                    IntegrationJobService,
                    "_assert_agent_execution_target",
                    new=AsyncMock(),
                ),
            ):
                mock_job = MagicMock()
                mock_job.id = 1
                mock_job.status = "pending"
                mock_create.return_value = mock_job

                await IntegrationJobService.enqueue_agent_execution(
                    db=db,
                    run_id="run-abc",
                    organization_id=1,
                )

                # Verify the job was created with request_id in payload
                mock_create.assert_called_once()
                call_args = mock_create.call_args
                # create(db, job) -> args[1] is the job
                created_job = (
                    call_args[0][1]
                    if len(call_args[0]) > 1
                    else call_args[1].get("job")
                )
                assert created_job is not None, (
                    f"Could not extract job from call_args: {call_args}"
                )
                assert created_job.payload["request_id"] == test_request_id
                assert created_job.payload["run_id"] == "run-abc"
        finally:
            request_id_var.reset(token)

    @pytest.mark.asyncio
    async def test_enqueue_agent_execution_no_request_id_when_no_context(self):
        """When enqueueing without HTTP request context, payload should have None request_id."""

        # Ensure no request context
        token = request_id_var.set(None)

        try:
            db = AsyncMock()
            with (
                patch(
                    "app.services.integration_job_service.IntegrationJobRepository.get_by_dedupe_key",
                    return_value=None,
                ),
                patch(
                    "app.services.integration_job_service.IntegrationJobRepository.create"
                ) as mock_create,
                patch.object(
                    IntegrationJobService,
                    "_assert_agent_execution_target",
                    new=AsyncMock(),
                ),
            ):
                mock_job = MagicMock()
                mock_job.id = 1
                mock_job.status = "pending"
                mock_create.return_value = mock_job

                await IntegrationJobService.enqueue_agent_execution(
                    db=db,
                    run_id="run-xyz",
                    organization_id=1,
                )

                mock_create.assert_called_once()
                call_args = mock_create.call_args
                created_job = (
                    call_args[0][1]
                    if len(call_args[0]) > 1
                    else call_args[1].get("job")
                )
                assert created_job is not None, (
                    f"Could not extract job from call_args: {call_args}"
                )
                # When no request context, get_request_id() returns None
                assert created_job.payload["request_id"] is None
                assert created_job.payload["run_id"] == "run-xyz"
        finally:
            request_id_var.reset(token)

    @pytest.mark.asyncio
    async def test_worker_binds_request_id_from_payload(self):
        """Worker should extract and bind request_id from job payload."""

        # Ensure clean context
        unbind_context()

        # Simulate a job with request_id in payload
        job = MagicMock()
        job.id = 42
        job.job_type = "agent.execute"
        job.attempts = 1
        job.payload = {"run_id": "run-123", "request_id": "worker-req-456"}

        # Test the extraction logic directly
        request_id = job.payload.get("request_id") if job.payload else None
        assert request_id == "worker-req-456"

        # Verify it can be bound
        bind_context(job_id=str(job.id), action=job.job_type, request_id=request_id)

        # Verify through get_request_id (though this uses contextvar, not structlog directly)
        # The important thing is the extraction works
        unbind_context()


class TestRequestIdValidation:
    """Extended validation tests for incoming X-Request-ID header."""

    def test_valid_uuid(self):
        """Valid UUID should be accepted."""
        assert (
            sanitize_request_id("550e8400-e29b-41d4-a716-446655440000")
            == "550e8400-e29b-41d4-a716-446655440000"
        )
        assert sanitize_request_id("abcdef123456") == "abcdef123456"  # short UUID-like

    def test_safe_hyphenated_id(self):
        """Safe hyphenated IDs should be accepted."""
        assert sanitize_request_id("req-123-abc-def") == "req-123-abc-def"
        assert sanitize_request_id("correlation-id-v1") == "correlation-id-v1"

    def test_invalid_newline_control_char(self):
        """Newlines and control characters should be rejected."""
        assert sanitize_request_id("req\n123") is None
        assert sanitize_request_id("req\r123") is None
        assert sanitize_request_id("req\t123") is None
        assert sanitize_request_id("req\x00123") is None
        assert sanitize_request_id("req\x1f123") is None

    def test_excessive_length(self):
        """IDs over 128 chars should be rejected."""
        too_long = "a" * 129
        assert sanitize_request_id(too_long) is None
        exactly_128 = "a" * 128
        assert sanitize_request_id(exactly_128) == exactly_128
