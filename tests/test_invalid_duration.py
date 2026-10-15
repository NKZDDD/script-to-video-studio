import pytest
from core import apiutil as A


@pytest.mark.parametrize('status', [0, 200, 400, 500])
def test_duration_code_is_task_fatal(status):
    assert A.classify(status, 'please retry', 'invalid_duration') == A.TASK_FATAL


def test_polled_duration_error_keeps_parameter_classification():
    error = A.task_failed({'status': 'failed', 'error': {
        'code': 'invalid_duration', 'message': '视频时长必须在 4～30 秒之间，请调整时长后重试。'}})
    assert error.err_code == 'invalid_duration'
    assert error.kind == A.TASK_FATAL
    assert A.classify(0, 'network timeout') == A.RETRYABLE
