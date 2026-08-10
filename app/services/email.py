from __future__ import annotations

import base64
from email.message import EmailMessage
from html import escape

import httpx

from ..config import GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, GMAIL_SENDER_EMAIL


def gmail_configured() -> bool:
    return bool(GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN and GMAIL_SENDER_EMAIL)


def _gmail_access_token() -> str:
    if not gmail_configured():
        raise RuntimeError('Gmail API is not configured.')
    response = httpx.post(
        'https://oauth2.googleapis.com/token',
        data={
            'client_id': GMAIL_CLIENT_ID,
            'client_secret': GMAIL_CLIENT_SECRET,
            'refresh_token': GMAIL_REFRESH_TOKEN,
            'grant_type': 'refresh_token',
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    token = data.get('access_token')
    if not token:
        raise RuntimeError('Google did not return an access token.')
    return token


def send_gmail_html(to: str, subject: str, html: str) -> str:
    token = _gmail_access_token()
    message = EmailMessage()
    message['To'] = to
    message['From'] = GMAIL_SENDER_EMAIL
    message['Subject'] = subject
    message.set_content('This message contains an HTML secure-review invitation. Please open it in an HTML-capable email client.')
    message.add_alternative(html, subtype='html')
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii').rstrip('=')
    response = httpx.post(
        'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json={'raw': raw},
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json().get('id') or '')


def review_assignment_email(*, reviewer_name: str, reviewer_email: str, level: str, count: int,
                            secure_url: str, due_at, link_expires_at, message: str = '') -> str:
    review_label = 'College Scientific Review' if level == 'college' else 'IRB Ethical Review'
    optional_message = f'<p><strong>Message from the assigning office:</strong><br>{escape(message)}</p>' if message else ''
    due_text = due_at.strftime('%d %B %Y, %H:%M UTC')
    expiry_text = link_expires_at.strftime('%d %B %Y, %H:%M UTC')
    html = f'''<!doctype html><html><body style="font-family:Arial,sans-serif;color:#182431;line-height:1.55">
    <div style="max-width:680px;margin:auto;padding:24px">
      <div style="font-size:12px;text-transform:uppercase;color:#c69b2d;font-weight:bold">University of Cape Coast</div>
      <h2 style="color:#082b4c">{review_label} Assignment</h2>
      <p>Dear {escape(reviewer_name)},</p>
      <p>You have been assigned <strong>{count}</strong> ethical-clearance application{'s' if count != 1 else ''} for {review_label.lower()}.</p>
      {optional_message}
      <p><a href="{escape(secure_url)}" style="display:inline-block;background:#082b4c;color:#fff;text-decoration:none;padding:12px 18px;border-radius:7px;font-weight:bold">Open Secure Review Workspace</a></p>
      <p>This single secure link contains all applications assigned in this batch. Applicant details are already bound to the assignment. For each application, first complete the conflict-of-interest declaration, then download the application package and submit that application's review report separately.</p>
      <div style="margin:20px 0;padding:16px;background:#fff7dc;border:1px solid #ead58c;border-radius:8px">
        <strong>Review timeline</strong><br>Review due: <strong>{escape(due_text)}</strong><br>Secure link expires: <strong>{escape(expiry_text)}</strong>
      </div>
      <p>Please do not forward the secure link.</p>
      <p>Regards,<br>Institutional Review Board Secretariat<br>University of Cape Coast</p>
    </div></body></html>'''
    send_gmail_html(reviewer_email, f'UCC {review_label} Assignment', html)
    return html
