"""Close an owner-authored request after its verified Pages publish succeeds.

The workflow gates this post-deployment step on successful request processing and
verified publication. This script authorizes the original Issue event again; it
never discovers requests, comments on Issues, or changes registration Issues.
"""

import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_interests import REPOSITORY, request_identity

TIMEOUT = 20
MAX_RESPONSE_BYTES = 2_000_000


class CompletionError(Exception):
    """Only fixed safe error codes, never HTTP bodies or credential text."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CompletionError('invalid_response')
        result[key] = value
    return result


def request_once(url, token, body, timeout):
    """Send one PATCH with bounded JSON response and no redirects or retries."""
    request = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), method='PATCH',
                                     headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                                              'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28',
                                              'User-Agent': 'weekly-trend-interest-request-completion/1.0'})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise CompletionError('response_too_large')
            return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
    except CompletionError:
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        raise CompletionError('request_failed') from None
    except (ValueError, UnicodeError):
        raise CompletionError('invalid_response') from None


def complete(environ=None, transport=None):
    """Idempotently close the one validated request from an ``issues`` event.

    An injected ``transport(url, token, body, timeout)`` returns a decoded GitHub
    Issue object. It is not called for dispatches, untrusted events, or a missing
    token. Only the issue number, closed state and exact public URL are retained.
    Deployment verification is the caller workflow's prerequisite.
    """
    env = os.environ if environ is None else environ
    if env.get('GITHUB_EVENT_NAME') != 'issues':
        raise CompletionError('invalid_request_event')
    try:
        identity, public_url = request_identity(env)
    except Exception:
        raise CompletionError('invalid_request_event') from None
    if not identity.startswith('issue-') or public_url is None:
        raise CompletionError('invalid_request_event')
    token = env.get('GITHUB_TOKEN', '')
    if not isinstance(token, str) or not token.strip():
        raise CompletionError('missing_token')
    number = int(identity.removeprefix('issue-'))
    url = f'https://api.github.com/repos/{REPOSITORY}/issues/{number}'
    try:
        result = (transport or request_once)(url, token.strip(), {'state': 'closed'}, TIMEOUT)
        if (not isinstance(result, dict) or type(result.get('number')) is not int or result['number'] != number
                or result.get('state') != 'closed' or result.get('html_url') != public_url
                or 'pull_request' in result):
            raise CompletionError('invalid_response')
    except CompletionError:
        raise
    except Exception:
        # Unexpected transport exceptions may contain response bodies or tokens.
        raise CompletionError('request_failed') from None
    return {'ok': True, 'status': 'closed', 'issue_number': number}


def main():
    try:
        result = complete()
    except Exception:
        print(json.dumps({'ok': False, 'error_code': 'request_completion_failed'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
