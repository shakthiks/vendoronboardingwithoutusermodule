from html import escape
 
from app.services.email_service import send_email
from app.core.config import settings
 
 
def send_returned_risk_assessment_email(
    to_email: str,
    prospect_id: str,
    company_name: str | None,
    comments: str |None,
) -> bool:
 
    safe_company_name = escape(company_name or "Vendor")
    safe_prospect_id = escape(prospect_id)
    safe_comments = escape(
        comments or "No additional comments were provided."
    ).replace("\n", "<br>")
 
    # Vendor Portal URL
    link = settings.ONBOARDING_FRONTEND_URL
 
    subject = f"Risk Assessment Returned - {prospect_id}"
 
    body = f"""
<!DOCTYPE html>
<html>
<body style="
        margin:0;
        padding:0;
        background:#f4f6f8;
        font-family:Arial, Helvetica, sans-serif;
        color:#222222;
    ">
 
        <table width="100%" cellpadding="0" cellspacing="0"
               style="padding:30px 10px; background:#f4f6f8;">
<tr>
<td align="center">
 
                    <table width="620" cellpadding="0" cellspacing="0"
                           style="
                               width:100%;
                               max-width:620px;
                               background:#ffffff;
                               border:1px solid #e2e6ea;
                               border-radius:8px;
                               overflow:hidden;
                           ">
 
                        <tr>
<td style="
                                background:#17365d;
                                color:#ffffff;
                                padding:22px 30px;
                            ">
<h2 style="margin:0; font-size:21px;">
                                    Risk Assessment Returned
</h2>
</td>
</tr>
 
                        <tr>
<td style="
                                padding:30px;
                                font-size:14px;
                                line-height:1.7;
                            ">
 
                                <p style="margin-top:0;">
                                    Dear {safe_company_name},
</p>
 
                                <p>
                                    Your risk assessment has been returned for correction.
</p>
 
                                <table width="100%"
                                       cellpadding="9"
                                       cellspacing="0"
                                       style="
                                           background:#f8f9fa;
                                           border:1px solid #e2e6ea;
                                           margin:18px 0;
                                       ">
<tr>
<td style="
                                            width:150px;
                                            font-weight:bold;
                                        ">
                                            Prospect ID
</td>
 
                                        <td>
                                            {safe_prospect_id}
</td>
</tr>
 
                                    <tr>
<td style="font-weight:bold;">
                                            Status
</td>
 
                                        <td>
                                            Returned
</td>
</tr>
</table>
 
                                <p style="
                                    font-weight:bold;
                                    margin-bottom:6px;
                                ">
                                    Comments
</p>
 
                                <div style="
                                    border:1px solid #d7dce1;
                                    background:#fafafa;
                                    border-radius:5px;
                                    padding:14px;
                                    min-height:55px;
                                    margin-bottom:20px;
                                ">
                                    {safe_comments}
</div>
 
                                <div style="
                                    background:#fff8e7;
                                    border-left:4px solid #d97706;
                                    padding:14px 16px;
                                    margin:20px 0;
                                ">
                                    Please ensure the evaluation is completed accurately and submitted within the specified timeline. Delayed evaluations may impact supplier performance monitoring and procurement decisions.
</div>
 
                                <p>
                                    Please review the comments, make the necessary corrections, and resubmit your risk assessment for further evaluation.
</p>
 
                                <div style="text-align:center; margin:30px 0;">
<a href="{link}"
                                       style="
                                           background:#17365d;
                                           color:#ffffff;
                                           text-decoration:none;
                                           padding:14px 32px;
                                           border-radius:6px;
                                           display:inline-block;
                                           font-size:15px;
                                           font-weight:bold;
                                       ">
                                        Review &amp; Resubmit Assessment
</a>
</div>
 
                                <p style="font-size:13px; color:#666666;">
                                    If the button above does not work, please copy and paste the following link into your browser:
</p>
 
                                <p style="word-break:break-all;">
<a href="{link}" style="color:#17365d;">
                                        {link}
</a>
</p>
 
                                <p style="margin-bottom:0;">
                                    Regards,<br>
<strong>
                                        HI-Q Vendor Management Team
</strong>
</p>
 
                            </td>
</tr>
 
                        <tr>
<td style="
                                padding:14px 30px;
                                background:#f5f6f7;
                                color:#6b7280;
                                text-align:center;
                                font-size:12px;
                            ">
                                This is an automated email. Please do not reply.
</td>
</tr>
 
                    </table>
 
                </td>
</tr>
</table>
 
    </body>
</html>
    """
 
    return send_email(
        to_email=to_email,
        subject=subject,
        body=body,
    )
# # app/services/riskassessment_email_service.py

# from html import escape

# from app.services.email_service import send_email


# def send_returned_risk_assessment_email(
#     to_email: str,
#     prospect_id: str,
#     company_name: str | None,
#     comments: str | None,
# ) -> bool:

#     safe_company_name = escape(company_name or "Vendor")
#     safe_prospect_id = escape(prospect_id)
#     safe_comments = escape(
#         comments or "No additional comments were provided."
#     ).replace("\n", "<br>")

#     subject = f"Risk Assessment Returned - {prospect_id}"

#     body = f"""
#     <!DOCTYPE html>
#     <html>
#     <body style="
#         margin:0;
#         padding:0;
#         background:#f4f6f8;
#         font-family:Arial, Helvetica, sans-serif;
#         color:#222222;
#     ">

#         <table width="100%" cellpadding="0" cellspacing="0"
#                style="padding:30px 10px; background:#f4f6f8;">
#             <tr>
#                 <td align="center">

#                     <table width="620" cellpadding="0" cellspacing="0"
#                            style="
#                                width:100%;
#                                max-width:620px;
#                                background:#ffffff;
#                                border:1px solid #e2e6ea;
#                                border-radius:8px;
#                                overflow:hidden;
#                            ">

#                         <tr>
#                             <td style="
#                                 background:#17365d;
#                                 color:#ffffff;
#                                 padding:22px 30px;
#                             ">
#                                 <h2 style="margin:0; font-size:21px;">
#                                     Risk Assessment Returned
#                                 </h2>
#                             </td>
#                         </tr>

#                         <tr>
#                             <td style="
#                                 padding:30px;
#                                 font-size:14px;
#                                 line-height:1.7;
#                             ">

#                                 <p style="margin-top:0;">
#                                     Dear {safe_company_name},
#                                 </p>

#                                 <p>
#                                     Your risk assessment has been returned
#                                     for correction.
#                                 </p>

#                                 <table width="100%"
#                                        cellpadding="9"
#                                        cellspacing="0"
#                                        style="
#                                            background:#f8f9fa;
#                                            border:1px solid #e2e6ea;
#                                            margin:18px 0;
#                                        ">
#                                     <tr>
#                                         <td style="
#                                             width:150px;
#                                             font-weight:bold;
#                                         ">
#                                             Prospect ID
#                                         </td>

#                                         <td>
#                                             {safe_prospect_id}
#                                         </td>
#                                     </tr>

#                                     <tr>
#                                         <td style="font-weight:bold;">
#                                             Status
#                                         </td>

#                                         <td>
#                                             Returned
#                                         </td>
#                                     </tr>
#                                 </table>

#                                 <p style="
#                                     font-weight:bold;
#                                     margin-bottom:6px;
#                                 ">
#                                     Comments
#                                 </p>

#                                 <div style="
#                                     border:1px solid #d7dce1;
#                                     background:#fafafa;
#                                     border-radius:5px;
#                                     padding:14px;
#                                     min-height:55px;
#                                     margin-bottom:20px;
#                                 ">
#                                     {safe_comments}
#                                 </div>

#                                 <div style="
#                                     background:#fff8e7;
#                                     border-left:4px solid #d97706;
#                                     padding:14px 16px;
#                                     margin:20px 0;
#                                 ">
#                                     Please ensure the evaluation is completed
#                                     accurately and submitted within the
#                                     specified timeline. Delayed evaluations
#                                     may impact supplier performance monitoring
#                                     and procurement decisions.
#                                 </div>

#                                 <p>
#                                     Please review the comments, update the
#                                     required information and resubmit the
#                                     assessment.
#                                 </p>

#                                 <p style="margin-bottom:0;">
#                                     Regards,<br>
#                                     <strong>
#                                         HI-Q Vendor Management Team
#                                     </strong>
#                                 </p>

#                             </td>
#                         </tr>

#                         <tr>
#                             <td style="
#                                 padding:14px 30px;
#                                 background:#f5f6f7;
#                                 color:#6b7280;
#                                 text-align:center;
#                                 font-size:12px;
#                             ">
#                                 This is an automated email.
#                                 Please do not reply.
#                             </td>
#                         </tr>

#                     </table>

#                 </td>
#             </tr>
#         </table>

#     </body>
#     </html>
#     """

#     return send_email(
#         to_email=to_email,
#         subject=subject,
#         body=body,
#     )