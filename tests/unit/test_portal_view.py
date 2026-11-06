from datetime import UTC, datetime, timedelta

from automation.portal_view import csp, render


def test_read_only_and_contextual_controls_with_escaped_untrusted_metadata(release):
    now=datetime.now(UTC)
    context={'rcl_id':'RCL000001','change_id':9,'phase':'HUMAN_REVIEW','application_version':'1.0.1',
             'current_version':'1.0.0','criticality':'NON_CRITICAL','owner':'<script>bad()</script>'}
    page=render(release,context,'test-csrf',now=now)
    assert 'RCL000001 — Orders Worker' in page and '>Aprovar<' in page
    assert '<script>bad()' not in page and '&lt;script&gt;bad()' in page
    assert 'Bedrock' not in page and '<html lang="pt-BR">' in page
    assert "'unsafe-inline'" not in csp() and "form-action 'self'" in csp()
    context.update(phase='CLOSED_SUCCESS')
    assert '>Aprovar<' not in render(release,context,'test-csrf',now=now)
    context.update(phase='OBSERVING',rollback_eligible=True,rollback_until=(now+timedelta(hours=12)).isoformat())
    assert 'Solicitar rollback' in render(release,context,'test-csrf',now=now)
    assert 'Solicitar rollback' not in render(release,context,'test-csrf',now=now,rollback_pending=True)
    context['rollback_until']=(now-timedelta(seconds=1)).isoformat()
    assert 'Solicitar rollback' not in render(release,context,'test-csrf',now=now)
