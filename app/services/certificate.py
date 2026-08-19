from __future__ import annotations
from io import BytesIO
from pathlib import Path

import qrcode
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..config import BASE_DIR, PUBLIC_BASE_URL, STORAGE_DIR
from .routing import is_direct_irb_affiliation

UCC_NAVY = colors.HexColor('#1F2056')
UCC_RED = colors.HexColor('#DF232C')
UCC_GOLD = colors.HexColor('#F2D13D')


def certificate_dir() -> Path:
    path = STORAGE_DIR / 'certificates'
    path.mkdir(parents=True, exist_ok=True)
    return path


def certificate_path(stored_name: str) -> Path:
    return certificate_dir() / stored_name


def _qr_image(token: str) -> Image:
    url = f'{PUBLIC_BASE_URL}/verify/{token}'
    qr = qrcode.QRCode(version=3, box_size=7, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return Image(buf, width=31*mm, height=31*mm)


def generate_certificate_pdf(certificate, application, document_kind: str = 'ethical_clearance') -> str:
    stored_name = f'{certificate.certificate_no.replace("/", "-")}.pdf'
    out = certificate_path(stored_name)
    page_width, page_height = landscape(A4)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=landscape(A4),
        rightMargin=18*mm,
        leftMargin=18*mm,
        topMargin=14*mm,
        bottomMargin=13*mm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle('title', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=22, leading=26, textColor=UCC_NAVY, alignment=TA_CENTER, spaceAfter=8)
    subtitle = ParagraphStyle('subtitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=13, textColor=UCC_RED, alignment=TA_CENTER, spaceAfter=11)
    body = ParagraphStyle('body', parent=styles['BodyText'], fontName='Helvetica', fontSize=10.8, leading=16, alignment=TA_CENTER, textColor=colors.HexColor('#22243d'))
    label = ParagraphStyle('label', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=9.5, leading=13, textColor=UCC_NAVY)
    value = ParagraphStyle('value', parent=styles['BodyText'], fontName='Helvetica', fontSize=9.5, leading=13, textColor=colors.HexColor('#20222f'))
    fine = ParagraphStyle('fine', parent=styles['BodyText'], fontName='Helvetica', fontSize=8.2, leading=11, textColor=colors.HexColor('#5f6576'))

    story = []
    brand_path = BASE_DIR / 'app' / 'static' / 'img' / 'ucc-brand.png'
    if brand_path.exists():
        story.append(Image(str(brand_path), width=118*mm, height=22.4*mm))
        story.append(Spacer(1, 4*mm))

    story.append(Paragraph('INSTITUTIONAL REVIEW BOARD', title))
    if document_kind == 'exemption':
        story.append(Paragraph('EXEMPTION DETERMINATION', subtitle))
        story.append(Paragraph('This document confirms the authorised UCC Institutional Review Board determination that the research protocol stated below meets the applicable criteria for an exempt determination. It is not an Ethical Clearance Certificate for a protocol requiring expedited or Full Board approval.', body))
    elif document_kind == 'conditional_clearance':
        story.append(Paragraph('ETHICS APPROVAL CERTIFICATE', subtitle))
        story.append(Paragraph('<b>APPROVED PENDING IRB BOARD RATIFICATION.</b> Based on the completed College Scientific Committee review and authorised IRB administrative review, this protocol has ethical approval subject to formal ratification by the UCC Institutional Review Board. The Board may ratify, vary the conditions, defer, suspend or revoke this approval.', body))
    else:
        story.append(Paragraph('ETHICS APPROVAL CERTIFICATE', subtitle))
        story.append(Paragraph('This is to certify that the research protocol stated below has completed the University of Cape Coast ethical review process and has received final IRB approval, subject to any conditions recorded in the approval decision.', body))
    story.append(Spacer(1, 7*mm))

    rows = [
        [Paragraph('Determination Number' if document_kind == 'exemption' else 'Ethics Certificate Number', label), Paragraph(certificate.certificate_no, value)],
        [Paragraph('Approval Status', label), Paragraph(('Pending IRB Board Ratification' if certificate.status == 'pending_ratification' else certificate.status.replace('_', ' ').title()), value)],
        [Paragraph('Protocol/Application Number', label), Paragraph(application.reference_no or '—', value)],
        [Paragraph('Researcher', label), Paragraph(application.applicant.full_name, value)],
        [Paragraph('College / UCC Unit', label), Paragraph((application.department or 'Other UCC Academic/Administrative Unit') if is_direct_irb_affiliation(application.college) else application.college.name, value)],
        [Paragraph('Research Title', label), Paragraph(application.title, value)],
        [Paragraph('Approval Date', label), Paragraph(certificate.issue_date.strftime('%d %B %Y'), value)],
        [Paragraph('Expiry Date', label), Paragraph(certificate.expiry_date.strftime('%d %B %Y'), value)],
    ]
    tbl = Table(rows, colWidths=[53*mm, 144*mm], hAlign='CENTER')
    tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f2f3f8')),
        ('BOX', (0,0), (-1,-1), 0.7, colors.HexColor('#ccd0df')),
        ('INNERGRID', (0,0), (-1,-1), 0.35, colors.HexColor('#d9dce8')),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))

    qr = _qr_image(certificate.verification_token)
    verify_text = Paragraph(
        '<b>Online verification</b><br/>Scan the QR code or use the public verification page to confirm this UCC IRB record. Only limited non-sensitive information is displayed.',
        fine,
    )
    lower = Table([[tbl, Table([[qr], [verify_text]], colWidths=[42*mm])]], colWidths=[203*mm, 47*mm])
    lower.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('LEFTPADDING',(0,0),(-1,-1),4), ('RIGHTPADDING',(0,0),(-1,-1),4)]))
    story.append(lower)

    if certificate.conditions:
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph(f'<b>Approval conditions:</b> {certificate.conditions}', fine))

    story.append(Spacer(1, 7*mm))
    footer = Table([
        ['', ''],
        ['________________________________', '________________________________'],
        ['IRB Chairperson / Authorised Signatory', 'Date'],
    ], colWidths=[95*mm, 95*mm], hAlign='CENTER')
    footer.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('TEXTCOLOR', (0,0), (-1,-1), UCC_NAVY),
        ('FONTNAME', (0,2), (-1,2), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
    ]))
    story.append(footer)
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('University of Cape Coast • Institutional Review Board • Research Ethics Governance Portal', fine))

    def decorate(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(UCC_NAVY)
        canvas.setLineWidth(2.2)
        canvas.rect(7*mm, 7*mm, page_width-14*mm, page_height-14*mm)
        canvas.setStrokeColor(UCC_GOLD)
        canvas.setLineWidth(0.8)
        canvas.rect(9.5*mm, 9.5*mm, page_width-19*mm, page_height-19*mm)
        canvas.restoreState()

    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return stored_name
