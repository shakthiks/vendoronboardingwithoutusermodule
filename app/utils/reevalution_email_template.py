# app/utils/reevaluation_email_html.py

from html import escape


def build_reevaluation_email_html(
    vendor_name: str,
    content: str,
    action_url: str | None = None,
    action_text: str = "Open Vendor Reevaluation Form",
) -> str:

    vendor_name = escape(
        vendor_name or "Vendor"
    )

    action_button = ""

    if action_url:

        action_button = f"""
        <tr>
            <td align="center"
                style="padding: 10px 30px 30px 30px;">

                <a
                    href="{action_url}"
                    target="_blank"
                    style="
                        background-color:#1F3864;
                        color:#ffffff;
                        text-decoration:none;
                        padding:12px 24px;
                        border-radius:5px;
                        display:inline-block;
                        font-family:Arial, Helvetica, sans-serif;
                        font-size:14px;
                        font-weight:600;
                    "
                >
                    {escape(action_text)}
                </a>

            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1.0">
    </head>

    <body
        style="
            margin:0;
            padding:0;
            background-color:#f5f6f8;
            font-family:Arial, Helvetica, sans-serif;
        "
    >

        <table
            width="100%"
            cellpadding="0"
            cellspacing="0"
            border="0"
            style="background-color:#f5f6f8;"
        >

            <tr>
                <td
                    align="center"
                    style="padding:30px 15px;"
                >

                    <table
                        width="600"
                        cellpadding="0"
                        cellspacing="0"
                        border="0"
                        style="
                            width:100%;
                            max-width:600px;
                            background:#ffffff;
                            border:1px solid #e5e7eb;
                            border-radius:8px;
                        "
                    >

                        <!-- HEADER -->
                        <tr>
                            <td
                                style="
                                    background:#1F3864;
                                    padding:20px 30px;
                                    border-radius:8px 8px 0 0;
                                "
                            >
                                <span
                                    style="
                                        color:#ffffff;
                                        font-size:20px;
                                        font-weight:600;
                                    "
                                >
                                    Hi-Q Electronics
                                </span>

                                <br>

                                <span
                                    style="
                                        color:#dbe4f2;
                                        font-size:13px;
                                    "
                                >
                                    Vendor Reevaluation
                                </span>
                            </td>
                        </tr>


                        <!-- BODY -->
                        <tr>
                            <td
                                style="
                                    padding:30px;
                                    color:#333333;
                                    font-size:15px;
                                    line-height:1.7;
                                "
                            >

                                <p style="margin:0 0 20px 0;">
                                    Dear
                                    <strong>{vendor_name}</strong>,
                                </p>

                                {content}

                            </td>
                        </tr>


                        <!-- BUTTON -->
                        {action_button}


                        <!-- FOOTER -->
                        <tr>
                            <td
                                style="
                                    padding:20px 30px 30px 30px;
                                    color:#555555;
                                    font-size:14px;
                                    line-height:1.6;
                                "
                            >

                                <p style="margin:0 0 20px 0;">
                                    If you have any questions,
                                    please contact us.
                                </p>

                                <p style="margin:0;">
                                    Regards,<br>
                                    <strong>Hi-Q Electronics</strong>
                                </p>

                            </td>
                        </tr>

                    </table>

                </td>
            </tr>

        </table>

    </body>
    </html>
    """