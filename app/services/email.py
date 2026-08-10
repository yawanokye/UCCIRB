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
                            secure_url: str, due_at, link_expires_at, message: str = '',
                            assigning_office: str = 'Institutional Review Board Secretariat') -> str:
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
      <p>Regards,<br>{escape(assigning_office)}<br>University of Cape Coast</p>
    </div></body></html>'''
    send_gmail_html(reviewer_email, f'UCC {review_label} Assignment', html)
    return html


def college_revision_request_email(*, applicant_name: str, applicant_email: str, reference_no: str,
                                   research_title: str, college_name: str, decision: str,
                                   comments: str, application_url: str) -> str:
    comments_html = f'<div style="margin:18px 0;padding:15px;background:#fff7dc;border-left:4px solid #c69b2d"><strong>Committee comments</strong><br>{escape(comments)}</div>' if comments else ''
    html = f'''<!doctype html><html><body style="font-family:Arial,sans-serif;color:#182431;line-height:1.55">
    <div style="max-width:680px;margin:auto;padding:24px">
      <div style="font-size:12px;text-transform:uppercase;color:#c69b2d;font-weight:bold">University of Cape Coast</div>
      <h2 style="color:#082b4c">Scientific Review Revision Required</h2>
      <p>Dear {escape(applicant_name)},</p>
      <p>The {escape(college_name)} Scientific Committee has reviewed your ethical-clearance application <strong>{escape(reference_no)}</strong>, “{escape(research_title)}”.</p>
      <p><strong>Decision:</strong> {escape(decision)}</p>
      {comments_html}
      <p>Please upload the revised documents and a response to the College review through your applicant portal. Your revised submission will go directly back to the College Scientific Committee.</p>
      <p><a href="{escape(application_url)}" style="display:inline-block;background:#082b4c;color:#fff;text-decoration:none;padding:12px 18px;border-radius:7px;font-weight:bold">Open Application</a></p>
      <p>Regards,<br>{escape(college_name)} Scientific Committee<br>University of Cape Coast</p>
    </div></body></html>'''
    send_gmail_html(applicant_email, f'UCC Scientific Review Revision Required · {reference_no}', html)
    return html


def college_revision_submitted_email(*, officer_name: str, officer_email: str, applicant_name: str,
                                     reference_no: str, research_title: str, college_name: str,
                                     application_url: str) -> str:
    html = f'''<!doctype html><html><body style="font-family:Arial,sans-serif;color:#182431;line-height:1.55">
    <div style="max-width:680px;margin:auto;padding:24px">
      <div style="font-size:12px;text-transform:uppercase;color:#c69b2d;font-weight:bold">University of Cape Coast</div>
      <h2 style="color:#082b4c">Revised Scientific Review Submission Received</h2>
      <p>Dear {escape(officer_name)},</p>
      <p><strong>{escape(applicant_name)}</strong> has submitted revised documents for application <strong>{escape(reference_no)}</strong>, “{escape(research_title)}”.</p>
      <p>The revision has been routed directly to the <strong>{escape(college_name)} Scientific Committee</strong> and is ready for further scientific review.</p>
      <p><a href="{escape(application_url)}" style="display:inline-block;background:#082b4c;color:#fff;text-decoration:none;padding:12px 18px;border-radius:7px;font-weight:bold">Open Revised Application</a></p>
      <p>Regards,<br>UCC Ethical Clearance Portal</p>
    </div></body></html>'''
    send_gmail_html(officer_email, f'UCC Revised Scientific Review Submission · {reference_no}', html)
    return html
