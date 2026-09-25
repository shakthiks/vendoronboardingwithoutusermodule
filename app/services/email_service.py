import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from loguru import logger
from app.core.config import settings


def send_email(
    to_email: str,
    subject: str,
    body: str,
    attachments=None
):
    """
    Send email using Office365 SMTP
    """

    # ==========================
    # LOCAL MODE
    # ==========================
    if settings.is_local:
        logger.info(f"[EMAIL LOCAL] To      : {to_email}")
        logger.info(f"[EMAIL LOCAL] Subject : {subject}")
        logger.info(f"[EMAIL LOCAL] Body    : {body}")

        if attachments:
            logger.info(
                f"[EMAIL LOCAL] Attachments: {attachments}"
            )

        return True

    # ==========================
    # PRODUCTION MODE
    # ==========================
    try:
        msg = MIMEMultipart()

        msg["Subject"] = subject
        msg["From"] = settings.MAIL_FROM
        msg["To"] = to_email

        # HTML BODY
        msg.attach(MIMEText(body, "html"))

        # ==========================
        # ATTACHMENTS
        # ==========================
        if attachments:
            for file_path in attachments:

                if not os.path.exists(file_path):
                    logger.warning(
                        f"[EMAIL] File not found: {file_path}"
                    )
                    continue

                with open(file_path, "rb") as file:
                    part = MIMEBase(
                        "application",
                        "octet-stream"
                    )

                    part.set_payload(file.read())

                encoders.encode_base64(part)

                filename = os.path.basename(file_path)

                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"'
                )

                msg.attach(part)

        # ==========================
        # OFFICE365 SMTP
        # ==========================
        server = smtplib.SMTP(
            settings.MAIL_SERVER,
            settings.MAIL_PORT
        )

        server.ehlo()

        # REQUIRED FOR OFFICE365
        server.starttls()

        server.ehlo()

        server.login(
            settings.MAIL_USERNAME,
            settings.MAIL_PASSWORD
        )

        server.sendmail(
            settings.MAIL_FROM,
            to_email,
            msg.as_string()
        )

        server.quit()

        logger.info(
            f"[EMAIL] Successfully sent to {to_email}"
        )

        return True

    except Exception as e:
        logger.error(
            f"[EMAIL] Failed to send to {to_email}: {str(e)}"
        )
        return False

# import smtplib
# from email.mime.text import MIMEText
# from loguru import logger
# from app.core.config import settings
# import smtplib
# import os
# from email.mime.text import MIMEText
# from email.mime.multipart import MIMEMultipart
# from email.mime.base import MIMEBase
# from email import encoders
# from loguru import logger
# from app.core.config import settings


# def send_email(to_email: str, subject: str, body: str, attachments=None):

#     # =========================
#     # LOCAL MODE
#     # =========================
#     if settings.is_local:
#         logger.info(f"[EMAIL LOCAL] To      : {to_email}")
#         logger.info(f"[EMAIL LOCAL] Subject : {subject}")
#         logger.info(f"[EMAIL LOCAL] Body    : {body}")
#         if attachments:
#             logger.info(f"[EMAIL LOCAL] Attachments: {attachments}")
#         return True

#     # =========================
#     # PRODUCTION
#     # =========================
#     try:
#         msg = MIMEMultipart()
#         msg["Subject"] = subject
#         msg["From"] = settings.MAIL_FROM
#         msg["To"] = to_email

#         # HTML body
#         msg.attach(MIMEText(body, "html"))

#         # =========================
#         # ATTACH FILES (NEW)
#         # =========================
#         if attachments:
#             for file_path in attachments:
#                 with open(file_path, "rb") as f:
#                     part = MIMEBase("application", "octet-stream")
#                     part.set_payload(f.read())

#                 encoders.encode_base64(part)

#                 filename = os.path.basename(file_path)
#                 part.add_header(
#                     "Content-Disposition",
#                     f"attachment; filename={filename}"
#                 )

#                 msg.attach(part)

#         # =========================
#         # SEND MAIL
#         # =========================
#         with smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT) as server:
#             server.starttls()
#             server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
#             server.send_message(msg)

#         logger.info(f"[EMAIL] Sent to {to_email}")
#         return True

#     except Exception as e:
#         logger.error(f"[EMAIL] Failed to send to {to_email}: {e}")
#         return False

# # def send_email(to_email: str, subject: str, body: str):
# #     # LOCAL mode — just print to terminal, don't send real email
# #     if settings.is_local:
# #         logger.info(f"[EMAIL LOCAL] ━━━━━━━━━━━━━━━━━━━━━━━━━━")
# #         logger.info(f"[EMAIL LOCAL] To      : {to_email}")
# #         logger.info(f"[EMAIL LOCAL] Subject : {subject}")
# #         logger.info(f"[EMAIL LOCAL] Body    : {body}")
# #         logger.info(f"[EMAIL LOCAL] ━━━━━━━━━━━━━━━━━━━━━━━━━━")
# #         return True

# #     # PRODUCTION — send real email via SMTP
# #     try:
# #         msg = MIMEText(body, "html")
# #         msg["Subject"] = subject
# #         msg["From"]    = settings.MAIL_FROM
# #         msg["To"]      = to_email

# #         with smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT) as server:
# #             server.starttls()
# #             server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
# #             server.send_message(msg)

# #         logger.info(f"[EMAIL] Sent to {to_email}")
# #         return True

# #     except Exception as e:
# #         logger.error(f"[EMAIL] Failed to send to {to_email}: {e}")
# #         return False

# def send_rfq_expiry_reminder(
#     to_email:    str,
#     vendor_name: str,
#     rfq_list:    list      # list of rfq dicts
# ) -> bool:
#     count = len(rfq_list)
#     subject = (
#         f"⚠️ {count} RFQ{'s' if count > 1 else ''} Expiring Tomorrow!"
#     )

#     # Build RFQ rows for email table
#     rfq_rows = ""
#     for i, rfq in enumerate(rfq_list, 1):
#         rfq_rows += f"""
#         <tr style="background:{'#ffffff' if i % 2 == 0 else '#fdf9f9'}">
#             <td style="padding:10px;border-bottom:1px solid #f0e0e0">
#                 <b>{rfq['rfqcaseid']}</b>
#             </td>
#             <td style="padding:10px;border-bottom:1px solid #f0e0e0">
#                 {rfq.get('title', '')}
#             </td>
#             <td style="padding:10px;border-bottom:1px solid #f0e0e0">
#                 <b style="color:#c0392b">{str(rfq['expirydate'])[:10]}</b>
#             </td>
#         </tr>
#         """

#     return send_email(
#         to_email,
#         subject,
#         f"""
#         <div style="font-family:Arial;max-width:650px;margin:auto">

#           <div style="background:#1F3864;padding:24px;text-align:center">
#             <h2 style="color:white;margin:0">Vendor Portal — RFQ Reminder</h2>
#           </div>

#           <div style="padding:32px">
#             <p>Dear <b>{vendor_name}</b>,</p>

#             <p>You have <b style="color:#c0392b">{count} RFQ{'s' if count > 1 else ''}</b>
#                expiring <b>tomorrow</b>. Please submit your bids before they expire.</p>

#             <div style="background:#fde8e8;border-radius:6px;
#                         overflow:hidden;margin:20px 0">
#               <table style="width:100%;border-collapse:collapse">
#                 <thead>
#                   <tr style="background:#c0392b">
#                     <th style="padding:12px;color:white;text-align:left">RFQ Case ID</th>
#                     <th style="padding:12px;color:white;text-align:left">Title</th>
#                     <th style="padding:12px;color:white;text-align:left">Expiry Date</th>
#                   </tr>
#                 </thead>
#                 <tbody>
#                   {rfq_rows}
#                 </tbody>
#               </table>
#             </div>

#             <div style="text-align:center;margin:28px 0">
#               <a href="{settings.FRONTEND_BASE_URL}"
#                  style="background:#1F3864;color:white;padding:14px 32px;
#                         text-decoration:none;border-radius:6px;font-size:16px">
#                 Submit Bids Now
#               </a>
#             </div>

#             <p style="color:#aaa;font-size:12px">
#               This is an automated reminder. Do not reply to this email.
#             </p>
#           </div>

#         </div>
#         """
#     )
